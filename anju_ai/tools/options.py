#!/usr/bin/env python3
"""
anju_ai.tools.options — F&O option-chain + IV data from NSE.

Audit Finding 3.10 (Phase 2.7): when a stock signals with high
conviction AND its options are mispriced cheap (low IV percentile),
buying an ATM call gives 3-5x cleaner leverage than buying cash equity.

This module fetches NSE's option chain for an F&O-eligible symbol and
computes:
  - At-the-money strike + premium
  - Implied volatility percentile (vs trailing 1y of own IV)
  - Open Interest distribution (max-pain proxy)
  - Bid-ask liquidity check (refuse illiquid chains)

Mode is PASSIVE in Phase 2.7. The actual leverage decision
(cash vs ATM call) gates on backtest validation in later phases.

Endpoint:
  https://www.nseindia.com/api/option-chain-equities?symbol=RELIANCE
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from pydantic import BaseModel, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as _Retry


# ── HTTP session ──────────────────────────────────────────────────────────────

_retry = _Retry(total=2, backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504])
_http = requests.Session()
_http.mount("https://", HTTPAdapter(max_retries=_retry))
_http.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
    "Connection": "keep-alive",
})

_NSE_SESSION_WARMED = False


def _warm_nse_session() -> None:
    global _NSE_SESSION_WARMED
    if _NSE_SESSION_WARMED:
        return
    try:
        _http.get("https://www.nseindia.com/", timeout=10)
        _http.get("https://www.nseindia.com/option-chain", timeout=10)
        _NSE_SESSION_WARMED = True
    except Exception:
        pass


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class OptionLeg(BaseModel):
    """One side (call or put) at one strike for one expiry."""
    strike:       float
    expiry:       str            # 'YYYY-MM-DD'
    last_price:   float
    bid:          float = 0.0
    ask:          float = 0.0
    iv:           float = 0.0    # implied volatility %
    open_interest: int = 0
    change_in_oi: int = 0
    volume:       int = 0


class OptionChain(BaseModel):
    """Compact snapshot of an underlying's option chain."""
    symbol:          str
    underlying_price: float
    fetched_at:      str
    nearest_expiry:  str = ""
    atm_strike:      float = 0.0
    atm_call:        OptionLeg | None = None
    atm_put:         OptionLeg | None = None
    avg_iv:          float = 0.0     # mean IV across nearest-expiry ATM band
    pcr_oi:          float = 0.0     # put-call OI ratio (>1 = bearish)
    max_pain:        float = 0.0     # strike where total OI×|S-K| minimised
    is_liquid:       bool = False    # ATM bid-ask spread < 5% of premium


class IVHistory(BaseModel):
    """Stores trailing ATM IV per symbol per day. Phase 2.7 stub —
    populated by daily ingest, queried by ivp() to compute IV percentile."""
    symbol:        str
    date:          str            # 'YYYY-MM-DD'
    atm_iv:        float


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_chain(payload: dict, symbol: str) -> OptionChain | None:
    """Parse NSE option-chain JSON. Returns None on shape mismatch."""
    try:
        records = payload.get("records", {})
        filtered = payload.get("filtered", records)
        data = filtered.get("data", records.get("data", []))
        if not isinstance(data, list) or not data:
            return None
        underlying = float(records.get("underlyingValue", 0) or 0)
        expiry_dates = records.get("expiryDates", [])
        if not expiry_dates:
            return None
        # Use nearest expiry
        nearest = expiry_dates[0]
        nearest_iso = _expiry_to_iso(nearest)

        # Find ATM strike (closest to underlying)
        strikes = sorted({float(d["strikePrice"]) for d in data
                          if d.get("strikePrice") is not None})
        if not strikes or underlying <= 0:
            return None
        atm = min(strikes, key=lambda k: abs(k - underlying))

        call_leg = None
        put_leg = None
        avg_iv_band = []
        total_call_oi = 0
        total_put_oi = 0

        for d in data:
            try:
                strike = float(d.get("strikePrice", 0))
                if strike <= 0:
                    continue
                expiry_str = d.get("expiryDate", nearest)
                if expiry_str != nearest:
                    continue
                ce = d.get("CE", {})
                pe = d.get("PE", {})
                if isinstance(ce, dict):
                    total_call_oi += int(ce.get("openInterest", 0) or 0)
                if isinstance(pe, dict):
                    total_put_oi += int(pe.get("openInterest", 0) or 0)
                # ATM band: strikes within ±5% of ATM
                if abs(strike - atm) / atm < 0.05:
                    if isinstance(ce, dict) and ce.get("impliedVolatility"):
                        avg_iv_band.append(float(ce["impliedVolatility"]))
                    if isinstance(pe, dict) and pe.get("impliedVolatility"):
                        avg_iv_band.append(float(pe["impliedVolatility"]))
                if strike == atm:
                    if isinstance(ce, dict) and ce.get("lastPrice"):
                        call_leg = OptionLeg(
                            strike=strike, expiry=nearest_iso,
                            last_price=float(ce.get("lastPrice", 0)),
                            bid=float(ce.get("bidprice", 0) or 0),
                            ask=float(ce.get("askPrice", 0) or 0),
                            iv=float(ce.get("impliedVolatility", 0) or 0),
                            open_interest=int(ce.get("openInterest", 0) or 0),
                            change_in_oi=int(ce.get("changeinOpenInterest", 0) or 0),
                            volume=int(ce.get("totalTradedVolume", 0) or 0),
                        )
                    if isinstance(pe, dict) and pe.get("lastPrice"):
                        put_leg = OptionLeg(
                            strike=strike, expiry=nearest_iso,
                            last_price=float(pe.get("lastPrice", 0)),
                            bid=float(pe.get("bidprice", 0) or 0),
                            ask=float(pe.get("askPrice", 0) or 0),
                            iv=float(pe.get("impliedVolatility", 0) or 0),
                            open_interest=int(pe.get("openInterest", 0) or 0),
                            change_in_oi=int(pe.get("changeinOpenInterest", 0) or 0),
                            volume=int(pe.get("totalTradedVolume", 0) or 0),
                        )
            except (ValueError, TypeError):
                continue

        avg_iv = round(sum(avg_iv_band) / len(avg_iv_band), 2) if avg_iv_band else 0.0
        pcr = round(total_put_oi / max(total_call_oi, 1), 3) if total_call_oi else 0.0
        max_pain = _compute_max_pain(data, nearest, strikes)
        is_liquid = _is_liquid(call_leg) and _is_liquid(put_leg)

        return OptionChain(
            symbol=symbol.upper().replace(".NS", ""),
            underlying_price=round(underlying, 2),
            fetched_at=datetime.now(timezone.utc).isoformat(),
            nearest_expiry=nearest_iso,
            atm_strike=atm,
            atm_call=call_leg,
            atm_put=put_leg,
            avg_iv=avg_iv,
            pcr_oi=pcr,
            max_pain=max_pain,
            is_liquid=is_liquid,
        )
    except Exception:
        return None


def _expiry_to_iso(s: str) -> str:
    """NSE expiry string is 'DD-Mon-YYYY' — convert to ISO."""
    try:
        return datetime.strptime(s, "%d-%b-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return s


def _compute_max_pain(data: list, expiry: str, strikes: list[float]) -> float:
    """Strike where total OI × |spot - strike| is minimised.
    A crude max-pain estimate that ignores PE/CE asymmetry, but good
    enough as a feature."""
    if not strikes:
        return 0.0
    pain: dict[float, float] = {}
    for spot in strikes:
        total = 0.0
        for d in data:
            try:
                if d.get("expiryDate") != expiry:
                    continue
                k = float(d.get("strikePrice", 0))
                ce = d.get("CE", {}) or {}
                pe = d.get("PE", {}) or {}
                ce_oi = int(ce.get("openInterest", 0) or 0)
                pe_oi = int(pe.get("openInterest", 0) or 0)
                # Call writers lose money when spot > strike
                if spot > k:
                    total += (spot - k) * ce_oi
                # Put writers lose money when spot < strike
                if spot < k:
                    total += (k - spot) * pe_oi
            except (ValueError, TypeError):
                continue
        pain[spot] = total
    return min(pain, key=pain.get) if pain else 0.0


def _is_liquid(leg: OptionLeg | None) -> bool:
    """Bid-ask spread < 5% of premium AND last_price > 0.05 (i.e. not a stub)."""
    if leg is None or leg.last_price <= 0.05:
        return False
    if leg.bid <= 0 or leg.ask <= 0:
        return True   # missing quotes — assume liquid if last_price is real
    spread = abs(leg.ask - leg.bid)
    return (spread / leg.last_price) < 0.05


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_option_chain(symbol: str, http_get=None) -> OptionChain | None:
    """Fetch the option chain for an F&O-eligible symbol.

    Returns None if:
      - symbol is not F&O eligible (NSE returns no data)
      - parse failure / network error

    Caller must check is_liquid before recommending leverage.
    """
    if http_get is None:
        _warm_nse_session()
        time.sleep(0.3)
        http_get = lambda u, **kw: _http.get(u, timeout=kw.get("timeout", 15))

    sym = symbol.upper().replace(".NS", "").replace(".BSE", "")
    url = f"https://www.nseindia.com/api/option-chain-equities?symbol={sym}"
    try:
        r = http_get(url, timeout=15)
        if r.status_code != 200:
            return None
        return _parse_chain(r.json(), sym)
    except Exception:
        return None


def iv_percentile(con, symbol: str, current_iv: float,
                  lookback_days: int = 252) -> float | None:
    """Compute IV percentile (where today's IV sits in trailing 1y distribution).

    Returns percentile in [0, 100], or None if insufficient history.
    Buying options is favourable when IVP < 30 (relatively cheap vol).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    rows = con.execute("""
        SELECT atm_iv FROM iv_history
         WHERE symbol = ? AND date >= ?
         ORDER BY date
    """, (symbol.upper().replace(".NS", ""), cutoff)).fetchall()
    if len(rows) < 20:
        return None
    ivs = sorted(float(r[0]) for r in rows)
    below = sum(1 for v in ivs if v < current_iv)
    return round(below / len(ivs) * 100, 1)


def save_iv_observation(con, symbol: str, date: str, atm_iv: float) -> None:
    """Append today's ATM IV to iv_history for percentile computation."""
    con.execute("""
        INSERT OR REPLACE INTO iv_history (symbol, date, atm_iv)
        VALUES (?, ?, ?)
    """, (symbol.upper().replace(".NS", ""), date, atm_iv))


# ── Recommendation (PASSIVE — gated behind config flag) ──────────────────────

class LeverageRecommendation(BaseModel):
    """Output of evaluate_leverage. Mode 'NONE' until backtest validates."""
    mode:            str             # 'CASH' | 'ATM_CALL' | 'NONE'
    rationale:       str
    iv_percentile:   float | None
    is_liquid:       bool = False
    suggested_lots:  int = 0
    suggested_strike: float = 0.0
    suggested_expiry: str = ""


def evaluate_leverage(con,
                      symbol: str,
                      rule_score: float,
                      chain: OptionChain | None,
                      fno_enabled: bool = False,
                      min_score_for_options: float = 25.0,
                      max_ivp_for_options: float = 50.0) -> LeverageRecommendation:
    """Recommend cash vs ATM-call leverage.

    Phase 2.7 default fno_enabled=False → always returns CASH.
    Set fno_enabled=True in config/runtime.yaml only after Phase 1.5
    + Phase 2.4 backtest validate the scoring AND a non-zero edge from
    the F&O leverage.
    """
    if not fno_enabled:
        return LeverageRecommendation(
            mode="CASH", rationale="F&O leverage disabled until backtest validates",
            iv_percentile=None,
        )
    if chain is None or not chain.is_liquid or chain.atm_call is None:
        return LeverageRecommendation(
            mode="CASH", rationale="No liquid option chain available",
            iv_percentile=None, is_liquid=False,
        )
    if rule_score < min_score_for_options:
        return LeverageRecommendation(
            mode="CASH",
            rationale=f"Score {rule_score:.1f} below options threshold {min_score_for_options}",
            iv_percentile=None, is_liquid=True,
        )
    ivp = iv_percentile(con, symbol, chain.atm_call.iv)
    if ivp is None:
        return LeverageRecommendation(
            mode="CASH", rationale="Insufficient IV history for percentile",
            iv_percentile=None, is_liquid=True,
        )
    if ivp > max_ivp_for_options:
        return LeverageRecommendation(
            mode="CASH",
            rationale=f"IV percentile {ivp:.0f}% is too high — options are expensive",
            iv_percentile=ivp, is_liquid=True,
        )
    return LeverageRecommendation(
        mode="ATM_CALL",
        rationale=f"Score {rule_score:.1f} + IVP {ivp:.0f}% favours ATM call",
        iv_percentile=ivp, is_liquid=True,
        suggested_strike=chain.atm_strike,
        suggested_expiry=chain.nearest_expiry,
        suggested_lots=1,
    )
