"""Tests for anju_ai.tools.tax_aware — pure function, deterministic."""

from datetime import datetime, timedelta
import pytest

from anju_ai.tools.tax_aware import (
    LTCG_EXEMPTION_INR,
    TaxDecisionInput,
    TaxDecisionOutput,
    evaluate_tax_decision,
)


# ── Helper to build inputs at a known days-held offset ───────────────────────

def _input(days_held: int, profit_per_share: float = 50.0, qty: int = 100,
           reason: str = "TIME_EXIT") -> TaxDecisionInput:
    today_d = datetime(2026, 5, 14)
    fill_d = today_d - timedelta(days=days_held)
    fill_price = 100.0
    current = fill_price + profit_per_share
    return TaxDecisionInput(
        symbol="X", fill_date=fill_d.strftime("%Y-%m-%d"),
        fill_price=fill_price, qty=qty, current_price=current,
        proposed_exit_reason=reason,
        today="2026-05-14",
    )


# ── STOP / TARGET overrides ──────────────────────────────────────────────────

def test_stop_exit_never_defers_even_near_ltcg():
    inp = _input(days_held=350, profit_per_share=200, reason="STOP")
    out = evaluate_tax_decision(inp)
    assert out.action == "EXIT_NOW"
    assert "Risk discipline" in out.rationale


def test_target_exit_never_defers():
    inp = _input(days_held=350, profit_per_share=200, reason="TARGET")
    out = evaluate_tax_decision(inp)
    assert out.action == "EXIT_NOW"


# ── Already-LTCG passes through ──────────────────────────────────────────────

def test_already_ltcg_returns_no_impact():
    inp = _input(days_held=400, profit_per_share=200)
    out = evaluate_tax_decision(inp)
    assert out.action == "NO_IMPACT"
    assert "Already past 365d" in out.rationale


# ── In deferral window + profitable + meaningful saving → DEFER ──────────────

def test_in_window_profitable_meaningful_saving_defers():
    # 100 shares × ₹2000 profit = ₹200k → STCG ₹40k, LTCG (on 100k after
    # exemption) ₹12.5k → saving ~₹27.5k (well above ₹1k threshold)
    inp = _input(days_held=340, profit_per_share=2000, qty=100)
    out = evaluate_tax_decision(inp)
    assert out.action == "DEFER_FOR_LTCG"
    assert out.days_to_ltcg == 25
    assert out.tax_saved_by_deferring_inr > 1000
    assert "save" in out.rationale.lower()


def test_in_window_profitable_but_below_threshold_exits():
    # Tiny profit → STCG-LTCG saving < ₹1k → not worth deferring
    inp = _input(days_held=340, profit_per_share=10, qty=10)
    out = evaluate_tax_decision(inp)
    assert out.action == "EXIT_NOW"
    assert "below ₹1k threshold" in out.rationale


def test_in_window_underwater_exits():
    """Loss-making position has zero tax benefit from deferring."""
    inp = _input(days_held=340, profit_per_share=-50, qty=100)
    out = evaluate_tax_decision(inp)
    assert out.action == "EXIT_NOW"
    assert "underwater" in out.rationale.lower()


def test_outside_window_exits_normally():
    inp = _input(days_held=200, profit_per_share=500, qty=100)
    out = evaluate_tax_decision(inp)
    assert out.action == "EXIT_NOW"
    assert "not in deferral window" in out.rationale


# ── Boundary cases ───────────────────────────────────────────────────────────

def test_exactly_at_window_start_defers():
    # 365 - 30 = 335 days held — exactly at the edge
    inp = _input(days_held=335, profit_per_share=2000, qty=100)
    out = evaluate_tax_decision(inp)
    assert out.action == "DEFER_FOR_LTCG"


def test_exactly_at_threshold_defers_when_winning():
    # 365 days exactly — still <= LTCG, defer if winner
    inp = _input(days_held=365, profit_per_share=2000, qty=100)
    out = evaluate_tax_decision(inp)
    # At exactly 365, days_to_ltcg = 0 → still in window
    assert out.action == "DEFER_FOR_LTCG"


def test_one_day_past_threshold_is_no_impact():
    inp = _input(days_held=366, profit_per_share=2000, qty=100)
    out = evaluate_tax_decision(inp)
    assert out.action == "NO_IMPACT"


# ── Tax math sanity ──────────────────────────────────────────────────────────

def test_stcg_tax_is_20pct_of_gain():
    inp = _input(days_held=200, profit_per_share=100, qty=100)  # ₹10k gain
    out = evaluate_tax_decision(inp)
    assert out.stcg_tax_inr == pytest.approx(2000.0)   # 20% of 10k


def test_ltcg_tax_respects_exemption():
    # ₹150k gain — above ₹1L exemption by ₹50k → 12.5% × 50k = ₹6.25k
    inp = _input(days_held=200, profit_per_share=1500, qty=100)
    out = evaluate_tax_decision(inp)
    assert out.ltcg_tax_inr == pytest.approx(6250.0)


def test_ltcg_tax_zero_when_gain_under_exemption():
    inp = _input(days_held=200, profit_per_share=500, qty=100)  # ₹50k < ₹1L
    out = evaluate_tax_decision(inp)
    assert out.ltcg_tax_inr == 0.0


# ── Invalid input ────────────────────────────────────────────────────────────

def test_invalid_fill_date_returns_no_impact():
    inp = TaxDecisionInput(
        symbol="X", fill_date="not-a-date", fill_price=100,
        qty=10, current_price=120,
        proposed_exit_reason="TIME_EXIT", today="2026-05-14",
    )
    out = evaluate_tax_decision(inp)
    assert out.action == "NO_IMPACT"
    assert "parse fill_date" in out.rationale
