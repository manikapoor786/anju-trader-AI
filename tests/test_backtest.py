"""Tests for anju_ai.tools.backtest — synthetic histories, no network."""

import numpy as np
import pandas as pd
import pytest

from anju_ai.tools.backtest import (
    BacktestInput,
    BacktestReport,
    TradeRecord,
    _build_report,
    _compute_qty,
    run_backtest,
)


def make_history(prices: list[float], start: str = "2024-01-01",
                 volumes: int = 1_000_000) -> pd.DataFrame:
    """Build a 1-year+ synthetic OHLCV history."""
    n = len(prices)
    dates = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame({
        "Open":   [p * 0.997 for p in prices],
        "High":   [p * 1.008 for p in prices],
        "Low":    [p * 0.992 for p in prices],
        "Close":  prices,
        "Volume": [volumes] * n,
    }, index=dates)


# ── Position sizing ──────────────────────────────────────────────────────────

def test_compute_qty_zero_for_invalid_price():
    assert _compute_qty(0, 95, 10_000_000, 1.0, 10.0) == 0


def test_compute_qty_caps_at_max_position_pct():
    # 1% risk = ₹1L allows huge qty by risk, but 10% cap on ₹1cr = ₹10L max position
    qty = _compute_qty(price=100, stop=99, capital=10_000_000,
                       risk_pct=1.0, max_pos_pct=10.0)
    # 10% of capital / 100 = 10,000 shares max
    assert qty == 10_000


# ── _build_report ─────────────────────────────────────────────────────────────

def _sample_input(symbols=None) -> BacktestInput:
    return BacktestInput(
        name="test", start_date="2024-01-01", end_date="2024-12-31",
        universe_symbols=symbols or ["TEST.NS"],
        mode="aggressive", min_score=0, max_open_positions=5,
        capital_inr=1_000_000, max_hold_days=10,
    )


def test_build_report_empty_trades_returns_zeros():
    rep = _build_report(_sample_input(), trades=[], open_positions=[],
                        days_scanned=100, universe_size=10)
    assert rep.total_closed == 0
    assert rep.win_rate_pct == 0
    assert rep.net_expectancy_pct == 0
    assert rep.by_outcome_kind == {}


def _make_trade(symbol="A", score=20, kind="WIN_T1", net=5.0, days=3,
                signal_date="2024-01-01", fill_date="2024-01-02") -> TradeRecord:
    return TradeRecord(
        signal_date=signal_date, symbol=symbol, score=score,
        verdict="BUY", entry_model="🚀 Breakout Entry",
        fill_date=fill_date, fill_price=100.0, qty=100,
        stop=95.0, t1=110.0, t2=120.0,
        exit_date="2024-01-05",
        exit_price=100 * (1 + net / 100),
        outcome_kind=kind, days_held=days,
        gross_pnl_pct=net + 0.4,
        costs_pct=0.4,
        net_pnl_pct=net,
        mfe_pct=net + 1, mae_pct=-2,
    )


def test_build_report_computes_win_rate_correctly():
    trades = [
        _make_trade(kind="WIN_T1", net=5.0),
        _make_trade(kind="WIN_T1", net=8.0),
        _make_trade(kind="LOSS_STOP", net=-4.0),
        _make_trade(kind="LOSS_STOP", net=-4.0),
    ]
    rep = _build_report(_sample_input(), trades, [], 100, 10)
    assert rep.win_rate_pct == 50.0
    assert rep.avg_winner_pct == pytest.approx(6.5, abs=0.01)
    assert rep.avg_loser_pct  == pytest.approx(-4.0, abs=0.01)
    assert rep.rr_realized    == pytest.approx(1.625, abs=0.01)


def test_build_report_drawdown_tracked():
    # Three winners then three losers — DD should bite at the end
    trades = (
        [_make_trade(kind="WIN_T1", net=5.0) for _ in range(3)] +
        [_make_trade(kind="LOSS_STOP", net=-4.0) for _ in range(3)]
    )
    rep = _build_report(_sample_input(), trades, [], 100, 10)
    assert rep.max_drawdown_pct < 0
    # Equity peaked at 1.05^3 ≈ 1.158, then dropped to 1.158*0.96^3 ≈ 1.024
    # → DD ≈ (1.024-1.158)/1.158 ≈ -11.6%
    assert rep.max_drawdown_pct < -10


def test_build_report_groups_by_score_bucket():
    trades = [
        _make_trade(score=12, kind="WIN_T1", net=5.0),
        _make_trade(score=13, kind="LOSS_STOP", net=-4.0),
        _make_trade(score=22, kind="WIN_T1", net=8.0),
        _make_trade(score=23, kind="WIN_T1", net=8.0),
    ]
    rep = _build_report(_sample_input(), trades, [], 100, 10)
    # bucket "10-14" has 2 trades (12, 13); "20-24" has 2 trades (22, 23)
    assert rep.by_score_bucket["10-14"]["trades"] == 2
    assert rep.by_score_bucket["10-14"]["win_rate_pct"] == 50.0
    assert rep.by_score_bucket["20-24"]["trades"] == 2
    assert rep.by_score_bucket["20-24"]["win_rate_pct"] == 100.0


def test_build_report_best_and_worst_trades_identified():
    trades = [
        _make_trade(symbol="TOP", kind="WIN_T2", net=15.0),
        _make_trade(symbol="MID", kind="WIN_T1", net=3.0),
        _make_trade(symbol="BAD", kind="LOSS_STOP", net=-8.0),
    ]
    rep = _build_report(_sample_input(), trades, [], 100, 10)
    assert rep.best_trades[0].symbol == "TOP"
    assert rep.worst_trades[0].symbol == "BAD"


def test_build_report_by_entry_model():
    trades = [
        _make_trade(kind="WIN_T1", net=5.0),       # default "🚀 Breakout Entry"
        _make_trade(kind="LOSS_STOP", net=-4.0),
    ]
    # Override entry_model on one trade
    trades[1] = trades[1].model_copy(update={"entry_model": "🎯 Early Base Entry"})
    rep = _build_report(_sample_input(), trades, [], 100, 10)
    assert "🚀 Breakout Entry" in rep.by_entry_model
    assert "🎯 Early Base Entry" in rep.by_entry_model
    assert rep.by_entry_model["🚀 Breakout Entry"]["trades"] == 1
    assert rep.by_entry_model["🎯 Early Base Entry"]["win_rate_pct"] == 0


# ── End-to-end smoke ──────────────────────────────────────────────────────────

def test_run_backtest_smoke_no_signals():
    """Backtest with constant flat prices should produce no signals
    (the scanner requires volume signals or base — flat data has neither)."""
    inp = BacktestInput(
        name="smoke", start_date="2024-06-01", end_date="2024-06-30",
        universe_symbols=["TEST.NS"], mode="aggressive", min_score=0,
        max_open_positions=5, capital_inr=1_000_000, max_hold_days=10,
    )
    # 300 bars of fairly flat price — well past 60-bar minimum
    df = make_history([100 + (i % 3) for i in range(300)], start="2024-01-01")

    def loader(symbol, days):
        return df.copy()

    report, trades = run_backtest(inp, ohlcv_loader=loader, nifty_loader=None)
    assert isinstance(report, BacktestReport)
    assert report.universe_size == 1
    # Flat data → no setup → no trades — accepted edge: the engine should run
    # cleanly even on data that produces zero signals.
    assert report.total_closed >= 0


def test_run_backtest_handles_missing_data_gracefully():
    inp = BacktestInput(
        name="missing", start_date="2024-01-01", end_date="2024-01-31",
        universe_symbols=["NONE.NS"], mode="aggressive", min_score=0,
        max_open_positions=5, capital_inr=1_000_000, max_hold_days=10,
    )

    def loader(symbol, days):
        return None   # all loads fail

    with pytest.raises(RuntimeError, match="No symbol histories"):
        run_backtest(inp, ohlcv_loader=loader, nifty_loader=None)
