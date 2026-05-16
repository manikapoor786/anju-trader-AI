#!/usr/bin/env python3
"""
anju_ai.tools.setups — setup type classification + per-setup parameters.

Phase 3.0: shift from a single scoring formula to a setup-aware system.
Real winners from the user's portfolio came from FIVE distinct setups:

  Stock       Setup type          Why it fits
  ─────────   ─────────────────   ────────────────────────────────────
  MODEFENCE   BREAKOUT            Tight base, near 52wh, defence wave
  SAIL        BREAKOUT            Clean breakout near 52wh, metals wave
  HFCL        MOMENTUM            Already +33% above MA50, kept running
  HINDZINC    COMEBACK            -19.7% from 52wh, volume 2.23x spike
  BOMDYEING   CONTRARIAN          -33% from 52wh, sector rotation
  (no example) EARLY_BASE         In tight base, volume dryup, pre-B/O

One scoring formula can't capture all five. This module classifies which
setup a stock represents at signal-day; scoring.py then applies
setup-specific weights and gates.

Empirical observation from multi-bagger study (2026-04-24 entries):
  Setup           Volume>1.5x avg     Tight base       Near 52wh
  BREAKOUT        Often (not always)  Yes              Yes
  MOMENTUM        No (quiet vol)      No (extended)    Yes
  COMEBACK        Yes (always)        No               No (-15-25% off)
  CONTRARIAN      Yes (rotation in)   No (basing)      No (-25-40% off)
  EARLY_BASE      No (dryup)          Yes              No (any)

→ ONE volume rule, ONE base-tightness rule, ONE 52wh-distance rule
  cannot fit all five. Setup-aware classification is essential.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel


SetupType = Literal[
    "BREAKOUT",      # Tight base completing → volume B/O near 52wh
    "MOMENTUM",      # Already extended, but trend still strong, theme intact
    "COMEBACK",      # Recovered above MA50 from drawdown, fresh volume spike
    "CONTRARIAN",    # Sector turn, well off 52wh, basing
    "EARLY_BASE",    # In tight base, volume drying up — pre-breakout
]


# Setup-specific scoring parameters. score_signal() applies these.
class SetupParams(BaseModel):
    """Tunable parameters per setup type."""
    name: SetupType
    # T1 distance gate (in fraction, e.g. 0.03 = +3%)
    t1_min_dist: float
    t1_max_dist: float
    # Required MA structure
    require_above_ma20: bool = True
    require_above_ma50: bool = True
    require_above_ma200: bool = False
    # Volume requirement (0 = no requirement; otherwise multiple of 20DMA)
    min_vol_ratio: float = 0.0
    # 52-week-high distance bounds (e.g. (-0.05, 0.0) = within 5% of 52wh)
    near_52wh_min: float | None = None
    near_52wh_max: float | None = None
    # Base tightness max (last 30 days range as % — None = no requirement)
    max_30d_range: float | None = None
    # Exhaustion handling: should we PENALISE extended price?
    apply_exhaustion_penalty: bool = True
    # Sector wave bonus (additional bonus for top-3 sector)
    sector_wave_bonus: int = 5


SETUP_PARAMS: dict[SetupType, SetupParams] = {
    "BREAKOUT": SetupParams(
        name="BREAKOUT",
        t1_min_dist=0.03, t1_max_dist=0.10,
        require_above_ma20=True, require_above_ma50=True,
        min_vol_ratio=1.0,
        near_52wh_min=-0.10, near_52wh_max=0.05,
        max_30d_range=20.0,
        apply_exhaustion_penalty=True,
        sector_wave_bonus=5,
    ),
    "MOMENTUM": SetupParams(
        name="MOMENTUM",
        # Momentum already moved — accept closer T1 (squeeze plays) and
        # don't require near-52wh (already at/above).
        t1_min_dist=0.02, t1_max_dist=0.08,
        require_above_ma20=True, require_above_ma50=True,
        # Volume can be quiet — momentum stays alive without big vol
        min_vol_ratio=0.0,
        near_52wh_min=-0.10, near_52wh_max=0.20,
        # No tight base — by definition, MOMENTUM is extended.
        max_30d_range=None,
        # Don't penalise extension — that's the WHOLE point of MOMENTUM.
        apply_exhaustion_penalty=False,
        # Larger bonus when sector theme aligns — that's what keeps
        # extended trends running.
        sector_wave_bonus=8,
    ),
    "COMEBACK": SetupParams(
        name="COMEBACK",
        # Comeback usually has more upside room — wider T1 OK.
        t1_min_dist=0.04, t1_max_dist=0.15,
        require_above_ma20=True, require_above_ma50=True,
        # MANDATORY volume spike — this is what defines the setup.
        min_vol_ratio=1.5,
        # 15-30% off 52wh = comeback zone
        near_52wh_min=-0.30, near_52wh_max=-0.05,
        max_30d_range=None,
        apply_exhaustion_penalty=False,
        sector_wave_bonus=6,
    ),
    "CONTRARIAN": SetupParams(
        name="CONTRARIAN",
        # Contrarian = bigger expected move, wider T1 range
        t1_min_dist=0.05, t1_max_dist=0.20,
        # MA20 reclaim acceptable; MA50 may still be far above price
        require_above_ma20=True, require_above_ma50=False,
        min_vol_ratio=1.2,
        near_52wh_min=-0.50, near_52wh_max=-0.20,
        max_30d_range=None,
        apply_exhaustion_penalty=False,
        # Sector wave is THE main trigger for contrarian — high weight
        sector_wave_bonus=10,
    ),
    "EARLY_BASE": SetupParams(
        name="EARLY_BASE",
        # Pre-breakout — T1 distance modest
        t1_min_dist=0.03, t1_max_dist=0.08,
        require_above_ma20=True, require_above_ma50=True,
        # Volume DRYUP — anti-spike (low recent vol = good)
        min_vol_ratio=0.0,
        near_52wh_min=-0.15, near_52wh_max=0.0,
        max_30d_range=15.0,
        apply_exhaustion_penalty=True,
        sector_wave_bonus=4,
    ),
}


class SetupFeatures(BaseModel):
    """Computed features used by classify_setup()."""
    above_ma20: bool
    above_ma50: bool
    above_ma200: bool
    dist_from_52wh_pct: float   # -100..0 (always negative or 0)
    vol_ratio_20d: float        # today_vol / avg_20d
    range_30d_pct: float        # tightness of last 30d (lower = tighter)
    pct_above_ma50: float       # extension from MA50
    base_quality: int           # base detector score (0-10)
    has_breakout_vol: bool      # breakout volume signal in last 5 days
    has_dryup: bool             # volume dryup signal


def compute_features(df: pd.DataFrame, base_data: dict | None = None,
                     vol_signals: list[dict] | None = None) -> SetupFeatures | None:
    """Compute the feature vector used for setup classification.

    Returns None if insufficient history (< 60 bars)."""
    if df is None or len(df) < 60:
        return None

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    vol = df["Volume"].astype(float)

    try:
        cur = float(close.iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = (
            float(close.rolling(200).mean().iloc[-1])
            if len(close) >= 200 else cur  # fallback: treat as "above"
        )
        wk52 = float(high.iloc[-min(252, len(high)):].max())
        vol_today = float(vol.iloc[-1])
        avg_vol_20 = float(vol.iloc[-20:].mean()) if len(vol) >= 20 else vol_today
        last30 = close.iloc[-30:] if len(close) >= 30 else close
        range_30 = float((last30.max() - last30.min()) / max(last30.min(), 0.01) * 100)
    except Exception:
        return None

    if pd.isna(ma50) or pd.isna(ma20):
        return None

    base_score = int(base_data.get("score", 0)) if base_data else 0

    vol_sig_names = {s.get("name", "") for s in (vol_signals or [])}
    has_breakout_vol = "🚀 Breakout Volume" in vol_sig_names
    has_dryup = "💧 Volume Dry-Up" in vol_sig_names

    return SetupFeatures(
        above_ma20=(cur > ma20),
        above_ma50=(cur > ma50),
        above_ma200=(cur > ma200),
        dist_from_52wh_pct=round((cur - wk52) / wk52 * 100, 2),
        vol_ratio_20d=round(vol_today / max(avg_vol_20, 1), 2),
        range_30d_pct=round(range_30, 2),
        pct_above_ma50=round((cur - ma50) / ma50 * 100, 2),
        base_quality=base_score,
        has_breakout_vol=has_breakout_vol,
        has_dryup=has_dryup,
    )


def classify_setup(features: SetupFeatures) -> SetupType | None:
    """Return the setup type for the given features, or None if no setup fits.

    Decision tree (in priority order — the FIRST match wins). Rules
    refined from real-data validation on the user's 2026-04-24 portfolio
    entries (HFCL, MODEFENCE, HINDZINC, BOMDYEING, SAIL):

      1. EARLY_BASE: tight 30d range + dryup + near 52wh + good base
      2. BREAKOUT: above MA20/50 + within 10% of 52wh + (vol signal OR
                   vol_ratio >= 1.3x as breakout-vol proxy)
      3. MOMENTUM: above MA20/50 + extended (≥10% above MA50) + near 52wh
      4. COMEBACK: above MA50 + 5-30% off 52wh + vol spike ≥1.5x
      5. CONTRARIAN: above MA20 + 20-50% off 52wh (no vol requirement —
                     deep value plays often start on quiet days)
      6. None: doesn't fit any tradeable setup
    """
    # Universal requirement: must be above MA20 for any long setup
    if not features.above_ma20:
        return None

    # EARLY_BASE: tight base + dryup + near 52wh (pre-breakout)
    if (features.has_dryup
            and features.range_30d_pct <= 15
            and features.dist_from_52wh_pct >= -15
            and features.above_ma50
            and features.base_quality >= 5):
        return "EARLY_BASE"

    # BREAKOUT: near 52wh + above MAs + (breakout vol signal OR vol ratio
    # proxy). The vol-ratio proxy catches setups where anju-AI's volume
    # signal pipeline hasn't fired but the day's volume is materially up.
    if (features.above_ma50
            and -10 <= features.dist_from_52wh_pct <= 5
            and (features.has_breakout_vol or features.vol_ratio_20d >= 1.3)):
        return "BREAKOUT"

    # MOMENTUM: extended above MA50, still in uptrend, near highs.
    # Threshold ≥10% above MA50 (was 15% — SAIL at +11.9% would have
    # failed; relaxed after real-data validation).
    if (features.above_ma50
            and features.pct_above_ma50 >= 10
            and features.dist_from_52wh_pct >= -10):
        return "MOMENTUM"

    # COMEBACK: recovered above MA50, 5-30% off 52wh, fresh volume spike
    if (features.above_ma50
            and -30 <= features.dist_from_52wh_pct <= -5
            and features.vol_ratio_20d >= 1.5):
        return "COMEBACK"

    # CONTRARIAN: above MA20 (MA50 NOT required — these stocks are still
    # below MA50 typically), deep off 52wh. Volume optional — turnaround
    # plays often start on quiet accumulation days.
    if (features.above_ma20
            and -50 <= features.dist_from_52wh_pct <= -20):
        return "CONTRARIAN"

    return None
