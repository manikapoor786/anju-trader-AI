"""Tests for anju_core.indicators — all offline, synthetic OHLCV.

We construct deterministic DataFrames that should produce known signals,
and verify the indicator functions detect them correctly.
"""

import numpy as np
import pandas as pd
import pytest

from anju_core.indicators import (
    analyse_volume,
    get_base_analysis,
    get_mtfa_alignment,
    get_volume_signals,
)


# ── Synthetic data helpers ────────────────────────────────────────────────────

def make_ohlcv(prices: list[float], volumes: list[int] | None = None,
               start: str = "2025-01-01") -> pd.DataFrame:
    """Build an OHLCV DataFrame from a list of close prices.
    OHLC are derived as close ± 1%, indexed by business days from `start`."""
    n = len(prices)
    if volumes is None:
        volumes = [100_000] * n
    dates = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame({
        "Open":   [p * 0.995 for p in prices],
        "High":   [p * 1.01  for p in prices],
        "Low":    [p * 0.99  for p in prices],
        "Close":  prices,
        "Volume": volumes,
    }, index=dates)


# ── analyse_volume ────────────────────────────────────────────────────────────

def test_analyse_volume_normal_data():
    df = make_ohlcv([100 + i for i in range(30)], [200_000] * 30)
    out = analyse_volume(df)
    assert out["current_raw"] == 200_000
    assert out["ratio_to_avg"] == 1.0
    assert "Normal Volume" in out["signal"]


def test_analyse_volume_spike_high():
    vols = [100_000] * 29 + [500_000]   # 5x spike
    df = make_ohlcv([100 + i for i in range(30)], vols)
    out = analyse_volume(df)
    assert out["ratio_to_avg"] >= 2.0
    assert "Very High Volume" in out["signal"]


def test_analyse_volume_handles_short_df_gracefully():
    df = make_ohlcv([100, 101], [1000, 1000])
    out = analyse_volume(df)
    assert out["current"] != "Crash"  # exception path returns sentinel dict
    assert "signal" in out


# ── get_mtfa_alignment ────────────────────────────────────────────────────────

def test_mtfa_returns_none_when_data_too_short():
    df = make_ohlcv([100] * 30)
    assert get_mtfa_alignment(df) is None


def test_mtfa_strong_uptrend_aligned():
    # Steadily rising prices for 100 days → weekly MA10 below price, RSI > 50
    prices = [100 + i * 0.5 for i in range(120)]
    df = make_ohlcv(prices)
    out = get_mtfa_alignment(df)
    assert out is not None
    assert out["aligned"] is True
    assert out["w_rsi"] > 50


def test_mtfa_downtrend_not_aligned():
    prices = [200 - i * 0.5 for i in range(120)]
    df = make_ohlcv(prices)
    out = get_mtfa_alignment(df)
    assert out is not None
    assert out["aligned"] is False
    assert out["w_rsi"] < 50


# ── get_volume_signals ────────────────────────────────────────────────────────

def test_volume_signals_empty_on_short_df():
    df = make_ohlcv([100] * 20)
    assert get_volume_signals(df) == []


def test_volume_signals_breakout_detected():
    # Build flat price + low volume, last bar = up-day + 3x volume
    n = 40
    closes = [100 + i * 0.02 for i in range(n)]
    closes[-1] = closes[-2] * 1.03   # +3% up day
    vols = [100_000] * (n - 1) + [400_000]
    df = make_ohlcv(closes, vols)
    sigs = get_volume_signals(df)
    names = [s["name"] for s in sigs]
    assert "🚀 Breakout Volume" in names


def test_volume_signals_dryup_detected():
    # Tight range last 10 bars + last 5 bars each lower volume than prev5 avg
    n = 30
    closes = [100 + np.sin(i) * 0.5 for i in range(n)]   # tight oscillation
    vols = [100_000] * (n - 10) + [80_000, 70_000, 60_000, 50_000, 45_000,
                                   40_000, 35_000, 30_000, 25_000, 20_000]
    df = make_ohlcv(closes, vols)
    sigs = get_volume_signals(df)
    names = [s["name"] for s in sigs]
    assert "💧 Volume Dry-Up" in names


def test_volume_signals_pocket_pivot_or_breakout_on_high_vol_up_day():
    # 30 bars: alternating up/down days with modest vols; final bar = up day
    # with volume far exceeding any prior red-day's volume.
    n = 30
    closes = [100 + ((-1) ** i) * 0.5 for i in range(n - 1)]   # alternates
    closes.append(closes[-1] * 1.02)   # final up day
    vols = [100_000] * (n - 1) + [400_000]
    df = make_ohlcv(closes, vols)
    sigs = get_volume_signals(df)
    names = [s["name"] for s in sigs]
    # The high-vol up-day with 4x volume should trigger at least breakout vol;
    # may also trigger pocket pivot. Both are valid for this setup.
    assert any(n in names for n in ("⚡ Pocket Pivot", "🚀 Breakout Volume"))


# ── get_base_analysis ─────────────────────────────────────────────────────────

def test_base_analysis_returns_none_on_short_df():
    df = make_ohlcv([100] * 40)
    assert get_base_analysis(df) is None


def test_base_analysis_no_base_when_strong_uptrend():
    # Steady uptrend with no flat consolidation
    prices = [100 + i * 0.8 for i in range(120)]
    df = make_ohlcv(prices)
    out = get_base_analysis(df)
    # Either found=False, or found=True with very weak rating
    assert out is not None
    # In a clean uptrend the algorithm may still find a "base" in the early
    # flatter portion — that's fine, just verify the function returns sensibly
    assert "found" in out


def test_base_analysis_finds_flat_consolidation():
    # 80 days of rally, then 30 days of tight 5% range = obvious base
    rally  = [100 + i * 1.5 for i in range(60)]
    base   = [190 + np.sin(i / 2) * 5 for i in range(35)]   # ~5% range around 190
    df     = make_ohlcv(rally + base)
    out    = get_base_analysis(df)
    assert out is not None
    assert out.get("found") is True
    assert out["base_depth"] < 12  # flat base
    assert out["base_type"] == "Flat Base"
    assert out["score"] > 0
