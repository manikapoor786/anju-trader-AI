"""Tests for anju_ai.tools.paper_fill — pure function, no I/O."""

import pandas as pd
import pytest

from anju_ai.tools.paper_fill import (
    FillInput,
    FillResult,
    classify_segment,
    simulate_fill,
)


# ── simulate_fill ─────────────────────────────────────────────────────────────

def test_simulate_fill_buy_slips_up():
    dates = pd.bdate_range(start="2026-01-02", periods=3)
    df = pd.DataFrame({
        "Open":   [100.0, 101.0, 102.0],
        "High":   [101.0, 102.0, 103.0],
        "Low":    [99.0,  100.0, 101.0],
        "Close":  [100.5, 101.5, 102.5],
        "Volume": [1_000_000] * 3,
    }, index=dates)

    out = simulate_fill(FillInput(
        symbol="TEST", signal_date="2026-01-01",
        intended_price=100.0, qty=100, side="BUY",
        df_post_signal=df, segment="largecap",
        base_slippage_pct=0.10,
    ))

    assert out.is_filled
    assert out.fill_price > 100.0   # BUY slips up
    assert out.slippage_pct >= 0.10
    assert out.slippage_inr > 0


def test_simulate_fill_sell_slips_down():
    df = pd.DataFrame({
        "Open":   [100.0],
        "High":   [101.0],
        "Low":    [99.0],
        "Close":  [100.5],
        "Volume": [1_000_000],
    }, index=pd.bdate_range(start="2026-01-02", periods=1))

    out = simulate_fill(FillInput(
        symbol="TEST", signal_date="2026-01-01",
        intended_price=100.0, qty=100, side="SELL",
        df_post_signal=df, segment="largecap",
        base_slippage_pct=0.10,
    ))

    assert out.is_filled
    assert out.fill_price < 100.0   # SELL slips down
    assert out.slippage_inr > 0


def test_simulate_fill_no_data_unfilled():
    out = simulate_fill(FillInput(
        symbol="TEST", signal_date="2026-01-01",
        intended_price=100.0, qty=100, side="BUY",
        df_post_signal=pd.DataFrame(),
    ))
    assert not out.is_filled
    assert "No post-signal data" in (out.rejection_reason or "")


def test_simulate_fill_smallcap_higher_slippage():
    df = pd.DataFrame({
        "Open":   [100.0],
        "High":   [101.0],
        "Low":    [99.0],
        "Close":  [100.5],
        "Volume": [50_000],
    }, index=pd.bdate_range(start="2026-01-02", periods=1))

    largecap = simulate_fill(FillInput(
        symbol="L", signal_date="2026-01-01", intended_price=100.0, qty=100,
        side="BUY", df_post_signal=df, segment="largecap",
        base_slippage_pct=0.05,
    ))
    smallcap = simulate_fill(FillInput(
        symbol="S", signal_date="2026-01-01", intended_price=100.0, qty=100,
        side="BUY", df_post_signal=df, segment="smallcap",
        base_slippage_pct=0.35,
    ))
    assert smallcap.slippage_pct > largecap.slippage_pct


def test_simulate_fill_size_impact_scales_slippage():
    df = pd.DataFrame({
        "Open":   [100.0],
        "High":   [101.0],
        "Low":    [99.0],
        "Close":  [100.5],
        "Volume": [10_000],   # tiny ADV
    }, index=pd.bdate_range(start="2026-01-02", periods=1))

    tiny = simulate_fill(FillInput(
        symbol="T", signal_date="2026-01-01", intended_price=100.0, qty=10,
        side="BUY", df_post_signal=df, base_slippage_pct=0.10,
        avg_volume_10d=10_000,
    ))
    huge = simulate_fill(FillInput(
        symbol="H", signal_date="2026-01-01", intended_price=100.0, qty=5000,
        side="BUY", df_post_signal=df, base_slippage_pct=0.10,
        avg_volume_10d=10_000,
    ))
    # Trading 5000 shares of a 10k-vol stock is enormous — slippage must reflect
    assert huge.slippage_pct > tiny.slippage_pct


# ── classify_segment ──────────────────────────────────────────────────────────

def test_classify_segment_by_volume():
    assert classify_segment(100, 10_000_000) == "largecap"
    assert classify_segment(100, 2_000_000) == "midcap"
    assert classify_segment(100, 500_000) == "smallcap"
    assert classify_segment(100, None) == "smallcap"   # unknown defaults conservative
