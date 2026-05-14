"""Tests for anju_ai.loops.intraday_monitor — pure functions, no network."""

from datetime import datetime, timezone, timedelta
import pytest

from anju_ai.loops.intraday_monitor import (
    classify_alert,
    is_market_open,
    _days_between,
)


# ── is_market_open ───────────────────────────────────────────────────────────

IST = timezone(timedelta(hours=5, minutes=30))


def test_market_open_during_trading_hours():
    assert is_market_open(datetime(2026, 5, 14, 10, 30, tzinfo=IST))   # Thu
    assert is_market_open(datetime(2026, 5, 14, 9, 15, tzinfo=IST))    # open
    assert is_market_open(datetime(2026, 5, 14, 15, 30, tzinfo=IST))   # close


def test_market_closed_before_open():
    assert not is_market_open(datetime(2026, 5, 14, 9, 14, tzinfo=IST))


def test_market_closed_after_close():
    assert not is_market_open(datetime(2026, 5, 14, 15, 31, tzinfo=IST))


def test_market_closed_weekend():
    assert not is_market_open(datetime(2026, 5, 16, 10, 0, tzinfo=IST))  # Sat
    assert not is_market_open(datetime(2026, 5, 17, 10, 0, tzinfo=IST))  # Sun


# ── classify_alert ───────────────────────────────────────────────────────────

def test_stop_hit_returns_critical():
    out = classify_alert(entry=100, stop=95, t1=110, t2=120,
                         current=94.5, days_held=3, max_hold_days=90)
    assert out is not None
    severity, summary = out
    assert severity == "CRITICAL"
    assert "Stop hit" in summary


def test_t2_hit_returns_info():
    out = classify_alert(entry=100, stop=95, t1=110, t2=120,
                         current=121, days_held=10, max_hold_days=90)
    assert out is not None
    severity, summary = out
    assert severity == "INFO"
    assert "T2 hit" in summary


def test_t1_hit_returns_info():
    out = classify_alert(entry=100, stop=95, t1=110, t2=120,
                         current=110.5, days_held=5, max_hold_days=90)
    assert out is not None
    severity, summary = out
    assert severity == "INFO"
    assert "T1 hit" in summary


def test_stale_position_returns_warn():
    out = classify_alert(entry=100, stop=95, t1=110, t2=120,
                         current=101, days_held=95, max_hold_days=90)
    assert out is not None
    severity, summary = out
    assert severity == "WARN"
    assert "max" in summary.lower()


def test_normal_position_no_alert():
    out = classify_alert(entry=100, stop=95, t1=110, t2=120,
                         current=103, days_held=10, max_hold_days=90)
    assert out is None


def test_invalid_inputs_no_alert():
    assert classify_alert(entry=0, stop=None, t1=None, t2=None,
                          current=100, days_held=1, max_hold_days=90) is None
    assert classify_alert(entry=100, stop=None, t1=None, t2=None,
                          current=None, days_held=1, max_hold_days=90) is None


def test_stop_priority_over_target_in_same_check():
    """If somehow current price is both <= stop AND >= t1 (impossible in
    reality but defensive), stop wins because it's checked first."""
    out = classify_alert(entry=100, stop=120, t1=110, t2=130,
                         current=109, days_held=5, max_hold_days=90)
    # current (109) < stop (120) → STOP hit
    severity, summary = out
    assert severity == "CRITICAL"


# ── _days_between ────────────────────────────────────────────────────────────

def test_days_between_basic():
    now = datetime(2026, 5, 14, tzinfo=IST)
    assert _days_between("2026-05-14", now) == 0
    assert _days_between("2026-05-04", now) == 10
    assert _days_between("2026-04-14", now) == 30


def test_days_between_invalid_date_returns_zero():
    now = datetime(2026, 5, 14, tzinfo=IST)
    assert _days_between("invalid", now) == 0
    assert _days_between(None, now) == 0
