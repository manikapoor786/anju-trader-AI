"""Tests for anju_ai.tools.correlation — pure function, deterministic data."""

import numpy as np
import pandas as pd
import pytest

from anju_ai.tools.correlation import (
    CorrelationInput,
    CorrelationResult,
    aligned_corr,
    compute_correlation_penalty,
    correlation_matrix,
    daily_returns,
    portfolio_concentration_score,
)


def _series(values: list[float], start: str = "2026-01-01") -> pd.Series:
    return pd.Series(values, index=pd.bdate_range(start=start, periods=len(values)))


# ── daily_returns ────────────────────────────────────────────────────────────

def test_daily_returns_drops_first_nan():
    s = _series([100, 105, 110, 108])
    r = daily_returns(s)
    assert len(r) == 3
    assert r.iloc[0] == pytest.approx(0.05, abs=1e-3)


# ── aligned_corr ─────────────────────────────────────────────────────────────

def test_aligned_corr_perfect():
    """Two identical series → correlation = 1."""
    np.random.seed(0)
    base = _series(list(np.random.randn(30)))
    assert aligned_corr(base, base) == pytest.approx(1.0)


def test_aligned_corr_perfect_negative():
    np.random.seed(0)
    a = _series(list(np.random.randn(30)))
    b = _series([-x for x in a.tolist()])
    assert aligned_corr(a, b) == pytest.approx(-1.0)


def test_aligned_corr_independent_near_zero():
    """Random independent series should produce small correlation."""
    np.random.seed(42)
    a = _series(list(np.random.randn(60)))
    np.random.seed(99)
    b = _series(list(np.random.randn(60)))
    c = aligned_corr(a, b)
    assert c is not None
    assert abs(c) < 0.3


def test_aligned_corr_returns_none_when_too_short():
    a = _series([1, 2, 3])
    b = _series([4, 5, 6])
    assert aligned_corr(a, b, min_periods=20) is None


def test_aligned_corr_returns_none_when_no_overlap():
    a = pd.Series([1, 2, 3], index=pd.bdate_range("2026-01-01", periods=3))
    b = pd.Series([4, 5, 6], index=pd.bdate_range("2026-06-01", periods=3))
    assert aligned_corr(a, b, min_periods=2) is None


# ── compute_correlation_penalty ──────────────────────────────────────────────

def test_no_open_positions_returns_full_size():
    inp = CorrelationInput(
        candidate_symbol="X",
        candidate_returns=_series(list(np.random.randn(30))),
        open_position_returns={},
    )
    out = compute_correlation_penalty(inp)
    assert out.size_multiplier == 1.0
    assert out.effective_corr == 0.0
    assert out.n_positions_considered == 0


def test_uncorrelated_positions_no_penalty():
    np.random.seed(1)
    cand = _series(list(np.random.randn(50)))
    np.random.seed(2)
    pos_a = _series(list(np.random.randn(50)))
    np.random.seed(3)
    pos_b = _series(list(np.random.randn(50)))

    inp = CorrelationInput(
        candidate_symbol="X",
        candidate_returns=cand,
        open_position_returns={"A": pos_a, "B": pos_b},
        correlation_threshold=0.6,
    )
    out = compute_correlation_penalty(inp)
    assert out.size_multiplier == 1.0
    assert out.effective_corr < 0.3


def test_highly_correlated_position_triggers_penalty():
    np.random.seed(1)
    base = _series(list(np.random.randn(50)))
    # Two positions identical to candidate → effective_corr=1.0
    inp = CorrelationInput(
        candidate_symbol="X",
        candidate_returns=base,
        open_position_returns={"A": base.copy(), "B": base.copy()},
        correlation_threshold=0.6,
        penalty_strength=1.0,
    )
    out = compute_correlation_penalty(inp)
    assert out.effective_corr == pytest.approx(1.0)
    # excess = 0.4, scaled = 1.0, multiplier = 1 - 1 = 0
    # but floor at 0.1
    assert out.size_multiplier == 0.1


def test_partial_correlation_partial_penalty():
    """Candidate correlated 0.8 with one position → moderate penalty."""
    np.random.seed(1)
    base = _series(list(np.random.randn(50)))
    # Create a series correlated ~0.8 with base
    np.random.seed(99)
    noise = pd.Series(np.random.randn(50) * 0.6,
                      index=base.index)
    similar = (base * 0.8 + noise).rename("similar")

    inp = CorrelationInput(
        candidate_symbol="X",
        candidate_returns=base,
        open_position_returns={"A": similar},
        correlation_threshold=0.6,
        penalty_strength=1.0,
    )
    out = compute_correlation_penalty(inp)
    assert out.effective_corr > 0.6
    assert 0.1 <= out.size_multiplier < 1.0


def test_penalty_strength_zero_disables_penalty():
    np.random.seed(1)
    base = _series(list(np.random.randn(50)))
    inp = CorrelationInput(
        candidate_symbol="X",
        candidate_returns=base,
        open_position_returns={"A": base.copy()},
        correlation_threshold=0.6,
        penalty_strength=0.0,   # disabled
    )
    out = compute_correlation_penalty(inp)
    # effective_corr is 1.0 but penalty_strength=0 means scaled*0 = 0
    # multiplier = 1 - 0 = 1.0
    assert out.size_multiplier == 1.0


def test_insufficient_overlap_no_consideration():
    np.random.seed(1)
    cand = _series(list(np.random.randn(50)))
    short = _series(list(np.random.randn(5)))   # too short

    inp = CorrelationInput(
        candidate_symbol="X",
        candidate_returns=cand,
        open_position_returns={"A": short},
        min_periods=20,
    )
    out = compute_correlation_penalty(inp)
    assert out.n_positions_considered == 0
    assert out.size_multiplier == 1.0


# ── correlation_matrix ───────────────────────────────────────────────────────

def test_correlation_matrix_returns_none_too_few_symbols():
    closes = {"A": _series([100, 101, 102, 103])}
    assert correlation_matrix(closes) is None


def test_correlation_matrix_computes_pairwise():
    np.random.seed(1)
    a = _series(list(100 + np.cumsum(np.random.randn(60))))
    b = _series(list(100 + np.cumsum(np.random.randn(60))))
    c = _series(list(100 + np.cumsum(np.random.randn(60))))
    m = correlation_matrix({"A": a, "B": b, "C": c}, min_periods=20)
    assert m is not None
    assert m.shape == (3, 3)
    assert m.loc["A", "A"] == pytest.approx(1.0)


# ── portfolio_concentration_score ────────────────────────────────────────────

def test_concentration_score_zero_when_uncorrelated():
    # Build a 3x3 identity correlation matrix (off-diagonal ≈ 0)
    m = pd.DataFrame([[1.0, 0.0, 0.0],
                       [0.0, 1.0, 0.0],
                       [0.0, 0.0, 1.0]],
                      index=["A", "B", "C"], columns=["A", "B", "C"])
    assert portfolio_concentration_score(m) == 0.0


def test_concentration_score_high_when_all_correlated():
    m = pd.DataFrame([[1.0, 0.9, 0.95],
                       [0.9, 1.0, 0.92],
                       [0.95, 0.92, 1.0]],
                      index=["A", "B", "C"], columns=["A", "B", "C"])
    score = portfolio_concentration_score(m)
    assert score > 0.9


def test_concentration_score_handles_none():
    assert portfolio_concentration_score(None) == 0.0


def test_concentration_score_handles_single_symbol():
    m = pd.DataFrame([[1.0]], index=["A"], columns=["A"])
    assert portfolio_concentration_score(m) == 0.0
