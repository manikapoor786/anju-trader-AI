#!/usr/bin/env python3
"""
anju_ai.tools.flows — institutional flow ingestion.

Audit Finding 3.8: anju-trader had no flows tracking. This module fetches
NSE's daily institutional activity data and stores it in
memory.flows_snapshots:

  Phase 2.1  FII/DII cash + F&O activity (this file)
  Phase 2.2  Bulk + block deals          (separate add)
  Phase 2.3  Promoter / SAST disclosures (separate add)

Why it matters: persistent FII selling + DII buying flags accumulation
before retail notices. FIIs alone can move midcaps ±2-5% over a week.
The signals aren't auto-wired into scoring — they're collected first so
Phase 2.4 can quantify their predictive value via backtest before we
turn the weight on.

Data sources (all free, no auth):
  https://www.nseindia.com/api/fiidiiTradeReact  — daily cash provisional
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import requests
from pydantic import BaseModel, ConfigDict, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as _Retry


# ── HTTP session (same warm-up pattern as data_layer.py) ──────────────────────

_retry = _Retry(total=3, backoff_factor=1.0,
                status_forcelist=[429, 500, 502, 503, 504])
_http = requests.Session()
_http.mount("https://", HTTPAdapter(max_retries=_retry))
_http.mount("http://",  HTTPAdapter(max_retries=_retry))
_http.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
    "Connection": "keep-alive",
})

_NSE_SESSION_WARMED = False


def _warm_nse_session() -> None:
    global _NSE_SESSION_WARMED
    if _NSE_SESSION_WARMED:
        return
    try:
        _http.get("https://www.nseindia.com/", timeout=10)
        _http.get("https://www.nseindia.com/reports/fii-dii", timeout=10)
        _NSE_SESSION_WARMED = True
    except Exception:
        pass


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class FIIDIIRow(BaseModel):
    """One row of the FII/DII table, normalised to ₹ crore."""
    category:     Literal["FII/FPI", "DII"]
    date:         str             # 'YYYY-MM-DD'
    buy_value_cr: float           # ₹ crore
    sell_value_cr: float
    net_value_cr: float           # buy - sell (positive = net buying)


class FIIDIISnapshot(BaseModel):
    """One day's FII/DII data — cash market only (FY2024 schema)."""
    snapshot_date:    str
    fii_buy_cr:       float
    fii_sell_cr:      float
    fii_net_cr:       float
    dii_buy_cr:       float
    dii_sell_cr:      float
    dii_net_cr:       float
    raw_response:     Any = Field(default=None)   # NSE returns list-of-dicts
    fetched_at:       str

    def signal_strength(self) -> str:
        """Human-readable summary of the day's flow."""
        fii_dir = "Buying" if self.fii_net_cr > 0 else "Selling"
        dii_dir = "Buying" if self.dii_net_cr > 0 else "Selling"
        return (f"FII {fii_dir} ₹{abs(self.fii_net_cr):.0f}cr · "
                f"DII {dii_dir} ₹{abs(self.dii_net_cr):.0f}cr")


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_nse_response(payload: list | dict) -> tuple[FIIDIIRow, FIIDIIRow] | None:
    """NSE returns a list of two dicts (one for FII/FPI, one for DII).

    Schema (as of 2024):
        [
          {"category": "FII/FPI **", "date": "13-May-2026",
           "buyValue": "12345.67", "sellValue": "11000.00",
           "netValue": "1345.67"},
          {"category": "DII **", ...}
        ]

    The values are STRINGS (despite being numbers). Returns
    (fii_row, dii_row) or None if shape is unexpected.
    """
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data", [])
    else:
        return None
    if not isinstance(rows, list) or len(rows) < 2:
        return None

    fii_row = None
    dii_row = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        cat = str(r.get("category", "")).strip()
        # Normalise category — NSE adds ** suffix sometimes
        cat_norm = (cat.replace("**", "").replace("*", "").strip()
                       .replace("FII / FPI", "FII/FPI").replace("FII", "FII/FPI"))
        if cat_norm.startswith("FII") or "FPI" in cat_norm:
            cat_norm = "FII/FPI"
        elif cat_norm.startswith("DII"):
            cat_norm = "DII"
        else:
            continue

        try:
            buy  = float(str(r.get("buyValue",  "0")).replace(",", "") or 0)
            sell = float(str(r.get("sellValue", "0")).replace(",", "") or 0)
            net  = float(str(r.get("netValue",  "0")).replace(",", "") or 0)
            # Re-derive net to defend against parsing inconsistencies
            net_calc = buy - sell
            net = net if abs(net) > 0.01 else net_calc
        except (ValueError, TypeError):
            continue

        # Parse date — NSE format "13-May-2026"
        date_raw = str(r.get("date", "")).strip()
        try:
            d = datetime.strptime(date_raw, "%d-%b-%Y").strftime("%Y-%m-%d")
        except ValueError:
            d = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        row = FIIDIIRow(category=cat_norm, date=d,
                        buy_value_cr=buy, sell_value_cr=sell, net_value_cr=net)
        if cat_norm == "FII/FPI":
            fii_row = row
        elif cat_norm == "DII":
            dii_row = row

    if fii_row and dii_row:
        return fii_row, dii_row
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_fii_dii(http_get=None) -> FIIDIISnapshot | None:
    """Fetch today's FII/DII data from NSE.

    Args:
        http_get: callable(url, timeout) -> Response (for testing).
            Defaults to the warmed _http session.

    Returns FIIDIISnapshot or None on parse/fetch failure.
    """
    if http_get is None:
        _warm_nse_session()
        time.sleep(0.5)
        http_get = lambda u, **kw: _http.get(u, timeout=kw.get("timeout", 15))

    try:
        r = http_get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=15)
        if r.status_code != 200:
            return None
        payload = r.json()
    except Exception:
        return None

    parsed = _parse_nse_response(payload)
    if not parsed:
        return None
    fii, dii = parsed

    return FIIDIISnapshot(
        snapshot_date=fii.date,
        fii_buy_cr=fii.buy_value_cr,
        fii_sell_cr=fii.sell_value_cr,
        fii_net_cr=fii.net_value_cr,
        dii_buy_cr=dii.buy_value_cr,
        dii_sell_cr=dii.sell_value_cr,
        dii_net_cr=dii.net_value_cr,
        raw_response=payload if isinstance(payload, (list, dict)) else {},
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def save_flows_snapshot(con, snapshot: FIIDIISnapshot) -> int:
    """Insert (or upsert) a flows snapshot into memory.db. Returns row id.

    Uses ON CONFLICT DO UPDATE to handle re-fetches the same day (e.g.
    NSE updates the row from "provisional" to "final" after EOD)."""
    con.execute("""
        INSERT INTO flows_snapshots
            (snapshot_date, fii_cash_cr, dii_cash_cr,
             fii_futures_cr, fii_options_cr,
             bulk_deals_json, block_deals_json,
             promoter_json, insider_json)
        VALUES (?, ?, ?, NULL, NULL, '[]', '[]', '[]', '[]')
        ON CONFLICT(snapshot_date) DO UPDATE SET
            fii_cash_cr = excluded.fii_cash_cr,
            dii_cash_cr = excluded.dii_cash_cr
    """, (snapshot.snapshot_date, snapshot.fii_net_cr, snapshot.dii_net_cr))

    row = con.execute(
        "SELECT id FROM flows_snapshots WHERE snapshot_date=?",
        (snapshot.snapshot_date,),
    ).fetchone()
    return row[0] if row else 0


def latest_flows(con, days_back: int = 5) -> list[dict]:
    """Read the last N days of flows snapshots. Used by morning digest
    + (Phase 2.4 onward) by scoring as a contextual feature."""
    rows = con.execute(
        """SELECT snapshot_date, fii_cash_cr, dii_cash_cr, created_at
             FROM flows_snapshots
            ORDER BY snapshot_date DESC
            LIMIT ?""", (days_back,)
    ).fetchall()
    return [dict(zip(["date", "fii_net_cr", "dii_net_cr", "created_at"], r))
            for r in rows]
