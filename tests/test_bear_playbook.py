"""Tests for anju_ai.tools.bear_playbook — synthetic OHLCV, no network."""

import numpy as np
import pandas as pd
import pytest

from anju_ai.tools.bear_playbook import (
    BearPick,
    BearPlaybook,
    DEFENSIVE_LONG_UNIVERSE,
    build_playbook,
    render_telegram,
    score_defensive_long,
    score_short_candidate,
)


def _df(closes: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    n = len(closes)
    dates = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame({
        "Open": closes, "High": [c*1.01 for c in closes],
        "Low": [c*0.99 for c in closes], "Close": closes,
        "Volume": [1_000_000] * n,
    }, index=dates)


# ── Universe sanity ──────────────────────────────────────────────────────────

def test_defensive_universe_has_curated_symbols():
    assert len(DEFENSIVE_LONG_UNIVERSE) >= 15
    assert "HINDUNILVR.NS" in DEFENSIVE_LONG_UNIVERSE
    assert "SUNPHARMA.NS" in DEFENSIVE_LONG_UNIVERSE


# ── score_defensive_long ─────────────────────────────────────────────────────

def test_defensive_long_strong_rs_above_ma200_picks():
    # Stock compounding ~0.5%/day → 20d return ~10.5%, Nifty flat → RS ~+10%
    df = _df([100 * (1.005 ** i) for i in range(250)])
    nifty = pd.Series([22000] * 250,
                       index=pd.bdate_range(start="2025-01-15", periods=250))
    pick = score_defensive_long("X.NS", df, nifty)
    assert pick is not None
    assert pick.side == "LONG_DEFENSIVE"
    assert pick.above_ma200
    assert pick.rs_diff_20d > 8   # ~10% in practice


def test_defensive_long_weak_rs_returns_none():
    # Sideways stock with no RS
    df = _df([100] * 250)
    nifty = pd.Series([22000 + i for i in range(250)],
                       index=pd.bdate_range(start="2025-01-15", periods=250))
    pick = score_defensive_long("X.NS", df, nifty)
    # RS is heavily negative, score=0 → rejected
    assert pick is None


def test_defensive_long_handles_missing_data():
    assert score_defensive_long("X.NS", None, None) is None
    assert score_defensive_long("X.NS", pd.DataFrame(), None) is None


# ── score_short_candidate ────────────────────────────────────────────────────

def test_short_candidate_requires_fno_eligibility():
    df = _df([100 - i*0.5 for i in range(50)])   # downtrend
    pick = score_short_candidate("X.NS", df, None, fno_eligible=False)
    assert pick is None   # not F&O → no short


def test_short_candidate_weak_rs_below_ma200():
    # Compounding ~-0.5%/day → 20d return ~-9.5%
    df = _df([1000 * (0.995 ** i) for i in range(250)])
    nifty = pd.Series([22000] * 250,
                       index=pd.bdate_range(start="2025-01-15", periods=250))
    pick = score_short_candidate("X.NS", df, nifty, fno_eligible=True)
    assert pick is not None
    assert pick.side == "SHORT_FNO"
    assert not pick.above_ma200
    assert pick.rs_diff_20d < -5


def test_short_candidate_strong_uptrend_rejected():
    """A name that's rising shouldn't be a short candidate even in Bear."""
    df = _df([100 * (1.005 ** i) for i in range(250)])
    nifty = pd.Series([22000] * 250,
                       index=pd.bdate_range(start="2025-01-15", periods=250))
    pick = score_short_candidate("X.NS", df, nifty, fno_eligible=True)
    assert pick is None


# ── build_playbook ───────────────────────────────────────────────────────────

def test_build_playbook_non_bear_returns_empty():
    pb = build_playbook("Trending", {}, {}, None, enabled=True)
    assert pb.regime == "Trending"
    assert pb.long_picks == []
    assert pb.short_picks == []
    assert "Not Bear" in pb.notes


def test_build_playbook_disabled_returns_empty():
    pb = build_playbook("Bear", {}, {}, None, enabled=False)
    assert pb.regime == "Bear"
    assert pb.long_picks == []
    assert "disabled" in pb.notes


def test_build_playbook_bear_with_defensive_longs():
    # Two defensive symbols, both outperforming
    nifty = pd.Series([22000] * 250,
                       index=pd.bdate_range(start="2025-01-15", periods=250))
    dfs = {
        "A.NS": _df([100 * (1.006 ** i) for i in range(250)]),
        "B.NS": _df([100 * (1.004 ** i) for i in range(250)]),
    }
    pb = build_playbook("Bear", dfs, {}, nifty, enabled=True)
    assert pb.regime == "Bear"
    assert len(pb.long_picks) == 2
    # Highest-score first
    assert pb.long_picks[0].score >= pb.long_picks[1].score


def test_build_playbook_caps_long_exposure_at_30pct():
    # 20 defensive picks at 2% each = 40% → must be scaled down to 30%
    nifty = pd.Series([22000] * 250,
                       index=pd.bdate_range(start="2025-01-15", periods=250))
    dfs = {f"S{i}.NS": _df([100 * (1.005 ** j) for j in range(250)])
           for i in range(20)}
    pb = build_playbook("Bear", dfs, {}, nifty, enabled=True, max_long_picks=20)
    long_total = sum(p.suggested_qty_pct for p in pb.long_picks)
    assert long_total <= 30.0 + 0.5   # floating-point slack


def test_build_playbook_includes_shorts_when_fno_eligible():
    nifty = pd.Series([22000] * 250,
                       index=pd.bdate_range(start="2025-01-15", periods=250))
    short_dfs = {
        "WEAK.NS":  _df([1000 * (0.995 ** i) for i in range(250)]),
        "WEAK2.NS": _df([1000 * (0.996 ** i) for i in range(250)]),
    }
    pb = build_playbook("Bear", {}, short_dfs, nifty, enabled=True,
                        fno_eligible_set={"WEAK", "WEAK2"})
    assert len(pb.short_picks) >= 1


def test_build_playbook_excludes_shorts_when_not_fno():
    nifty = pd.Series([22000] * 250,
                       index=pd.bdate_range(start="2025-01-15", periods=250))
    short_dfs = {"WEAK.NS": _df([1000 * (0.995 ** i) for i in range(250)])}
    pb = build_playbook("Bear", {}, short_dfs, nifty, enabled=True,
                        fno_eligible_set=set())   # no F&O eligibility
    assert pb.short_picks == []


# ── render_telegram ──────────────────────────────────────────────────────────

def test_render_telegram_empty_for_non_bear():
    pb = BearPlaybook(regime="Trending", notes="x")
    assert render_telegram(pb) == ""


def test_render_telegram_includes_longs_and_shorts():
    pb = BearPlaybook(
        regime="Bear", notes="3 picks",
        long_picks=[BearPick(symbol="HINDUNILVR", side="LONG_DEFENSIVE",
                              score=10, rationale="strong RS",
                              rs_diff_20d=8, above_ma200=True,
                              suggested_qty_pct=2.0)],
        short_picks=[BearPick(symbol="WEAK", side="SHORT_FNO",
                                score=7, rationale="below MA200",
                                rs_diff_20d=-12, suggested_qty_pct=1.5)],
        max_net_long_pct=2.0, cash_pct=96.5,
    )
    msg = render_telegram(pb)
    assert "Bear Playbook" in msg
    assert "HINDUNILVR" in msg
    assert "WEAK" in msg
    assert "🟢" in msg
    assert "🔴" in msg
