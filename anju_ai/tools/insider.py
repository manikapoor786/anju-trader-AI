#!/usr/bin/env python3
"""
anju_ai.tools.insider — promoter + insider (SAST) disclosures from BSE/NSE.

Audit Finding 3.8 (Phase 2.3): when promoters or designated persons buy
or sell their own company's shares, they file SAST disclosures within
T+2. These are the LEADING-est leading indicators in equities — promoter
buys ahead of good news, sells ahead of bad. Public, free, structured.

This module fetches the NSE corporate-announcements insider trading feed
(SAST regulation under SEBI). Phase 2.4 backtests their predictive value
before scoring weight is activated.

Endpoint (free, NSE session cookie required):
  https://www.nseindia.com/api/corporates-pit  (insider trading)

Schema (FY2024 sample):
  {
    "symbol": "RELIANCE",
    "company": "Reliance Industries Limited",
    "personCategory": "Promoter",  // Promoter | Designated Person | KMP
    "acquisitionMode": "Market Purchase",
    "securitiesAcquired": 50000,
    "securitiesValue": 71375000,
    "transactionType": "Buy",
    "date": "13-May-2026",
    ...
  }
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import requests
from pydantic import BaseModel, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as _Retry


# ── HTTP session ──────────────────────────────────────────────────────────────

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
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading",
    "Connection": "keep-alive",
})

_NSE_SESSION_WARMED = False


def _warm_nse_session() -> None:
    global _NSE_SESSION_WARMED
    if _NSE_SESSION_WARMED:
        return
    try:
        _http.get("https://www.nseindia.com/", timeout=10)
        _http.get(
            "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading",
            timeout=10)
        _NSE_SESSION_WARMED = True
    except Exception:
        pass


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class InsiderTransaction(BaseModel):
    """One SAST disclosure — normalised across NSE/BSE schemas."""
    date:               str         # 'YYYY-MM-DD'
    symbol:             str
    company:            str
    person_category:    str         # Promoter | Designated Person | KMP | Director
    side:               Literal["BUY", "SELL"]
    mode:               str         # Market Purchase | Off Market | ESOP | etc.
    qty:                int
    value_inr:          float
    person_name:        str = ""


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_insider(payload: Any) -> list[InsiderTransaction]:
    """Parse NSE insider-trading API payload into normalised transactions."""
    if isinstance(payload, dict):
        rows = payload.get("data", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        return []

    out: list[InsiderTransaction] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            sym = str(r.get("symbol", "")).strip()
            company = str(r.get("company", "") or r.get("companyName", "")).strip()
            pcat = str(r.get("personCategory", "") or r.get("category", "")).strip()
            tx_raw = str(r.get("transactionType", "") or r.get("type", "")).strip().upper()
            mode = str(r.get("acquisitionMode", "") or r.get("mode", "")).strip()
            qty = int(float(str(r.get("securitiesAcquired", "0") or
                                 r.get("noOfSecurities", "0") or
                                 r.get("quantity", "0")
                                ).replace(",", "") or 0))
            val = float(str(r.get("securitiesValue", "0") or
                            r.get("value", "0") or 0
                           ).replace(",", "") or 0)
            person = str(r.get("personName", "") or r.get("acquirer", "")).strip()

            date_raw = str(r.get("date", "") or r.get("acqFromDt", "") or
                           r.get("acqDt", "")).strip()
            try:
                d = datetime.strptime(date_raw, "%d-%b-%Y").strftime("%Y-%m-%d")
            except ValueError:
                try:
                    d = datetime.strptime(date_raw, "%d-%m-%Y").strftime("%Y-%m-%d")
                except ValueError:
                    d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        if not sym or qty <= 0:
            continue
        side: Literal["BUY", "SELL"] = "BUY" if tx_raw.startswith("B") or tx_raw == "ACQUISITION" else "SELL"

        out.append(InsiderTransaction(
            date=d, symbol=sym, company=company,
            person_category=pcat or "Unknown",
            side=side, mode=mode or "Unknown",
            qty=qty, value_inr=round(val, 2),
            person_name=person,
        ))
    return out


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_insider(from_date: str | None = None,
                  to_date: str | None = None,
                  http_get=None) -> list[InsiderTransaction]:
    """Fetch insider/SAST transactions for the given date range.

    Args:
        from_date: 'YYYY-MM-DD' (default: 7 days ago)
        to_date:   'YYYY-MM-DD' (default: today)
        http_get:  testing override
    Returns list[InsiderTransaction]; [] on fetch failure.
    """
    if http_get is None:
        _warm_nse_session()
        time.sleep(0.3)
        http_get = lambda u, **kw: _http.get(u, timeout=kw.get("timeout", 15))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from_d = from_date or (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    to_d = to_date or today

    endpoint = (
        f"https://www.nseindia.com/api/corporates-pit"
        f"?index=equities"
        f"&from_date={_to_nse_date(from_d)}"
        f"&to_date={_to_nse_date(to_d)}"
    )
    try:
        r = http_get(endpoint, timeout=15)
        if r.status_code != 200:
            return []
        return _parse_insider(r.json())
    except Exception:
        return []


def _to_nse_date(d: str) -> str:
    """NSE accepts both 'DD-MM-YYYY' and 'YYYY-MM-DD' on this endpoint —
    we send DD-MM-YYYY for consistency with other endpoints."""
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return d


def save_insider(con, transactions: list[InsiderTransaction]) -> dict[str, int]:
    """Append to insider_json on flows_snapshots, grouped by date.
    Returns {snapshot_date: snapshot_id}."""
    if not transactions:
        return {}

    by_date: dict[str, list[InsiderTransaction]] = {}
    for t in transactions:
        by_date.setdefault(t.date, []).append(t)

    out: dict[str, int] = {}
    for d, txs in by_date.items():
        con.execute("""
            INSERT INTO flows_snapshots (snapshot_date, insider_json)
            VALUES (?, '[]')
            ON CONFLICT(snapshot_date) DO NOTHING
        """, (d,))

        row = con.execute(
            "SELECT id, insider_json FROM flows_snapshots WHERE snapshot_date=?",
            (d,),
        ).fetchone()
        if not row:
            continue
        existing = json.loads(row[1] or "[]")
        merged = _merge_insider(existing, [t.model_dump() for t in txs])
        con.execute(
            "UPDATE flows_snapshots SET insider_json=? WHERE id=?",
            (json.dumps(merged), row[0]),
        )
        out[d] = row[0]
    return out


def _merge_insider(existing: list[dict], new: list[dict]) -> list[dict]:
    """Dedupe by (symbol, date, side, qty, value_inr, person_name)."""
    seen = {(e.get("symbol", ""), e.get("date", ""), e.get("side", ""),
             e.get("qty", 0), e.get("value_inr", 0.0),
             e.get("person_name", ""))
            for e in existing if isinstance(e, dict)}
    out = list(existing)
    for d in new:
        key = (d["symbol"], d["date"], d["side"], d["qty"],
               d["value_inr"], d.get("person_name", ""))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def get_insider_for_symbol(con, symbol: str, days_back: int = 90) -> list[dict]:
    """Return all insider transactions for `symbol` in the last N days.
    Used by Phase 2.4 scoring augmentation after validation."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    rows = con.execute(
        """SELECT snapshot_date, insider_json
             FROM flows_snapshots
            WHERE snapshot_date >= ?""", (cutoff,)
    ).fetchall()
    sym_up = symbol.upper().replace(".NS", "")
    out: list[dict] = []
    for r in rows:
        for d in json.loads(r[1] or "[]"):
            if d.get("symbol", "").upper() == sym_up:
                d["snapshot_date"] = r[0]
                out.append(d)
    return out


def insider_signal_for_symbol(con, symbol: str, days_back: int = 30) -> dict:
    """Aggregate insider activity for a single symbol into a one-line signal:
        net_qty, net_value_inr, n_buys, n_sells, promoter_net_value
    Phase 2.4 scoring uses this as a feature once validated."""
    txs = get_insider_for_symbol(con, symbol, days_back=days_back)
    if not txs:
        return {"n": 0, "net_qty": 0, "net_value_inr": 0.0,
                "n_buys": 0, "n_sells": 0, "promoter_net_value_inr": 0.0}
    net_qty = sum(t["qty"] if t["side"] == "BUY" else -t["qty"] for t in txs)
    net_value = sum(t["value_inr"] if t["side"] == "BUY" else -t["value_inr"]
                    for t in txs)
    n_buys  = sum(1 for t in txs if t["side"] == "BUY")
    n_sells = sum(1 for t in txs if t["side"] == "SELL")
    promoter_net = sum(
        t["value_inr"] if t["side"] == "BUY" else -t["value_inr"]
        for t in txs if "promoter" in t.get("person_category", "").lower()
    )
    return {
        "n": len(txs),
        "net_qty": net_qty,
        "net_value_inr": round(net_value, 2),
        "n_buys": n_buys, "n_sells": n_sells,
        "promoter_net_value_inr": round(promoter_net, 2),
    }
