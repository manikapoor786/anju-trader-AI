#!/usr/bin/env python3
"""
anju_ai.tools.tax_aware — Indian capital-gains-aware exit logic.

Audit Finding (Section 5, item 15): when a position is within 30 days
of its 365-day mark AND profitable, holding past the mark cuts the tax
on the gain from 20% STCG to 12.5% LTCG above the ₹1L exemption. On
sizeable winners this is real money.

The system can't avoid all exits — stops still hit, drawdowns happen —
but for time-exit or weak-signal exits where we have flexibility, this
module recommends deferring if the tax math justifies it.

Indian tax (FY2024 baseline, will be re-calibrated on Budget changes):
  STCG (≤365d):   20% on gains
  LTCG (>365d):   12.5% on gains above ₹1L per year
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field


# ── Tax constants (FY2024) ───────────────────────────────────────────────────

LTCG_THRESHOLD_DAYS = 365
LTCG_RATE_PCT       = 12.5
STCG_RATE_PCT       = 20.0
LTCG_EXEMPTION_INR  = 100_000


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class TaxDecisionInput(BaseModel):
    symbol:           str
    fill_date:        str        # 'YYYY-MM-DD'
    fill_price:       float
    qty:              int
    current_price:    float
    proposed_exit_reason: Literal["TIME_EXIT", "WEAK_SIGNAL", "STOP", "TARGET", "MANUAL"]
    today:            str = ""   # if blank, uses now
    defer_window_days: int = 30  # only defer if within N days of LTCG


class TaxDecisionOutput(BaseModel):
    """Recommendation: exit now, defer, or no-tax-impact."""
    action:               Literal["EXIT_NOW", "DEFER_FOR_LTCG", "NO_IMPACT"]
    days_held:            int
    days_to_ltcg:         int                  # negative if past 365d
    gross_pnl_inr:        float
    stcg_tax_inr:         float
    ltcg_tax_inr:         float                # if we wait + ₹1L exemption is fresh
    tax_saved_by_deferring_inr: float
    rationale:            str


# ── Core function ────────────────────────────────────────────────────────────

def evaluate_tax_decision(inp: TaxDecisionInput) -> TaxDecisionOutput:
    """Compute whether to defer or exit. Decision rules:

      1. If exit is for STOP or TARGET → EXIT_NOW. Tax tail does NOT
         override risk discipline.
      2. If days_held already > 365 → NO_IMPACT (LTCG already applies).
      3. If days_held >= (365 - defer_window_days) AND position is
         profitable AND tax saved is meaningful → DEFER_FOR_LTCG.
      4. Otherwise EXIT_NOW.
    """
    # Parse dates
    try:
        fill = datetime.strptime(inp.fill_date, "%Y-%m-%d")
    except ValueError:
        return _no_impact(0, 0, "Could not parse fill_date")
    if inp.today:
        try:
            today = datetime.strptime(inp.today, "%Y-%m-%d")
        except ValueError:
            today = datetime.now(timezone.utc)
    else:
        today = datetime.now(timezone.utc)
        today = today.replace(tzinfo=None)
    fill = fill.replace(tzinfo=None)

    days_held = (today - fill).days
    days_to_ltcg = LTCG_THRESHOLD_DAYS - days_held

    gross_pnl = (inp.current_price - inp.fill_price) * inp.qty

    # Compute tax under both regimes
    stcg_tax = max(0.0, gross_pnl) * STCG_RATE_PCT / 100
    taxable_ltcg = max(0.0, gross_pnl - LTCG_EXEMPTION_INR)
    ltcg_tax = taxable_ltcg * LTCG_RATE_PCT / 100
    tax_saved = max(0.0, stcg_tax - ltcg_tax)

    # Rule 1: STOP / TARGET overrides tax — never defer a discipline exit
    if inp.proposed_exit_reason in ("STOP", "TARGET"):
        return TaxDecisionOutput(
            action="EXIT_NOW", days_held=days_held, days_to_ltcg=days_to_ltcg,
            gross_pnl_inr=round(gross_pnl, 2),
            stcg_tax_inr=round(stcg_tax, 2), ltcg_tax_inr=round(ltcg_tax, 2),
            tax_saved_by_deferring_inr=round(tax_saved, 2),
            rationale=("Risk discipline overrides tax: "
                       f"{inp.proposed_exit_reason} exits never defer"),
        )

    # Rule 2: already LTCG
    if days_held > LTCG_THRESHOLD_DAYS:
        return _no_impact(days_held, days_to_ltcg,
                          "Already past 365d — LTCG already applies; "
                          "no benefit from deferring")

    # Rule 3: in the deferral window with a real winner
    in_window = 0 <= days_to_ltcg <= inp.defer_window_days
    profitable = gross_pnl > 0
    meaningful_saving = tax_saved >= 1000   # at least ₹1k saved

    if in_window and profitable and meaningful_saving:
        return TaxDecisionOutput(
            action="DEFER_FOR_LTCG", days_held=days_held,
            days_to_ltcg=days_to_ltcg,
            gross_pnl_inr=round(gross_pnl, 2),
            stcg_tax_inr=round(stcg_tax, 2), ltcg_tax_inr=round(ltcg_tax, 2),
            tax_saved_by_deferring_inr=round(tax_saved, 2),
            rationale=(f"Within {days_to_ltcg}d of LTCG threshold. "
                       f"Hold to save ₹{round(tax_saved):,} tax "
                       f"(STCG ₹{round(stcg_tax):,} → LTCG ₹{round(ltcg_tax):,})."),
        )

    # Rule 4: default exit
    if not in_window:
        why = f"Days held {days_held} not in deferral window (need {LTCG_THRESHOLD_DAYS - inp.defer_window_days}–{LTCG_THRESHOLD_DAYS})"
    elif not profitable:
        why = f"Position underwater (₹{round(gross_pnl):,}); no tax benefit from deferring"
    else:
        why = f"Tax saving ₹{round(tax_saved):,} below ₹1k threshold; not worth the risk"

    return TaxDecisionOutput(
        action="EXIT_NOW", days_held=days_held, days_to_ltcg=days_to_ltcg,
        gross_pnl_inr=round(gross_pnl, 2),
        stcg_tax_inr=round(stcg_tax, 2), ltcg_tax_inr=round(ltcg_tax, 2),
        tax_saved_by_deferring_inr=round(tax_saved, 2),
        rationale=why,
    )


def _no_impact(days_held: int, days_to_ltcg: int, why: str) -> TaxDecisionOutput:
    return TaxDecisionOutput(
        action="NO_IMPACT", days_held=days_held, days_to_ltcg=days_to_ltcg,
        gross_pnl_inr=0.0, stcg_tax_inr=0.0, ltcg_tax_inr=0.0,
        tax_saved_by_deferring_inr=0.0, rationale=why,
    )
