#!/usr/bin/env python3
"""
anju_ai.tools.concentration — portfolio concentration + pyramiding enforcer.

Audit Finding 3.12: anju-trader runs 44 tiny positions averaging ₹24k each.
Edge gets diluted to zero. Best-trader research is unambiguous: the top
5–15 conviction picks produce 80%+ of returns; positions 20+ are noise.

This module enforces a concentration band (min_open .. max_open) and
allows pyramiding capital into existing HERO positions when a fresh
signal fires on a symbol we already hold strongly.

Used by morning_scan after scoring + ranking candidates, BEFORE writing
signals to memory.db. Phase 2.8 activates this with config defaults:
  min_open = 5
  max_open = 15
  pyramiding_enabled = true (add to HERO when re-signaling)
  correlation_threshold = 0.6 (Phase 2.10 wires correlation cap)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from anju_ai.tools.scoring import ScoreResult


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class OpenPosition(BaseModel):
    """Lightweight view of one open position."""
    symbol:        str
    entry_price:   float
    current_price: float
    qty:           int
    days_held:     int
    pnl_pct:       float
    score_at_entry: float = 0.0
    verdict_at_entry: str = ""


class ConcentrationDecision(BaseModel):
    """One row of the concentration decision per candidate."""
    symbol:         str
    action:         Literal[
        "NEW_OPEN",        # open a new position
        "PYRAMID",         # add to existing position (HERO behaviour)
        "SKIP_DUPLICATE",  # already held — not pyramiding right now
        "SKIP_CAP",        # max_open reached
        "SKIP_BELOW_VERDICT",  # candidate verdict isn't BUY
        "SKIP_SCORE_BELOW",    # candidate score < min_score
    ]
    score:          float
    suggested_qty:  int = 0
    rationale:      str = ""


class ConcentrationConfig(BaseModel):
    """Rules for the enforcer — wired from config/runtime.yaml."""
    min_open:               int = 5
    max_open:               int = 15
    pyramiding_enabled:     bool = True
    pyramid_min_score:      float = 25.0   # don't pyramid weak signals
    pyramid_min_pnl_pct:    float = 5.0    # only pyramid winners
    pyramid_max_qty_pct:    float = 0.5    # add at most 50% of existing qty
    new_min_score:          float = 6.0    # min score for fresh open
    only_buy_verdict:       bool = True    # skip WATCH / AVOID


# ── Public API ────────────────────────────────────────────────────────────────

def enforce_concentration(candidates: list[ScoreResult],
                          open_positions: list[OpenPosition],
                          cfg: ConcentrationConfig,
                          base_qty_fn=None) -> list[ConcentrationDecision]:
    """For each candidate, decide NEW_OPEN / PYRAMID / SKIP_*.

    Args:
        candidates:     ranked highest-score-first list from scoring.
        open_positions: current portfolio holdings (paper or live).
        cfg:            ConcentrationConfig from runtime.yaml.
        base_qty_fn:    callable(candidate) -> int suggested qty for fresh
                        opens. If None, returns suggested_qty=0.

    Returns: one ConcentrationDecision per candidate, in input order.
    """
    if base_qty_fn is None:
        base_qty_fn = lambda r: 0

    open_by_sym = {p.symbol.upper().replace(".NS", ""): p for p in open_positions}
    slots_remaining = max(0, cfg.max_open - len(open_positions))
    decisions: list[ConcentrationDecision] = []

    for r in candidates:
        sym = r.symbol.upper().replace(".NS", "")

        # Filter weak signals first — cheap rejections at top
        if cfg.only_buy_verdict and r.verdict != "BUY":
            decisions.append(ConcentrationDecision(
                symbol=sym, action="SKIP_BELOW_VERDICT", score=r.score,
                rationale=f"Verdict {r.verdict} — not actionable",
            ))
            continue
        if r.score < cfg.new_min_score:
            decisions.append(ConcentrationDecision(
                symbol=sym, action="SKIP_SCORE_BELOW", score=r.score,
                rationale=f"Score {r.score:.1f} < min {cfg.new_min_score}",
            ))
            continue

        # Already held? consider pyramiding
        existing = open_by_sym.get(sym)
        if existing is not None:
            decision = _consider_pyramid(r, existing, cfg)
            decisions.append(decision)
            continue

        # Fresh open — gated by max_open cap
        if slots_remaining <= 0:
            decisions.append(ConcentrationDecision(
                symbol=sym, action="SKIP_CAP", score=r.score,
                rationale=f"Max open ({cfg.max_open}) reached",
            ))
            continue

        qty = base_qty_fn(r)
        decisions.append(ConcentrationDecision(
            symbol=sym, action="NEW_OPEN", score=r.score,
            suggested_qty=qty,
            rationale=f"New high-conviction signal (score {r.score:.1f})",
        ))
        slots_remaining -= 1

    return decisions


def _consider_pyramid(candidate: ScoreResult,
                      existing: OpenPosition,
                      cfg: ConcentrationConfig) -> ConcentrationDecision:
    """Pyramiding rules — add capital to a winning position."""
    if not cfg.pyramiding_enabled:
        return ConcentrationDecision(
            symbol=candidate.symbol, action="SKIP_DUPLICATE",
            score=candidate.score,
            rationale="Already held, pyramiding disabled",
        )
    if candidate.score < cfg.pyramid_min_score:
        return ConcentrationDecision(
            symbol=candidate.symbol, action="SKIP_DUPLICATE",
            score=candidate.score,
            rationale=(f"Re-signal score {candidate.score:.1f} below "
                       f"pyramid threshold {cfg.pyramid_min_score}"),
        )
    if existing.pnl_pct < cfg.pyramid_min_pnl_pct:
        return ConcentrationDecision(
            symbol=candidate.symbol, action="SKIP_DUPLICATE",
            score=candidate.score,
            rationale=(f"Existing position P&L {existing.pnl_pct:+.1f}% "
                       f"below pyramid threshold {cfg.pyramid_min_pnl_pct}%"),
        )
    # Add up to pyramid_max_qty_pct of existing qty
    add_qty = max(1, int(existing.qty * cfg.pyramid_max_qty_pct))
    return ConcentrationDecision(
        symbol=candidate.symbol, action="PYRAMID",
        score=candidate.score, suggested_qty=add_qty,
        rationale=(f"HERO pyramid: re-signal score {candidate.score:.1f}, "
                   f"existing +{existing.pnl_pct:.1f}%, add {add_qty} qty"),
    )


def summarise_decisions(decisions: list[ConcentrationDecision]) -> dict:
    """Aggregate for digest / audit. Returns counts by action."""
    counts: dict[str, int] = {}
    for d in decisions:
        counts[d.action] = counts.get(d.action, 0) + 1
    return {
        "total": len(decisions),
        "by_action": counts,
        "new_opens": [d for d in decisions if d.action == "NEW_OPEN"],
        "pyramids":  [d for d in decisions if d.action == "PYRAMID"],
    }
