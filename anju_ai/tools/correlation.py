#!/usr/bin/env python3
"""
anju_ai.tools.correlation — penalise sizing when new signal is highly
correlated with open positions.

Why: if 10 of your 15 positions are bank stocks, you don't have 15
positions — you have a leveraged bet on Indian banking. Diversification
benefit goes to zero. Adding a 16th bank doesn't reduce risk; it
concentrates it. A new candidate's effective position size should be
scaled down by how much it correlates with what you already hold.

Used by morning_scan AFTER concentration filters and BEFORE final qty
calculation. Phase 2.10 ships the engine but defaults to penalty=0 in
runtime.yaml until backtest validates that correlation-aware sizing
adds expectancy.

Math (simple, proven approach):
  - Compute 30-day returns correlation matrix among (candidate, open_positions)
  - effective_corr = mean of |corr(candidate, each open position)|
  - size_multiplier = 1 - clip(effective_corr - threshold, 0, 1)
  - When effective_corr = 0.6 (threshold), multiplier = 1.0 (no penalty)
  - When effective_corr = 1.0,                multiplier = 0.6 (-40%)
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class CorrelationInput(BaseModel):
    """All data needed to compute correlation-aware sizing for one candidate."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    candidate_symbol:    str
    candidate_returns:   pd.Series         # daily % returns (last 30+ days)
    open_position_returns: dict[str, pd.Series]   # symbol → returns series
    correlation_threshold: float = 0.6     # below this → no penalty
    penalty_strength:    float = 1.0       # 0 = no effect; 1 = full
    min_periods:         int = 20          # need at least N overlapping bars


class CorrelationResult(BaseModel):
    """Output of compute_correlation_penalty."""
    effective_corr:      float       # avg |corr| with open positions
    size_multiplier:     float       # in [0.1, 1.0]
    n_positions_considered: int      # how many had enough overlap
    detail:              dict        # per-symbol correlations


# ── Helpers ──────────────────────────────────────────────────────────────────

def daily_returns(close: pd.Series) -> pd.Series:
    """Convert a Close series into daily pct returns, dropping NaN."""
    return close.pct_change().dropna()


def aligned_corr(a: pd.Series, b: pd.Series,
                 min_periods: int = 20) -> float | None:
    """Pairwise correlation on the aligned overlap. Returns None if not
    enough common dates."""
    if a is None or b is None or len(a) == 0 or len(b) == 0:
        return None
    try:
        # Align by index (date) and drop missing pairs
        joined = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner")
        joined = joined.dropna()
        if len(joined) < min_periods:
            return None
        c = joined["a"].corr(joined["b"])
        if pd.isna(c):
            return None
        return float(c)
    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def compute_correlation_penalty(inp: CorrelationInput) -> CorrelationResult:
    """Compute the size multiplier for a candidate given open positions.

    Returns multiplier in [0.1, 1.0]. Hard floor at 0.1 (never shrink to
    zero — at least 10% of base size is always allowed if the signal passed
    all other filters)."""
    if not inp.open_position_returns:
        return CorrelationResult(
            effective_corr=0.0, size_multiplier=1.0,
            n_positions_considered=0, detail={},
        )

    corrs: dict[str, float] = {}
    for sym, ret in inp.open_position_returns.items():
        c = aligned_corr(inp.candidate_returns, ret, min_periods=inp.min_periods)
        if c is not None:
            corrs[sym] = c

    if not corrs:
        return CorrelationResult(
            effective_corr=0.0, size_multiplier=1.0,
            n_positions_considered=0, detail={},
        )

    abs_corrs = [abs(c) for c in corrs.values()]
    effective = float(np.mean(abs_corrs))

    if effective <= inp.correlation_threshold:
        multiplier = 1.0
    else:
        # Linear penalty above threshold
        excess = effective - inp.correlation_threshold
        # excess in [0, 1 - threshold] → scale to [0, 1]
        scaled = excess / max(1.0 - inp.correlation_threshold, 1e-6)
        multiplier = max(0.1, 1.0 - scaled * inp.penalty_strength)

    return CorrelationResult(
        effective_corr=round(effective, 4),
        size_multiplier=round(multiplier, 4),
        n_positions_considered=len(corrs),
        detail={k: round(v, 4) for k, v in corrs.items()},
    )


def correlation_matrix(closes_by_symbol: dict[str, pd.Series],
                       min_periods: int = 20) -> pd.DataFrame | None:
    """Full pairwise correlation matrix among symbols. Used by anomaly_qa
    + the Sunday A/B-compare report to spot over-concentration in a
    particular sector pattern.

    Returns None if fewer than 2 symbols or all overlaps insufficient."""
    if len(closes_by_symbol) < 2:
        return None
    rets = {sym: daily_returns(c) for sym, c in closes_by_symbol.items()
            if c is not None and len(c) >= min_periods}
    if len(rets) < 2:
        return None
    df = pd.DataFrame(rets).dropna(how="all")
    if len(df) < min_periods:
        return None
    return df.corr(min_periods=min_periods)


def portfolio_concentration_score(corr_matrix: pd.DataFrame) -> float:
    """Single 0-1 score: mean of |upper-triangle correlations|.
    0 = perfectly uncorrelated (max diversification)
    1 = identical positions (no diversification)
    A healthy portfolio sits below 0.5."""
    if corr_matrix is None or corr_matrix.empty:
        return 0.0
    n = len(corr_matrix)
    if n < 2:
        return 0.0
    arr = corr_matrix.values
    # Upper triangle, excluding diagonal
    upper = []
    for i in range(n):
        for j in range(i + 1, n):
            v = arr[i, j]
            if not np.isnan(v):
                upper.append(abs(v))
    return float(np.mean(upper)) if upper else 0.0
