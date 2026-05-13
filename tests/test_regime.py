"""Tests for anju_core.regime — pure classification logic only (no network)."""

from anju_core.regime import STATES, _classify


# ── State definitions integrity ───────────────────────────────────────────────

def test_all_four_states_defined():
    assert set(STATES.keys()) == {"Trending", "Sideways", "Volatile", "Bear"}


def test_each_state_has_required_fields():
    for name, meta in STATES.items():
        assert {"emoji", "scanner_mode", "min_score", "recommendations"} <= meta.keys()
        assert meta["scanner_mode"] in {"strict", "aggressive"}
        assert isinstance(meta["min_score"], int)
        assert 1 <= meta["min_score"] <= 10
        assert len(meta["recommendations"]) >= 1


def test_min_score_ordering_matches_risk_tolerance():
    # Bear must be tightest, Sideways must be loosest (per design)
    assert STATES["Bear"]["min_score"]     >= STATES["Volatile"]["min_score"]
    assert STATES["Volatile"]["min_score"] >= STATES["Trending"]["min_score"]
    assert STATES["Trending"]["min_score"] >= STATES["Sideways"]["min_score"]


# ── _classify — bear paths ────────────────────────────────────────────────────

def test_classify_bear_below_ma200():
    nifty = {"price": 19000, "ma20": 19200, "ma50": 19500, "ma200": 20000,
             "vol_10d_pct": 3.0, "above_ma200": False, "above_ma50": False}
    state, _ = _classify(nifty, {"breadth_pct": 50})
    assert state == "Bear"


def test_classify_bear_high_vol_below_ma200():
    nifty = {"price": 18000, "ma200": 20000, "vol_10d_pct": 7.0,
             "above_ma200": False}
    state, label = _classify(nifty, {"breadth_pct": 30})
    assert state == "Bear"
    assert "High Volatility" in label


# ── _classify — volatile path ─────────────────────────────────────────────────

def test_classify_volatile_above_ma200_high_vol():
    nifty = {"price": 20500, "ma20": 20300, "ma50": 20100, "ma200": 19500,
             "vol_10d_pct": 6.0, "above_ma200": True, "above_ma50": True,
             "ma20_rising": True}
    state, _ = _classify(nifty, {"breadth_pct": 60})
    assert state == "Volatile"


# ── _classify — trending path ─────────────────────────────────────────────────

def test_classify_trending_perfect_alignment():
    nifty = {"price": 21000, "ma20": 20800, "ma50": 20500, "ma200": 19800,
             "vol_10d_pct": 2.0, "above_ma200": True, "above_ma50": True,
             "ma20_rising": True}
    state, label = _classify(nifty, {"breadth_pct": 70})
    assert state == "Trending"
    assert "Strong" in label  # vol < 2.5 → Strong uptrend variant


def test_classify_trending_healthy_but_not_strong():
    nifty = {"price": 21000, "ma20": 20800, "ma50": 20500, "ma200": 19800,
             "vol_10d_pct": 3.5, "above_ma200": True, "above_ma50": True,
             "ma20_rising": True}
    state, label = _classify(nifty, {"breadth_pct": 60})
    assert state == "Trending"
    assert "Strong" not in label  # vol >= 2.5 → no Strong variant


def test_classify_not_trending_when_breadth_narrow():
    nifty = {"price": 21000, "ma20": 20800, "ma50": 20500, "ma200": 19800,
             "vol_10d_pct": 2.0, "above_ma200": True, "above_ma50": True,
             "ma20_rising": True}
    # Breadth below 55 → kicks out of Trending
    state, _ = _classify(nifty, {"breadth_pct": 40})
    assert state == "Sideways"


def test_classify_not_trending_when_ma20_falling():
    nifty = {"price": 21000, "ma20": 20800, "ma50": 20500, "ma200": 19800,
             "vol_10d_pct": 2.0, "above_ma200": True, "above_ma50": True,
             "ma20_rising": False}
    state, _ = _classify(nifty, {"breadth_pct": 70})
    assert state != "Trending"


# ── _classify — sideways paths ────────────────────────────────────────────────

def test_classify_sideways_pullback_between_ma50_ma200():
    nifty = {"price": 19900, "ma20": 20100, "ma50": 20300, "ma200": 19500,
             "vol_10d_pct": 3.0, "above_ma200": True, "above_ma50": False,
             "ma20_rising": False}
    state, label = _classify(nifty, {"breadth_pct": 50})
    assert state == "Sideways"
    assert "Pullback" in label


def test_classify_sideways_default_no_directional_edge():
    nifty = {"price": 20500, "ma20": 20400, "ma50": 20300, "ma200": 19800,
             "vol_10d_pct": 3.0, "above_ma200": True, "above_ma50": True,
             "ma20_rising": True}
    # Above all MAs but only borderline breadth, not strict Trending
    state, _ = _classify(nifty, {"breadth_pct": 50})  # < 55 fails Trending gate
    assert state == "Sideways"
