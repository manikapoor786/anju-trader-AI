#!/usr/bin/env python3
"""
anju_ai.tools.deals — bulk and block deals from NSE.

Audit Finding 3.8 (Phase 2.2): institutional position changes >5L shares
or >₹10cr value get reported by NSE on T+0 (block) and T+1 (bulk). These
are leading indicators:

  - Mutual fund buys ₹50cr of XYZ → block deal published 5pm
  - Smallcap stock with persistent FII bulk-deal sells → distribution

This module fetches both feeds, parses, and persists to flows_snapshots
(`bulk_deals_json` and `block_deals_json` columns). Phase 2.4 backtests
their predictive value before we score-weight them.

Endpoints (free, no auth beyond NSE session cookie):
  Bulk:  https://www.nseindia.com/api/historical/cm/bulk
  Block: https://www.nseindia.com/api/historical/cm/block
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import requests
from pydantic import BaseModel, ConfigDict, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as _Retry


# ── HTTP session (mirrors flows.py + data_layer.py) ───────────────────────────

_retry = _Retry(total=3, backoff_factor=1.0,
                status_forcelist=[429, 500, 502, 503, 504])
_http = requests.Session()
_http.mount("https://", HTTPAdapter(max_retries=_retry))
_http.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/large-deals",
    "Connection": "keep-alive",
})

_NSE_SESSION_WARMED = False


def _warm_nse_session() -> None:
    global _NSE_SESSION_WARMED
    if _NSE_SESSION_WARMED:
        return
    try:
        _http.get("https://www.nseindia.com/", timeout=10)
        _http.get("https://www.nseindia.com/market-data/large-deals", timeout=10)
        _NSE_SESSION_WARMED = True
    except Exception:
        pass


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class Deal(BaseModel):
    """One bulk or block deal — normalised across both endpoints."""
    deal_type:     Literal["bulk", "block"]
    deal_date:     str             # 'YYYY-MM-DD'
    symbol:        str
    client_name:   str             # buyer or seller
    side:          Literal["BUY", "SELL"]
    quantity:      int
    avg_price:     float
    value_inr:     float           # quantity * avg_price


# ── Parsing ───────────────────────────────────────────────────────────────────

# NSE bulk schema:
#   {"BD_DT_DATE": "13-May-2026", "BD_SYMBOL": "ABC", "BD_CLIENT_NAME": "...",
#    "BD_BUY_SELL": "BUY", "BD_QTY_TRD": "500000",
#    "BD_TP_WATP": "245.50", "BD_REMARKS": "..."}
#
# NSE block schema (newer): different keys but similar structure
#   {"BD_DT_DATE": ..., "BD_SYMBOL": ..., "BD_CLIENT_NAME": ...,
#    "BD_BUY_SELL": "BUY"/"SELL", "BD_QTY_TRD": ..., "BD_TP_WATP": ...}

def _parse_deals(payload: Any, deal_type: str) -> list[Deal]:
    """Parse NSE deals API response into normalised Deal objects."""
    if isinstance(payload, dict):
        rows = payload.get("data", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        return []

    out: list[Deal] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            sym  = str(r.get("BD_SYMBOL", "")).strip()
            name = str(r.get("BD_CLIENT_NAME", "")).strip()
            side_raw = str(r.get("BD_BUY_SELL", "")).strip().upper()
            qty = int(float(str(r.get("BD_QTY_TRD", "0")).replace(",", "") or 0))
            px  = float(str(r.get("BD_TP_WATP", "0")).replace(",", "") or 0)
            d_raw = str(r.get("BD_DT_DATE", "")).strip()
            try:
                d = datetime.strptime(d_raw, "%d-%b-%Y").strftime("%Y-%m-%d")
            except ValueError:
                d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        if not sym or qty <= 0 or px <= 0:
            continue
        side = "BUY" if side_raw.startswith("B") else "SELL"

        out.append(Deal(
            deal_type=deal_type, deal_date=d, symbol=sym,
            client_name=name, side=side, quantity=qty,
            avg_price=round(px, 2), value_inr=round(qty * px, 2),
        ))
    return out


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_deals(deal_type: Literal["bulk", "block"],
                from_date: str | None = None,
                to_date: str | None = None,
                http_get=None) -> list[Deal]:
    """Fetch bulk or block deals for the given date range.

    Args:
        deal_type:  "bulk" or "block"
        from_date:  'YYYY-MM-DD' (default: today)
        to_date:    'YYYY-MM-DD' (default: today)
        http_get:   testing override

    Returns list[Deal] (empty list on fetch failure).
    """
    if http_get is None:
        _warm_nse_session()
        time.sleep(0.3)
        http_get = lambda u, **kw: _http.get(u, timeout=kw.get("timeout", 15))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from_d = from_date or today
    to_d   = to_date   or today

    endpoint = (f"https://www.nseindia.com/api/historical/cm/{deal_type}"
                f"?from={_to_nse_date(from_d)}&to={_to_nse_date(to_d)}")
    try:
        r = http_get(endpoint, timeout=15)
        if r.status_code != 200:
            return []
        return _parse_deals(r.json(), deal_type)
    except Exception:
        return []


def _to_nse_date(d: str) -> str:
    """NSE date param format: 'DD-MM-YYYY'."""
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return d


def save_deals(con, deals: list[Deal], snapshot_date: str | None = None) -> int:
    """Append today's deals to the JSON column on flows_snapshots.
    Returns the flows_snapshots row id.

    Schema: bulk_deals_json and block_deals_json each store a JSON array.
    This function MERGES — re-fetches the same day add new deals without
    overwriting previous ones (assuming the dedupe field is symbol+side+qty).
    """
    if not deals:
        return 0

    d = snapshot_date or deals[0].deal_date
    bulk  = [x.model_dump() for x in deals if x.deal_type == "bulk"]
    block = [x.model_dump() for x in deals if x.deal_type == "block"]

    # Ensure row exists for that snapshot_date
    con.execute("""
        INSERT INTO flows_snapshots (snapshot_date, bulk_deals_json, block_deals_json)
        VALUES (?, '[]', '[]')
        ON CONFLICT(snapshot_date) DO NOTHING
    """, (d,))

    row = con.execute(
        "SELECT id, bulk_deals_json, block_deals_json FROM flows_snapshots "
        "WHERE snapshot_date=?", (d,),
    ).fetchone()
    if not row:
        return 0

    rid = row[0]
    cur_bulk  = json.loads(row[1] or "[]") if row[1] else []
    cur_block = json.loads(row[2] or "[]") if row[2] else []

    merged_bulk  = _merge_deals(cur_bulk, bulk)
    merged_block = _merge_deals(cur_block, block)

    con.execute("""
        UPDATE flows_snapshots
           SET bulk_deals_json = ?, block_deals_json = ?
         WHERE id = ?
    """, (json.dumps(merged_bulk), json.dumps(merged_block), rid))
    return rid


def _merge_deals(existing: list[dict], new: list[dict]) -> list[dict]:
    """Dedupe by symbol + side + quantity + avg_price."""
    seen = {(e["symbol"], e["side"], e["quantity"], e["avg_price"])
            for e in existing if isinstance(e, dict)}
    out = list(existing)
    for d in new:
        key = (d["symbol"], d["side"], d["quantity"], d["avg_price"])
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def get_deals_for_symbol(con, symbol: str, days_back: int = 30) -> list[dict]:
    """Return all bulk+block deals for `symbol` in the last N days.
    Used by Phase 2.4 scoring augmentation (once weights are validated)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    rows = con.execute(
        """SELECT snapshot_date, bulk_deals_json, block_deals_json
             FROM flows_snapshots
            WHERE snapshot_date >= ?""", (cutoff,)
    ).fetchall()

    out: list[dict] = []
    sym_up = symbol.upper().replace(".NS", "")
    for r in rows:
        for col in (r[1], r[2]):
            for d in json.loads(col or "[]"):
                if d.get("symbol", "").upper() == sym_up:
                    d["snapshot_date"] = r[0]
                    out.append(d)
    return out
