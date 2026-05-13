#!/usr/bin/env python3
"""
anju_core.indicators — technical indicators forked from anju-trader/stockview.py

The 4 functions the scoring engine depends on:
    analyse_volume(df)       Current vs avg / 5d / week volume + signal text
    get_mtfa_alignment(df)   Multi-timeframe alignment (weekly RSI + MA10)
    get_volume_signals(df)   5 volume patterns: dry-up, breakout, accumulation,
                             distribution, climax, pocket pivot
    get_base_analysis(df)    Base detection: type, depth, weeks, VCP, pivot,
                             rating, base number (1st vs 2nd)

Forked byte-for-byte from anju-trader/stockview.py (lines 239–283, 289–326,
1269–1395, 1401–1593). No behavioural changes.

All four take a pandas DataFrame with columns Open/High/Low/Close/Volume
indexed by date, and return a dict (or list of dicts for volume signals).
Pure compute, no I/O.
"""

from __future__ import annotations

import warnings

import pandas as pd
import ta

warnings.filterwarnings("ignore")


# ── analyse_volume ────────────────────────────────────────────────────────────

def analyse_volume(df: pd.DataFrame) -> dict:
    """Current vs average / 5d / weekly volume, with formatted strings + signal."""
    try:
        vol = df["Volume"]
        cur_vol = int(vol.iloc[-1])
        avg_20  = int(vol.tail(20).mean())
        avg_5   = int(vol.tail(5).mean())
        max_30  = int(vol.tail(30).max())

        # Week volume = last 5 trading days; prev_week = the 5 before that
        week_vol  = int(vol.tail(5).sum())
        prev_week = int(vol.tail(10).head(5).sum())

        ratio_to_avg  = round(cur_vol / avg_20, 2) if avg_20 else 0
        ratio_to_week = round(cur_vol / (week_vol / 5), 2) if week_vol else 0
        is_peak       = cur_vol >= max_30 * 0.9

        def fmt_vol(v: int) -> str:
            if v >= 1_00_00_000:
                return f"{v / 1_00_00_000:.2f} Cr"
            if v >= 1_00_000:
                return f"{v / 1_00_000:.2f} L"
            return f"{v:,}"

        if ratio_to_avg >= 2.0:
            vol_signal = "🔥 Very High Volume (2x avg) — strong conviction"
        elif ratio_to_avg >= 1.5:
            vol_signal = "📈 High Volume (1.5x avg) — above normal interest"
        elif ratio_to_avg >= 1.0:
            vol_signal = "🟡 Normal Volume"
        elif ratio_to_avg >= 0.5:
            vol_signal = "📉 Below Average Volume"
        else:
            vol_signal = "😴 Very Low Volume — weak conviction"

        week_change = round(((week_vol - prev_week) / prev_week * 100), 1) if prev_week else 0

        return {
            "current":      fmt_vol(cur_vol),
            "current_raw":  cur_vol,
            "avg_20":       fmt_vol(avg_20),
            "avg_20_raw":   avg_20,
            "avg_5":        fmt_vol(avg_5),
            "week_total":   fmt_vol(week_vol),
            "prev_week":    fmt_vol(prev_week),
            "week_change":  week_change,
            "ratio_to_avg": ratio_to_avg,
            "is_peak":      is_peak,
            "signal":       vol_signal,
        }
    except Exception:
        return {
            "current": "—", "avg_20": "—", "signal": "—",
            "ratio_to_avg": 0, "is_peak": False,
            "week_total": "—", "prev_week": "—",
            "week_change": 0, "avg_5": "—", "current_raw": 0,
        }


# ── get_mtfa_alignment ────────────────────────────────────────────────────────

def get_mtfa_alignment(df: pd.DataFrame) -> dict | None:
    """Multi-timeframe alignment: weekly RSI + weekly MA10 + alignment flag.
    Returns None if data insufficient or resample fails."""
    try:
        if len(df) < 50:
            return None

        df_w = df.resample("W-FRI").agg({
            "Open":   "first",
            "High":   "max",
            "Low":    "min",
            "Close":  "last",
            "Volume": "sum",
        }).dropna()

        if len(df_w) < 10:
            return None

        cur_price = float(df["Close"].iloc[-1])

        w_rsi  = round(float(ta.momentum.RSIIndicator(df_w["Close"], window=14).rsi().iloc[-1]), 1)
        w_ma10 = round(float(df_w["Close"].rolling(10).mean().iloc[-1]), 2)

        price_vs_w10 = round(((cur_price - w_ma10) / w_ma10) * 100, 1) if w_ma10 else 0
        aligned = (w_rsi > 50) and (cur_price > w_ma10)

        return {
            "aligned":      aligned,
            "w_rsi":        w_rsi,
            "w_ma10":       w_ma10,
            "price_vs_w10": price_vs_w10,
        }
    except Exception:
        return None


# ── get_volume_signals ────────────────────────────────────────────────────────

def get_volume_signals(df: pd.DataFrame) -> list:
    """Return list of 5 volume signal dicts (drying-up, breakout, accumulation/
    distribution, climax, pocket pivot). Empty list if data too short."""
    try:
        close = df["Close"]
        vol   = df["Volume"]
        opens = df["Open"]
        n     = len(df)
        if n < 30:
            return []

        signals = []
        avg20 = vol.tail(20).mean()

        # 1. VOLUME DRY-UP
        last5_vols = vol.iloc[-5:].values
        prev5_avg  = vol.iloc[-10:-5].mean()
        dryup_score = sum(1 for v in last5_vols if v < prev5_avg * 0.8)
        trend_down  = all(last5_vols[i] <= last5_vols[i - 1] * 1.05 for i in range(1, 5))
        if dryup_score >= 3 and trend_down:
            hi5 = close.iloc[-5:].max()
            lo5 = close.iloc[-5:].min()
            range_pct = (hi5 - lo5) / lo5 * 100
            if range_pct < 6:
                signals.append({
                    "name":     "💧 Volume Dry-Up",
                    "color":    "#4da6ff",
                    "signal":   "BULLISH SETUP",
                    "detail":   f"Vol shrinking {dryup_score}/5 days, price range only {range_pct:.1f}%. Breakout loading.",
                    "strength": "HIGH" if dryup_score == 5 else "MODERATE",
                })

        # 2. BREAKOUT VOLUME (2.5x rule)
        cur_vol    = vol.iloc[-1]
        cur_close  = close.iloc[-1]
        prev_close = close.iloc[-2]
        ratio      = cur_vol / avg20 if avg20 else 0
        is_up_day  = cur_close > prev_close
        if ratio >= 2.5 and is_up_day:
            signals.append({
                "name":     "🚀 Breakout Volume",
                "color":    "#26c940",
                "signal":   "STRONG BREAKOUT",
                "detail":   f"Today's volume is {ratio:.1f}x the 20-day avg on an up day.",
                "strength": "HIGH",
            })
        elif ratio >= 1.5 and is_up_day:
            signals.append({
                "name":     "📈 Above-Avg Up Volume",
                "color":    "#66bb6a",
                "signal":   "MODERATE BREAKOUT",
                "detail":   f"Volume {ratio:.1f}x avg on up day. Watch for 2.5x for high confidence.",
                "strength": "MODERATE",
            })

        # 3. UP/DOWN VOLUME RATIO (accumulation vs distribution)
        up_vol   = sum(vol.iloc[i] for i in range(-15, 0) if close.iloc[i] >= opens.iloc[i])
        down_vol = sum(vol.iloc[i] for i in range(-15, 0) if close.iloc[i] <  opens.iloc[i])
        ud_ratio = round(up_vol / down_vol, 2) if down_vol else 99
        if ud_ratio >= 2.0:
            signals.append({
                "name":     "🏦 Accumulation Detected",
                "color":    "#26c940",
                "signal":   "INSTITUTIONAL BUYING",
                "detail":   f"Up-day volume is {ud_ratio}x down-day volume over 15 days.",
                "strength": "HIGH" if ud_ratio >= 3.0 else "MODERATE",
            })
        elif ud_ratio <= 0.5:
            # Guard against div-by-zero when up_vol == 0 (the original silent failure path)
            if ud_ratio > 0:
                inv_ratio = round(1 / ud_ratio, 1)
            elif up_vol > 0:
                inv_ratio = round(down_vol / up_vol, 1)
            else:
                inv_ratio = 99
            signals.append({
                "name":     "📤 Distribution Detected",
                "color":    "#ff4d4f",
                "signal":   "INSTITUTIONAL SELLING",
                "detail":   f"Down-day volume {inv_ratio}x up-day volume over 15 days.",
                "strength": "HIGH" if ud_ratio <= 0.35 else "MODERATE",
            })

        # 4. CLIMAX VOLUME (exhaustion)
        up_pct = (cur_close - prev_close) / prev_close * 100
        rally  = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100
        if cur_vol >= avg20 * 4 and is_up_day and up_pct > 2 and rally > 15:
            signals.append({
                "name":     "🔥 Climax Volume",
                "color":    "#ff9f40",
                "signal":   "EXHAUSTION WARNING",
                "detail":   f"Volume {ratio:.1f}x avg, up {up_pct:.1f}% after {rally:.0f}% rally.",
                "strength": "HIGH",
            })

        # 5. POCKET PIVOT — green day whose volume > highest red-day volume in prior 10 days
        if is_up_day:
            max_red_vol = max(
                (vol.iloc[i] for i in range(-11, -1) if close.iloc[i] < opens.iloc[i]),
                default=0,
            )
            if max_red_vol > 0 and cur_vol > max_red_vol:
                signals.append({
                    "name":     "⚡ Pocket Pivot",
                    "color":    "#fdd835",
                    "signal":   "EARLY ENTRY SIGNAL",
                    "detail":   f"Today's up-day volume ({int(cur_vol/1e5):.1f}L) exceeds highest red-day vol in last 10 days.",
                    "strength": "HIGH",
                })

        return signals
    except Exception:
        return []


# ── get_base_analysis ─────────────────────────────────────────────────────────

def get_base_analysis(df: pd.DataFrame) -> dict | None:
    """Detect the most recent base formation: length, depth, type (flat / normal
    / deep), VCP flag, 1st vs 2nd base, pivot, rating, base_line (for chart)."""
    try:
        close = df["Close"]
        vol   = df["Volume"]
        high  = df["High"]
        n     = len(df)
        if n < 60:
            return None

        # Find the most recent consolidation: any window of 25+ days with depth ≤ 35%
        best_base_start = None
        best_base_end   = n
        best_base_high  = 0.0
        best_base_low   = 0.0

        for end_i in range(n - 1, max(n - 252, 25), -5):
            for start_i in range(end_i - 25, max(end_i - 200, 0), -5):
                segment = close.iloc[start_i:end_i]
                seg_hi  = segment.max()
                seg_lo  = segment.min()
                depth   = (seg_hi - seg_lo) / seg_hi * 100
                length  = end_i - start_i
                if depth <= 35 and length >= 25:
                    if seg_hi > best_base_high:
                        best_base_high  = float(seg_hi)
                        best_base_low   = float(seg_lo)
                        best_base_start = start_i
                        best_base_end   = end_i
            if best_base_start is not None:
                break

        if best_base_start is None:
            return {"found": False, "reason": "No base pattern detected in last 252 days."}

        base_len   = best_base_end - best_base_start
        base_weeks = round(base_len / 5, 1)
        base_depth = round((best_base_high - best_base_low) / best_base_high * 100, 1)
        cur_price  = float(close.iloc[-1])
        from_pivot = round((cur_price - best_base_high) / best_base_high * 100, 1)

        # Base type by depth
        if base_depth <= 12:
            base_type = "Flat Base"
            type_col  = "#26c940"
            type_note = "Very tight — strongest base type, minimal correction"
        elif base_depth <= 20:
            base_type = "Normal Base"
            type_col  = "#4da6ff"
            type_note = "Healthy correction, typical institutional accumulation"
        elif base_depth <= 35:
            base_type = "Deep Base"
            type_col  = "#ff9f40"
            type_note = "Deeper correction — needs strong breakout volume to confirm"
        else:
            base_type = "Too Deep"
            type_col  = "#ff4d4f"
            type_note = "Correction too deep (>35%) — likely a downtrend, not a base"

        # VCP — volatility contraction across 3 equal sub-periods
        third = base_len // 3
        if third > 5:
            r1 = (close.iloc[best_base_start:best_base_start + third].max() -
                  close.iloc[best_base_start:best_base_start + third].min())
            r2 = (close.iloc[best_base_start + third:best_base_start + 2 * third].max() -
                  close.iloc[best_base_start + third:best_base_start + 2 * third].min())
            r3 = (close.iloc[best_base_start + 2 * third:best_base_end].max() -
                  close.iloc[best_base_start + 2 * third:best_base_end].min())
            is_vcp = r1 > r2 > r3
        else:
            is_vcp = False

        # Volume inside base should contract vs pre-base
        base_vols    = vol.iloc[best_base_start:best_base_end]
        pre_base_avg = vol.iloc[max(0, best_base_start - 20):best_base_start].mean()
        base_avg     = base_vols.mean()
        vol_contraction = pre_base_avg > 0 and base_avg < pre_base_avg * 0.85

        # Prior uptrend (rally before the base)
        if best_base_start >= 20:
            pre_base_low = close.iloc[max(0, best_base_start - 60):best_base_start].min()
            prior_rally  = round((best_base_high - pre_base_low) / pre_base_low * 100, 1)
        else:
            prior_rally  = 0

        # Base number — 1st vs 2nd (2nd in established uptrend is most powerful)
        if best_base_start >= 60:
            pre_segment = close.iloc[max(0, best_base_start - 120):best_base_start]
            pre_hi = pre_segment.max()
            pre_lo = pre_segment.min()
            pre_depth = (pre_hi - pre_lo) / pre_hi * 100
            pre_flat_periods = sum(
                1 for i in range(len(pre_segment) - 5)
                if abs(pre_segment.iloc[i + 5] - pre_segment.iloc[i]) / pre_segment.iloc[i] * 100 < 5
            )
            had_prior_base = pre_depth < 30 and pre_flat_periods > 10
        else:
            had_prior_base = False

        base_number  = "2nd Base (Most Powerful)" if had_prior_base and prior_rally > 20 else "1st Base"
        base_num_col = "#fdd835" if "2nd" in base_number else "#8b949e"

        # Pivot (buy point) = 0.5% above base high
        pivot      = round(best_base_high * 1.005, 2)
        pivot_dist = round((pivot - cur_price) / cur_price * 100, 1)

        # Overall rating from weighted features
        score = 0
        if base_weeks >= 7:   score += 2
        elif base_weeks >= 5: score += 1
        if base_depth <= 15:  score += 2
        elif base_depth <= 25: score += 1
        if vol_contraction:   score += 2
        if is_vcp:            score += 2
        if prior_rally >= 20: score += 1
        if had_prior_base:    score += 2

        if score >= 8:
            rating, rating_col = "⭐⭐⭐ Excellent Base", "#26c940"
        elif score >= 5:
            rating, rating_col = "⭐⭐ Good Base", "#4da6ff"
        elif score >= 3:
            rating, rating_col = "⭐ Developing Base", "#ff9f40"
        else:
            rating, rating_col = "Weak / Unclear Base", "#ff4d4f"

        # base_line — for chart: line between 2 most prominent base highs
        base_line = None
        dates_idx = df.index
        if pivot_dist <= 8:
            try:
                seg_high  = high.iloc[best_base_start:best_base_end]
                top_threshold = best_base_high * 0.985
                top_indices = [i for i in range(len(seg_high)) if seg_high.iloc[i] >= top_threshold]
                if len(top_indices) >= 2:
                    first_top = top_indices[0]
                    last_top  = top_indices[-1]
                    if last_top - first_top >= 10:
                        abs_first = best_base_start + first_top
                        abs_last  = best_base_start + last_top
                        base_line = {
                            "high":  round(best_base_high, 2),
                            "start": str(dates_idx[abs_first])[:10],
                            "end":   str(dates_idx[abs_last])[:10],
                        }
            except Exception:
                pass

        return {
            "found":           True,
            "base_type":       base_type,
            "type_col":        type_col,
            "type_note":       type_note,
            "base_weeks":      base_weeks,
            "base_depth":      base_depth,
            "base_number":     base_number,
            "base_num_col":    base_num_col,
            "is_vcp":          is_vcp,
            "vol_contraction": vol_contraction,
            "prior_rally":     prior_rally,
            "pivot":           pivot,
            "pivot_dist":      pivot_dist,
            "from_pivot":      from_pivot,
            "rating":          rating,
            "rating_col":      rating_col,
            "score":           score,
            "base_line":       base_line,
        }
    except Exception as e:
        return {"found": False, "reason": f"Analysis error: {e}"}
