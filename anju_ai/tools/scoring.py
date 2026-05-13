#!/usr/bin/env python3
"""
anju_ai.tools.scoring — composite signal score for one symbol.

Forked from anju-trader/scanner.py:fetch_and_analyse (lines 407-822) with
two changes:
  1. Pure function — caller passes in `df`. No internal data fetching.
  2. Typed I/O via Pydantic — ScoreInput / ScoreResult.

All scoring weights match anju-trader exactly (v0 = baseline). Phase 1+
will replace these with walk-forward-backtested values per
config/strategies.yaml.

Usage:
    from anju_ai.tools.scoring import score_signal, ScoreInput
    out = score_signal(ScoreInput(symbol="RELIANCE", df=df, mode="strict"))
    if out:
        print(out.score, out.verdict, out.entry_model)
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from anju_core.indicators import (
    analyse_volume,
    get_base_analysis,
    get_mtfa_alignment,
    get_volume_signals,
)


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class ScoreInput(BaseModel):
    """Pure input — everything the scoring engine needs to produce a verdict."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str = Field(..., description="NSE symbol with or without .NS suffix")
    df: pd.DataFrame = Field(..., description="OHLCV with date index")
    mode: Literal["strict", "aggressive"] = "strict"
    nifty_close: pd.Series | None = Field(
        default=None,
        description="Nifty Close series aligned to df.index — for RS computation",
    )


class ExitLogic(BaseModel):
    """Stop + targets derived from price structure + base pivot + MAs."""
    stop: float
    partial_target: float
    t1_source: str
    full_target: float
    t2_source: str
    rr: float
    rule: str = "T1=nearest resistance, T2=next level, trail stop on MA20"
    all_targets: list = Field(default_factory=list)


class ScoreResult(BaseModel):
    """Composite score result — every field needed by downstream tools."""
    symbol: str
    price: float
    change_pct: float
    score: float
    breakdown: dict = Field(
        default_factory=dict,
        description="Map of feature_name → contribution. Sums to `score`.",
    )
    verdict: Literal["BUY", "WATCH", "AVOID"]
    reasoning: str
    tags: list[str] = Field(default_factory=list)
    entry_model: str = ""
    stage: int = 0
    stage_label: str = ""
    exit_logic: ExitLogic | None = None
    vol_signals: list = Field(default_factory=list)
    base_data: dict | None = None
    mtfa_data: dict | None = None
    rs_label: str = ""
    rs_diff: float = 0.0
    liquidity_ok: bool = True
    above_ma200: bool = False
    confidence: float = 0.5
    rejection_reason: str | None = None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _check_prior_breakout_vol(volume: pd.Series) -> bool:
    """Was there a real institutional breakout in last 20 bars (vol >= 1.5x avg)?"""
    try:
        if len(volume) < 40:
            return False
        avg = float(volume.iloc[-40:-20].mean())
        max_recent = float(volume.iloc[-20:].max())
        return avg > 0 and max_recent >= avg * 1.5
    except Exception:
        return False


def _add(breakdown: dict, key: str, value: float, tags: list, tag: str | None = None) -> float:
    """Record a scoring contribution + optionally append a tag. Returns value."""
    if value != 0:
        breakdown[key] = breakdown.get(key, 0) + value
    if tag and tag not in tags:
        tags.append(tag)
    return value


# ── Public API ────────────────────────────────────────────────────────────────

def score_signal(inp: ScoreInput) -> ScoreResult | None:
    """Score one symbol. Returns ScoreResult or None if the symbol fails the
    minimum-setup filter (no volume signal AND no base = no signal).

    This is the byte-for-byte port of anju-trader/scanner.py fetch_and_analyse,
    refactored to:
      - take df as input (no data fetching here — caller's job)
      - return a typed result instead of a dict
      - emit a `breakdown` showing how each feature contributed to the score
    """
    df = inp.df
    if df is None or len(df) < 60:
        return None

    vol_signals_list = get_volume_signals(df)
    base_data        = get_base_analysis(df)
    vol_data         = analyse_volume(df)
    mtfa_data        = get_mtfa_alignment(df)

    close  = df["Close"].astype(float)
    high   = df["High"].astype(float)
    low    = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    # ── Mode thresholds (exactly matching anju-trader) ──────────
    if inp.mode == "aggressive":
        MIN_LIQUIDITY  = 200_000
        MIN_BASE_SCORE = 2
        CLOSE_POS_MIN  = 0.40
        TIGHT_PCT      = 12.0
        LOOSE_PCT      = 30.0
        EXHAUST_WARN   = 30
        EXHAUST_DANGER = 40
    else:
        MIN_LIQUIDITY  = 500_000
        MIN_BASE_SCORE = 3
        CLOSE_POS_MIN  = 0.55
        TIGHT_PCT      = 8.0
        LOOSE_PCT      = 20.0
        EXHAUST_WARN   = 20
        EXHAUST_DANGER = 30

    # Liquidity filter
    avg_vol_60 = float(volume.iloc[-60:].mean()) if len(volume) >= 60 else float(volume.mean())
    if avg_vol_60 < MIN_LIQUIDITY:
        return None

    # Feature presence flags
    has_dryup    = any(s["name"] == "💧 Volume Dry-Up"        for s in vol_signals_list)
    has_pocket   = any(s["name"] == "⚡ Pocket Pivot"          for s in vol_signals_list)
    has_breakout = any(s["name"] == "🚀 Breakout Volume"       for s in vol_signals_list)
    has_accum    = any(s["name"] == "🏦 Accumulation Detected" for s in vol_signals_list)
    has_climax   = any(s["name"] == "🔥 Climax Volume"         for s in vol_signals_list)

    base_found = bool(base_data and base_data.get("found") and base_data.get("base_line"))
    base_score = base_data.get("score", 0) if base_data else 0
    base_good  = base_found and base_score >= MIN_BASE_SCORE

    # Filter: climax alone or nothing → reject
    if has_climax and not any([has_dryup, has_pocket, has_breakout, has_accum, base_good]):
        return None
    if not any([has_dryup, has_pocket, has_breakout, has_accum, base_good]):
        return None

    # ── Exhaustion filter ────────────────────────────────────────
    exhaustion_note = ""
    exhaustion_penalty = 0
    if len(close) >= 50:
        ma50_ex = float(close.rolling(50).mean().iloc[-1])
        cur_ex  = float(close.iloc[-1])
        if ma50_ex > 0:
            ext_pct = (cur_ex - ma50_ex) / ma50_ex * 100
            if ext_pct >= EXHAUST_DANGER:
                exhaustion_penalty = -4
                exhaustion_note = f"🚨 Extended {round(ext_pct,1)}% > MA50"
            elif ext_pct >= EXHAUST_WARN:
                exhaustion_penalty = -2
                exhaustion_note = f"⚠️ Extended {round(ext_pct,1)}% > MA50"

    # ── MTFA filter ──────────────────────────────────────────────
    mtfa_penalty = 0
    mtfa_note    = ""
    if mtfa_data:
        w_rsi = mtfa_data.get("w_rsi", 50)
        if w_rsi < 40:
            mtfa_penalty = -8
            mtfa_note = f"🚨 Severe weekly downtrend (RSI {w_rsi})"
        elif w_rsi < 45:
            mtfa_penalty = -5
            mtfa_note = f"⚠️ Weekly downtrend (RSI {w_rsi})"

    # ── Volume quality on breakout candle ───────────────────────
    vol_quality_note = ""
    vol_quality_ok = True
    if has_breakout:
        last_high  = float(high.iloc[-1])
        last_low   = float(low.iloc[-1])
        last_close = float(close.iloc[-1])
        candle_range = last_high - last_low
        if candle_range > 0:
            close_position = (last_close - last_low) / candle_range
            if close_position < CLOSE_POS_MIN:
                vol_quality_ok = False
                has_breakout = False
                vol_quality_note = "⚠️ Upper wick"
            elif close_position >= 0.75:
                vol_quality_note = "✅ Clean close"

    # Range expansion (used in combo scoring)
    recent_vol   = float(volume.iloc[-1])
    avg_vol_20   = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else recent_vol
    last_range   = float(high.iloc[-1]) - float(low.iloc[-1])
    avg_range_10 = float((high.iloc[-10:] - low.iloc[-10:]).mean()) if len(df) >= 10 else last_range
    range_expansion = (last_range / avg_range_10) if avg_range_10 > 0 else 1.0
    is_wide_candle = range_expansion >= 1.5
    is_high_vol    = recent_vol >= avg_vol_20 * 1.5

    # Base tightness
    if len(close) >= 15:
        window_prices = close.iloc[-15:]
        tightness_pct = float((window_prices.max() - window_prices.min()) / window_prices.min() * 100)
    else:
        tightness_pct = 99.0
    is_tight_base = tightness_pct <= TIGHT_PCT
    is_loose_base = tightness_pct >= LOOSE_PCT

    # ── Relative strength vs Nifty (dual-period: 20d trend + 5d momentum) ──
    rs_diff  = 0.0
    rs_score = 0
    rs_label = ""
    if inp.nifty_close is not None and len(close) >= 20:
        try:
            nifty_close = inp.nifty_close
            stock_ret_20 = float((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100)
            nifty_ret_20 = float((nifty_close.iloc[-1] - nifty_close.iloc[-20]) / nifty_close.iloc[-20] * 100)
            rs_diff = stock_ret_20 - nifty_ret_20

            rs_accelerating = True
            if len(close) >= 6 and len(nifty_close) >= 6:
                try:
                    stock_ret_5 = float((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100)
                    nifty_ret_5 = float((nifty_close.iloc[-1] - nifty_close.iloc[-6]) / nifty_close.iloc[-6] * 100)
                    rs_accelerating = (stock_ret_5 - nifty_ret_5) >= 0
                except Exception:
                    pass

            if rs_diff >= 5:
                rs_score = 4 if rs_accelerating else 2
                rs_label = (f"⭐ RS+{round(rs_diff,1)}%" if rs_accelerating
                            else f"⚠️ RS+{round(rs_diff,1)}% (decelerating)")
            elif rs_diff >= 2:
                rs_score = 2 if rs_accelerating else 0
                rs_label = (f"RS+{round(rs_diff,1)}%" if rs_accelerating
                            else f"RS+{round(rs_diff,1)}% (fading)")
            elif rs_diff < -5:
                rs_score = -2
                rs_label = f"RS{round(rs_diff,1)}%"
        except Exception:
            pass

    # ── Scoring ──────────────────────────────────────────────────
    score: float = 0
    tags: list[str] = []
    breakdown: dict[str, float] = {}

    if has_dryup:
        score += _add(breakdown, "volume_dryup", 3, tags, "💧 Dry-Up")
    if has_pocket:
        score += _add(breakdown, "pocket_pivot", 3, tags, "⚡ Pocket Pivot")
    if has_breakout:
        score += _add(breakdown, "breakout_vol", 4, tags, "🚀 Breakout Vol")
        if is_wide_candle and is_high_vol:
            score += _add(breakdown, "wide_candle_bonus", 2, tags, "💪 Wide+Vol")
    if has_accum:
        strength = next((s.get("strength", "MODERATE") for s in vol_signals_list
                         if s["name"] == "🏦 Accumulation Detected"), "MODERATE")
        v = 3 if strength == "HIGH" else 2
        score += _add(breakdown, "accumulation", v, tags, "🏦 Accumulation")
    if base_good:
        score += _add(breakdown, "base_score", base_score, tags, "🏗️ Base")
        if base_data and "2nd" in str(base_data.get("base_number", "")):
            score += _add(breakdown, "second_base_bonus", 3, tags, "⭐ 2nd Base")

    # Combo bonuses
    if has_dryup and base_good:
        score += _add(breakdown, "combo_dryup_base", 3, tags, "🔥 Dry-Up+Base")
    if has_pocket and base_good:
        score += _add(breakdown, "combo_pocket_base", 4, tags, "🎯 Pivot+Base")

    # Tightness
    if is_tight_base and base_good:
        score += _add(breakdown, "tight_base_bonus", 2, tags, "🔒 Tight")
    elif is_loose_base and base_good:
        score += _add(breakdown, "loose_base_penalty", -2, tags)

    # RS
    if rs_score:
        score += _add(breakdown, "rs_vs_nifty", rs_score, tags, rs_label)

    # Volume quality
    if not vol_quality_ok and vol_quality_note:
        score += _add(breakdown, "vol_quality_penalty", -2, tags, vol_quality_note)

    # Exhaustion
    if exhaustion_penalty:
        score += _add(breakdown, "exhaustion", exhaustion_penalty, tags, exhaustion_note)

    # MTFA
    if mtfa_penalty:
        score += _add(breakdown, "mtfa", mtfa_penalty, tags, mtfa_note)

    # ── Entry model detection ────────────────────────────────────
    entry_model = ""
    ma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else 0
    cur  = float(close.iloc[-1])

    if has_breakout and base_good:
        pivot = base_data.get("pivot", 0) if base_data else 0
        if pivot and cur <= pivot * 1.03:
            entry_model = "🔄 Retest Entry"
        else:
            entry_model = "🚀 Breakout Entry"
    elif has_dryup and base_good and is_tight_base:
        entry_model = "🎯 Early Base Entry"
    elif has_pocket and ma20 > 0 and cur > ma20 and (cur - ma20) / ma20 < 0.05:
        entry_model = "📈 Momentum Entry"
    elif has_dryup and ma20 > 0:
        entry_model = "📈 Momentum Entry"

    # ── Weinstein stage detection ────────────────────────────────
    stage = 0
    stage_label = ""
    stage_score_adj = 0
    try:
        if len(close) >= 200:
            ma200       = float(close.rolling(200, min_periods=200).mean().iloc[-1])
            ma50_val    = float(close.rolling(50, min_periods=50).mean().iloc[-1]) if len(close) >= 50 else 0.0
            ma50_20ago  = float(close.rolling(50, min_periods=50).mean().iloc[-21]) if len(close) >= 71 else ma50_val
            ma200_20ago = float(close.rolling(200, min_periods=200).mean().iloc[-21]) if len(close) >= 221 else None

            import math
            if not math.isnan(ma200) and ma200 != 0:
                ma200_rising = (ma200 > ma200_20ago) if ma200_20ago is not None else None
                ma50_rising  = ma50_val > ma50_20ago if (ma50_val and ma50_20ago) else None
                above_ma200  = float(close.iloc[-1]) > ma200
                above_ma50   = float(close.iloc[-1]) > ma50_val if ma50_val else False
                cur_c = float(close.iloc[-1])
                high_52w = float(close.iloc[-252:].max()) if len(close) >= 252 else float(close.max())
                from_52w_high_pct = (high_52w - cur_c) / high_52w * 100

                if above_ma200 and ma200_rising and ma50_rising and above_ma50:
                    if from_52w_high_pct <= 15:
                        stage = 2; stage_label = "✅ Stage 2 (Best)"; stage_score_adj = 2
                    else:
                        stage = 2; stage_label = "✅ Stage 2"; stage_score_adj = 1
                elif not above_ma200 and not ma200_rising and above_ma50:
                    stage = 1; stage_label = "⏳ Stage 1 (Basing)"; stage_score_adj = 0
                elif above_ma200 and not ma50_rising and from_52w_high_pct <= 5:
                    stage = 3; stage_label = "⚠️ Stage 3 (Topping)"; stage_score_adj = -2
                elif not above_ma200 and not ma200_rising:
                    stage = 4; stage_label = "❌ Stage 4 (Downtrend)"; stage_score_adj = -4
                else:
                    stage = 1; stage_label = "⏳ Stage 1/2 Transition"; stage_score_adj = 0

                if stage_score_adj:
                    score += _add(breakdown, "stage", stage_score_adj, tags, stage_label)
                elif stage_label:
                    tags.append(stage_label)
    except Exception:
        pass

    # ── Exit logic — stop + targets ──────────────────────────────
    exit_logic: ExitLogic | None = None
    try:
        if len(close) >= 20 and len(low) >= 20:
            from scipy.signal import argrelextrema

            ma20_val = float(close.rolling(20, min_periods=20).mean().iloc[-1])
            ma50_ex  = (float(close.rolling(50, min_periods=50).mean().iloc[-1])
                        if len(close) >= 50 else None)
            ma200_ex = (float(close.rolling(200, min_periods=200).mean().iloc[-1])
                        if len(close) >= 200 else None)
            swing_low10 = float(low.iloc[-10:].min())
            cur_price = float(close.iloc[-1])

            # Stop: graduated buffers below key levels
            stop_candidates = []
            if swing_low10 < cur_price * 0.99:
                stop_candidates.append(swing_low10 * 0.995)
            if ma20_val and not pd.isna(ma20_val) and ma20_val < cur_price * 0.99:
                stop_candidates.append(ma20_val * 0.995)
            if ma50_ex and not pd.isna(ma50_ex) and ma50_ex < cur_price * 0.99:
                stop_candidates.append(ma50_ex * 0.992)
            if ma200_ex and not pd.isna(ma200_ex) and ma200_ex < cur_price * 0.99:
                stop_candidates.append(ma200_ex * 0.988)
            trailing_stop = round(max(stop_candidates), 2) if stop_candidates else round(cur_price * 0.95, 2)

            # Targets: swing highs above price
            h_arr = high.values
            lookback = min(len(h_arr), 252)
            peaks_idx = argrelextrema(h_arr[-lookback:], np.greater_equal, order=7)[0]
            target_candidates = []
            for i in peaks_idx:
                p = float(h_arr[-lookback:][i])
                if p > cur_price * 1.005:
                    target_candidates.append((round(p, 2), "swing high"))

            # Fibonacci extensions from base pivot
            pivot_val = float(base_data.get("pivot", 0) or 0) if base_data else 0
            if pivot_val > 0:
                depth_pct = float(base_data.get("base_depth", 15) or 15) / 100
                base_low_v = pivot_val * (1 - depth_pct)
                pole = pivot_val - base_low_v
                for ratio, lbl in [(0.618, "Fib 0.618"), (1.0, "Fib 1.0"), (1.618, "Fib 1.618")]:
                    fib_p = round(pivot_val + ratio * pole, 2)
                    if fib_p > cur_price * 1.005:
                        target_candidates.append((fib_p, lbl))

            # MA levels above price
            for ma_v, lbl in [(ma50_ex, "MA50"), (ma200_ex, "MA200")]:
                if ma_v and not pd.isna(ma_v) and ma_v > cur_price * 1.005:
                    target_candidates.append((round(ma_v, 2), lbl))

            # Sort + dedupe within 1.5% zones
            target_candidates.sort(key=lambda x: x[0])
            merged = []
            prio = {"swing high": 0, "Fib 0.618": 1, "Fib 1.0": 1, "Fib 1.618": 1, "MA50": 2, "MA200": 2}
            for p, lbl in target_candidates:
                if merged and abs(p - merged[-1][0]) / merged[-1][0] < 0.015:
                    if prio.get(lbl, 3) < prio.get(merged[-1][1], 3):
                        merged[-1] = (p, lbl)
                else:
                    merged.append((p, lbl))

            t1 = merged[0] if len(merged) > 0 else (round(cur_price * 1.07, 2), "fallback +7%")
            t2 = merged[1] if len(merged) > 1 else (round(cur_price * 1.15, 2), "fallback +15%")
            rr = round((t1[0] - cur_price) / max(cur_price - trailing_stop, 0.01), 1)

            if rr > 0:
                exit_logic = ExitLogic(
                    stop=trailing_stop,
                    partial_target=t1[0],
                    t1_source=t1[1],
                    full_target=t2[0],
                    t2_source=t2[1],
                    rr=rr,
                    all_targets=merged,
                )
    except Exception:
        pass

    # ── Verdict ──────────────────────────────────────────────────
    # Anju-trader-AI v0: simple thresholds matching anju-trader's STATES.min_score
    # ranges. Phase 1+ calibrates these from backtest.
    if score >= 15:
        verdict = "BUY"
    elif score >= 8:
        verdict = "WATCH"
    else:
        verdict = "AVOID"

    cur_price  = round(float(close.iloc[-1]), 2)
    prev_close = float(close.iloc[-2]) if len(df) > 1 else cur_price
    change_pct = round((cur_price - prev_close) / prev_close * 100, 2)

    reasoning_parts = []
    if entry_model:
        reasoning_parts.append(f"Setup: {entry_model}.")
    if stage_label:
        reasoning_parts.append(stage_label + ".")
    if rs_label:
        reasoning_parts.append(rs_label + ".")
    if mtfa_note:
        reasoning_parts.append(mtfa_note + ".")
    if not reasoning_parts:
        reasoning_parts.append("Composite score from volume + base + structure features.")
    reasoning = " ".join(reasoning_parts)

    above_ma200 = bool(len(close) >= 200
                       and float(close.iloc[-1]) > float(close.rolling(200, min_periods=200).mean().iloc[-1]))

    return ScoreResult(
        symbol=inp.symbol.replace(".NS", "").replace(".BSE", ""),
        price=cur_price,
        change_pct=change_pct,
        score=float(score),
        breakdown=breakdown,
        verdict=verdict,
        reasoning=reasoning,
        tags=tags,
        entry_model=entry_model,
        stage=stage,
        stage_label=stage_label,
        exit_logic=exit_logic,
        vol_signals=vol_signals_list,
        base_data=base_data,
        mtfa_data=mtfa_data,
        rs_label=rs_label,
        rs_diff=float(rs_diff),
        liquidity_ok=avg_vol_60 >= MIN_LIQUIDITY,
        above_ma200=above_ma200,
        confidence=min(1.0, max(0.0, score / 25.0)),
    )
