#!/usr/bin/env python3
"""
anju_ai.tools.bear_playbook — defensive plays for Bear regime.

Audit Finding 3.7: anju-trader's Bear regime just raises min_score=9.
Result: zero signals fire, system hibernates for 20–30% of any 2-year
window. CAGR ceiling drops massively.

This module gives the system something to DO in Bear regime:
  1. Long defensive sectors (FMCG, Pharma, Gold) that historically
     outperform during equity downturns
  2. Short F&O on the weakest momentum names (worst RS over 20 days)
  3. Hard cap on net long exposure (default 30%)

Mode is GATED in config/runtime.yaml until backtest validates that
defensive longs + shorts actually outperform cash during Bear regimes.

Used by morning_scan as an additional candidate filter when regime
flips to Bear. Replaces — not augments — the normal scan in Bear.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field


# ── Defensive sector universe (curated) ──────────────────────────────────────
# These are NSE symbols that historically held up better in equity bear
# markets (2008, 2020, 2022 corrections). Hand-picked, not algorithmic.

DEFENSIVE_LONG_UNIVERSE = [
    # FMCG (revenue floor in bear markets)
    "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS",
    "DABUR.NS", "MARICO.NS", "COLPAL.NS", "GODREJCP.NS", "TATACONSUM.NS",
    # Pharma (counter-cyclical demand)
    "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS",
    "LUPIN.NS", "AUROPHARMA.NS", "TORNTPHARM.NS",
    # Healthcare services
    "APOLLOHOSP.NS", "MAXHEALTH.NS",
    # Utilities (regulated returns)
    "NTPC.NS", "POWERGRID.NS",
    # Gold proxy (BSE/NSE listed)
    "TITAN.NS",      # gold jewelry — partial proxy
]

# Short-candidate universe — same as the main universe but filtered to
# F&O-eligible names. In Bear regime we short the weakest, not the
# strongest.
SHORT_FNO_UNIVERSE: list[str] = []   # populated lazily from config


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class BearPick(BaseModel):
    symbol:        str
    side:          Literal["LONG_DEFENSIVE", "SHORT_FNO"]
    score:         float            # 0..100 — defensive strength or short conviction
    rationale:     str
    rs_diff_20d:   float = 0.0      # vs Nifty
    above_ma200:   bool = False
    suggested_qty_pct: float = 0.0  # % of capital


class BearPlaybook(BaseModel):
    regime:        str
    long_picks:    list[BearPick] = Field(default_factory=list)
    short_picks:   list[BearPick] = Field(default_factory=list)
    max_net_long_pct:  float = 30.0   # hard cap on net long exposure
    cash_pct:      float = 70.0
    notes:         str = ""


# ── Internal helpers ─────────────────────────────────────────────────────────

def _rel_strength_20d(df: pd.DataFrame, nifty_close: pd.Series | None) -> float:
    """Relative strength: stock 20d return − Nifty 20d return."""
    if df is None or len(df) < 20 or nifty_close is None or len(nifty_close) < 20:
        return 0.0
    try:
        s_ret = (df["Close"].iloc[-1] - df["Close"].iloc[-20]) / df["Close"].iloc[-20] * 100
        n_ret = (nifty_close.iloc[-1] - nifty_close.iloc[-20]) / nifty_close.iloc[-20] * 100
        return float(s_ret - n_ret)
    except Exception:
        return 0.0


def _above_ma200(df: pd.DataFrame) -> bool:
    if df is None or len(df) < 200:
        return False
    try:
        ma = float(df["Close"].rolling(200).mean().iloc[-1])
        return float(df["Close"].iloc[-1]) > ma
    except Exception:
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def score_defensive_long(symbol: str, df: pd.DataFrame,
                         nifty_close: pd.Series | None) -> BearPick | None:
    """A defensive long is attractive when it's outperforming Nifty AND
    sitting above its own MA200 (own trend intact)."""
    if df is None or df.empty:
        return None
    rs = _rel_strength_20d(df, nifty_close)
    above_ma200 = _above_ma200(df)

    # Defensive score: RS dominates, MA200 confirms
    score = 0.0
    if rs > 5:
        score += 6
    elif rs > 0:
        score += 3
    if above_ma200:
        score += 3
    if rs > 10:
        score += 2   # very strong defensive — bonus
    if score < 3:
        return None

    return BearPick(
        symbol=symbol.replace(".NS", ""), side="LONG_DEFENSIVE",
        score=round(score, 1),
        rationale=(f"Defensive RS {rs:+.1f}% vs Nifty over 20d; "
                   f"price {'above' if above_ma200 else 'below'} MA200"),
        rs_diff_20d=round(rs, 2),
        above_ma200=above_ma200,
        suggested_qty_pct=2.0,   # 2% per position
    )


def score_short_candidate(symbol: str, df: pd.DataFrame,
                          nifty_close: pd.Series | None,
                          fno_eligible: bool = False) -> BearPick | None:
    """A short candidate is the WEAKEST momentum: most negative RS AND
    below MA200. F&O eligibility required for actual shorting."""
    if not fno_eligible:
        return None
    if df is None or df.empty:
        return None

    rs = _rel_strength_20d(df, nifty_close)
    above_ma200 = _above_ma200(df)

    # Want STRONGLY negative RS and below MA200
    score = 0.0
    if rs < -10:
        score += 7
    elif rs < -5:
        score += 4
    elif rs < 0:
        score += 1
    if not above_ma200:
        score += 3
    if rs < -15:
        score += 2
    if score < 4:
        return None

    return BearPick(
        symbol=symbol.replace(".NS", ""), side="SHORT_FNO",
        score=round(score, 1),
        rationale=(f"Weak RS {rs:+.1f}% vs Nifty; below MA200 ({not above_ma200}); "
                   f"target short via PE Nov/Dec expiry"),
        rs_diff_20d=round(rs, 2),
        above_ma200=above_ma200,
        suggested_qty_pct=1.5,   # smaller — shorts are riskier
    )


def build_playbook(regime_state: str,
                   defensive_dfs: dict[str, pd.DataFrame],
                   short_candidate_dfs: dict[str, pd.DataFrame],
                   nifty_close: pd.Series | None,
                   fno_eligible_set: set[str] | None = None,
                   max_long_picks: int = 8,
                   max_short_picks: int = 5,
                   enabled: bool = False) -> BearPlaybook:
    """Build the full bear-regime playbook.

    Args:
        regime_state: e.g. 'Bear'. If not Bear, returns empty playbook.
        defensive_dfs: {symbol: ohlcv_df} for DEFENSIVE_LONG_UNIVERSE
        short_candidate_dfs: {symbol: ohlcv_df} from F&O universe
        nifty_close: Nifty Close series for RS computation
        fno_eligible_set: set of F&O-eligible symbols (for shorts)
        enabled: when False, returns empty playbook with note
                  (config gate — flip to True after backtest validates)
    """
    if regime_state != "Bear":
        return BearPlaybook(regime=regime_state,
                             notes="Not Bear — playbook not invoked")
    if not enabled:
        return BearPlaybook(regime="Bear",
                             notes="Bear playbook disabled until backtest validates")

    fno = fno_eligible_set or set()

    longs: list[BearPick] = []
    for sym, df in defensive_dfs.items():
        pick = score_defensive_long(sym, df, nifty_close)
        if pick is not None:
            longs.append(pick)
    longs.sort(key=lambda p: p.score, reverse=True)
    longs = longs[:max_long_picks]

    shorts: list[BearPick] = []
    for sym, df in short_candidate_dfs.items():
        bare = sym.replace(".NS", "")
        pick = score_short_candidate(sym, df, nifty_close,
                                      fno_eligible=(bare in fno))
        if pick is not None:
            shorts.append(pick)
    shorts.sort(key=lambda p: p.score, reverse=True)
    shorts = shorts[:max_short_picks]

    # Net long exposure cap
    long_alloc = sum(p.suggested_qty_pct for p in longs)
    if long_alloc > 30.0:
        # Scale down proportionally
        factor = 30.0 / long_alloc
        for p in longs:
            p.suggested_qty_pct = round(p.suggested_qty_pct * factor, 2)
        long_alloc = 30.0

    short_alloc = sum(p.suggested_qty_pct for p in shorts)
    cash_pct = max(0.0, 100.0 - long_alloc - short_alloc)

    return BearPlaybook(
        regime="Bear",
        long_picks=longs, short_picks=shorts,
        max_net_long_pct=long_alloc,
        cash_pct=round(cash_pct, 1),
        notes=(f"{len(longs)} defensive longs, {len(shorts)} F&O shorts. "
               f"Long exposure {long_alloc:.1f}%, short {short_alloc:.1f}%, "
               f"cash {cash_pct:.1f}%."),
    )


def render_telegram(pb: BearPlaybook) -> str:
    if pb.regime != "Bear":
        return ""
    if not pb.long_picks and not pb.short_picks:
        return (f"🐻 <b>Bear Playbook</b>\n"
                f"<i>{pb.notes}</i>")

    lines = [f"🐻 <b>anju-AI · Bear Playbook</b>",
             f"<i>{pb.notes}</i>"]
    if pb.long_picks:
        lines.append("\n<b>🟢 Defensive longs</b>")
        for p in pb.long_picks:
            lines.append(f"  <b>{p.symbol}</b>  score {p.score}  "
                         f"{p.suggested_qty_pct:.1f}% — {p.rationale}")
    if pb.short_picks:
        lines.append("\n<b>🔴 F&O shorts</b>")
        for p in pb.short_picks:
            lines.append(f"  <b>{p.symbol}</b>  score {p.score}  "
                         f"{p.suggested_qty_pct:.1f}% — {p.rationale}")
    return "\n".join(lines)
