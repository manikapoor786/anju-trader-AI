"""Tests for anju_ai.tools.concentration — pure function, no I/O."""

import pytest

from anju_ai.tools.concentration import (
    ConcentrationConfig,
    ConcentrationDecision,
    OpenPosition,
    enforce_concentration,
    summarise_decisions,
)
from anju_ai.tools.scoring import ScoreResult


def _r(symbol: str, score: float, verdict: str = "BUY") -> ScoreResult:
    return ScoreResult(
        symbol=symbol, price=100.0, change_pct=1.0,
        score=score, verdict=verdict, reasoning="x",
    )


def _p(symbol: str, pnl_pct: float = 5.0, qty: int = 100,
       score_at_entry: float = 20.0) -> OpenPosition:
    return OpenPosition(
        symbol=symbol, entry_price=100.0, current_price=100 * (1 + pnl_pct/100),
        qty=qty, days_held=10, pnl_pct=pnl_pct,
        score_at_entry=score_at_entry, verdict_at_entry="BUY",
    )


def _qty_5(r): return 50  # mock sizing


# ── Fresh opens, cap behaviour ───────────────────────────────────────────────

def test_new_opens_under_cap():
    cands = [_r("A", 20), _r("B", 15), _r("C", 12)]
    cfg = ConcentrationConfig(min_open=2, max_open=5)
    out = enforce_concentration(cands, open_positions=[], cfg=cfg, base_qty_fn=_qty_5)
    actions = [d.action for d in out]
    assert actions == ["NEW_OPEN", "NEW_OPEN", "NEW_OPEN"]
    assert all(d.suggested_qty == 50 for d in out)


def test_max_open_cap_enforced():
    cands = [_r("D", 25), _r("E", 18)]
    open_pos = [_p("A"), _p("B"), _p("C")]
    cfg = ConcentrationConfig(min_open=1, max_open=3)   # 3 slots, all taken
    out = enforce_concentration(cands, open_pos, cfg, _qty_5)
    assert all(d.action == "SKIP_CAP" for d in out)


def test_partial_cap_takes_highest_score_first():
    """Candidates already sorted desc by score (scoring engine guarantee).
    Only first one fits within remaining slots."""
    cands = [_r("HIGH", 25), _r("MED", 15), _r("LOW", 8)]
    open_pos = [_p("X"), _p("Y")]
    cfg = ConcentrationConfig(min_open=1, max_open=3)   # 1 slot left
    out = enforce_concentration(cands, open_pos, cfg, _qty_5)
    assert out[0].action == "NEW_OPEN"
    assert out[0].symbol == "HIGH"
    assert all(d.action == "SKIP_CAP" for d in out[1:])


# ── Score / verdict gates ────────────────────────────────────────────────────

def test_only_buy_verdict_skips_watch_and_avoid():
    cands = [_r("A", 20, "BUY"), _r("B", 12, "WATCH"), _r("C", 6, "AVOID")]
    cfg = ConcentrationConfig(min_open=1, max_open=10, only_buy_verdict=True)
    out = enforce_concentration(cands, [], cfg, _qty_5)
    assert out[0].action == "NEW_OPEN"
    assert out[1].action == "SKIP_BELOW_VERDICT"
    assert out[2].action == "SKIP_BELOW_VERDICT"


def test_min_score_filter():
    cands = [_r("A", 8, "BUY"), _r("B", 4, "BUY")]
    cfg = ConcentrationConfig(min_open=1, max_open=10, new_min_score=6)
    out = enforce_concentration(cands, [], cfg, _qty_5)
    assert out[0].action == "NEW_OPEN"
    assert out[1].action == "SKIP_SCORE_BELOW"


# ── Pyramiding ───────────────────────────────────────────────────────────────

def test_pyramid_into_winning_position_when_re_signaled():
    cands = [_r("A", 28, "BUY")]
    open_pos = [_p("A", pnl_pct=10.0, qty=100)]
    cfg = ConcentrationConfig(
        min_open=1, max_open=10, pyramiding_enabled=True,
        pyramid_min_score=25.0, pyramid_min_pnl_pct=5.0,
        pyramid_max_qty_pct=0.5,
    )
    out = enforce_concentration(cands, open_pos, cfg, _qty_5)
    assert out[0].action == "PYRAMID"
    # add up to 50% of existing 100 → 50
    assert out[0].suggested_qty == 50


def test_no_pyramid_when_below_pyramid_score():
    cands = [_r("A", 18, "BUY")]
    open_pos = [_p("A", pnl_pct=8.0, qty=100)]
    cfg = ConcentrationConfig(
        min_open=1, max_open=10, pyramiding_enabled=True,
        pyramid_min_score=25.0,
    )
    out = enforce_concentration(cands, open_pos, cfg, _qty_5)
    assert out[0].action == "SKIP_DUPLICATE"
    assert "below pyramid threshold" in out[0].rationale


def test_no_pyramid_when_position_underwater():
    cands = [_r("A", 28, "BUY")]
    open_pos = [_p("A", pnl_pct=-3.0, qty=100)]
    cfg = ConcentrationConfig(
        min_open=1, max_open=10, pyramiding_enabled=True,
        pyramid_min_score=25.0, pyramid_min_pnl_pct=5.0,
    )
    out = enforce_concentration(cands, open_pos, cfg, _qty_5)
    assert out[0].action == "SKIP_DUPLICATE"
    assert "below pyramid threshold" in out[0].rationale


def test_pyramiding_disabled_means_skip_duplicates():
    cands = [_r("A", 28, "BUY")]
    open_pos = [_p("A", pnl_pct=15.0)]
    cfg = ConcentrationConfig(pyramiding_enabled=False, min_open=1, max_open=10)
    out = enforce_concentration(cands, open_pos, cfg, _qty_5)
    assert out[0].action == "SKIP_DUPLICATE"


def test_pyramid_qty_caps_at_pyramid_max_qty_pct():
    cands = [_r("A", 30, "BUY")]
    open_pos = [_p("A", pnl_pct=20.0, qty=200)]
    cfg = ConcentrationConfig(
        min_open=1, max_open=10, pyramiding_enabled=True,
        pyramid_min_score=25.0, pyramid_min_pnl_pct=5.0,
        pyramid_max_qty_pct=0.25,   # quarter of 200 = 50
    )
    out = enforce_concentration(cands, open_pos, cfg, _qty_5)
    assert out[0].action == "PYRAMID"
    assert out[0].suggested_qty == 50


def test_pyramid_qty_minimum_one_share():
    cands = [_r("A", 30, "BUY")]
    open_pos = [_p("A", pnl_pct=20.0, qty=1)]   # tiny existing position
    cfg = ConcentrationConfig(
        min_open=1, max_open=10, pyramiding_enabled=True,
        pyramid_min_score=25.0, pyramid_max_qty_pct=0.1,   # 10% of 1 = 0
    )
    out = enforce_concentration(cands, open_pos, cfg, _qty_5)
    assert out[0].suggested_qty == 1


# ── Symbol matching with .NS suffix ──────────────────────────────────────────

def test_symbol_matching_handles_ns_suffix():
    """ScoreResult.symbol is stripped; open positions may have either form."""
    cands = [_r("A", 28, "BUY")]
    # OpenPosition.symbol might come in as "A.NS"
    open_pos = [OpenPosition(symbol="A.NS", entry_price=100, current_price=110,
                              qty=100, days_held=5, pnl_pct=10)]
    cfg = ConcentrationConfig(
        min_open=1, max_open=10, pyramiding_enabled=True,
        pyramid_min_score=25.0, pyramid_min_pnl_pct=5.0,
    )
    out = enforce_concentration(cands, open_pos, cfg, _qty_5)
    assert out[0].action == "PYRAMID"   # matched despite suffix


# ── summarise ────────────────────────────────────────────────────────────────

def test_summarise_decisions_counts_actions():
    decisions = [
        ConcentrationDecision(symbol="A", action="NEW_OPEN", score=20),
        ConcentrationDecision(symbol="B", action="NEW_OPEN", score=18),
        ConcentrationDecision(symbol="C", action="PYRAMID", score=25),
        ConcentrationDecision(symbol="D", action="SKIP_CAP", score=15),
    ]
    s = summarise_decisions(decisions)
    assert s["total"] == 4
    assert s["by_action"]["NEW_OPEN"] == 2
    assert s["by_action"]["PYRAMID"] == 1
    assert s["by_action"]["SKIP_CAP"] == 1
    assert len(s["new_opens"]) == 2
    assert len(s["pyramids"]) == 1
