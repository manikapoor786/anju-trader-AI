"""Phase 3.0: setup classifier tests.

Validates that the classifier correctly categorises charts matching each
of the five setup archetypes. Uses synthetic OHLCV data — no network."""

import pandas as pd
import pytest

from anju_ai.tools.setups import (
    SETUP_PARAMS,
    SetupFeatures,
    classify_setup,
    compute_features,
)


def _make_history(price_path: list[float],
                  vol_path: list[int] | None = None,
                  start: str = "2024-01-01") -> pd.DataFrame:
    """Synthesise OHLCV from a close-price path."""
    n = len(price_path)
    vols = vol_path or [1_000_000] * n
    dates = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame({
        "Open":   [p * 0.998 for p in price_path],
        "High":   [p * 1.008 for p in price_path],
        "Low":    [p * 0.992 for p in price_path],
        "Close":  price_path,
        "Volume": vols,
    }, index=dates)


def test_classifier_returns_none_when_below_ma20():
    """UNIONBANK case: stock below MA20 must not classify as anything."""
    # 200 bars of downtrend ending below MA20
    prices = [100 * (1 - i * 0.002) for i in range(200)]  # ends at 60
    df = _make_history(prices)
    feats = compute_features(df)
    assert feats is not None
    assert not feats.above_ma20
    assert classify_setup(feats) is None


def test_classifier_identifies_momentum():
    """HFCL case: extended above MA50, near 52wh — MOMENTUM.

    Construct: 150-bar flat base at 100 + 50-bar sharp rise to 140.
    This puts current price ~30%+ above the long MA50 average."""
    base = [100 + (i % 3) * 0.2 for i in range(150)]
    rise = [100 + (i + 1) * 0.8 for i in range(50)]  # → 140
    df = _make_history(base + rise)
    feats = compute_features(df)
    assert feats is not None
    assert feats.above_ma50
    assert feats.pct_above_ma50 > 10, f"pct_above_ma50={feats.pct_above_ma50}"
    assert classify_setup(feats) == "MOMENTUM"


def test_classifier_identifies_comeback_with_vol_spike():
    """HINDZINC case: above MA50, 15-20% off 52wh, fresh vol spike."""
    # Climb to 130, drop to 100, recover to ~108. Final-day vol = 2.2x avg.
    rise = [100 + i * 0.5 for i in range(60)]      # 100 → 130
    fall = [130 - (i + 1) * 0.5 for i in range(40)]   # 130 → 110
    base = [110 - (i % 5) * 0.2 for i in range(40)]   # ~108-110
    recover = [108 + i * 0.05 for i in range(40)]  # 108 → 110
    prices = rise + fall + base + recover
    # Volume spike on last day
    vols = [800_000] * (len(prices) - 1) + [2_000_000]
    df = _make_history(prices, vols)
    feats = compute_features(df)
    assert feats is not None
    assert feats.vol_ratio_20d >= 1.5, f"vol_ratio={feats.vol_ratio_20d}"
    # The price drop puts current ~15-20% off 52wh
    assert -30 <= feats.dist_from_52wh_pct <= -5, \
        f"dist_52wh={feats.dist_from_52wh_pct}"
    assert classify_setup(feats) == "COMEBACK"


def test_classifier_identifies_contrarian_deep_off_52wh():
    """BOMDYEING case: -33% from 52wh, above MA20 (recovering)."""
    # Climb to 150, deep drop to 95, slow recovery to ~100
    rise = [100 + i * 0.5 for i in range(100)]     # 100 → 150
    fall = [150 - (i + 1) * 0.6 for i in range(80)]   # 150 → 102
    recover = [97 + i * 0.05 for i in range(20)]   # 97 → 98 (above MA20)
    prices = rise + fall + recover
    df = _make_history(prices)
    feats = compute_features(df)
    assert feats is not None
    assert feats.dist_from_52wh_pct < -20
    assert feats.above_ma20  # has reclaimed MA20
    setup = classify_setup(feats)
    # Should be CONTRARIAN (volume optional for this setup)
    assert setup == "CONTRARIAN"


def test_classifier_priority_breakout_over_momentum():
    """When breakout volume signal is present AND price is near 52wh,
    BREAKOUT classification wins over MOMENTUM."""
    # Construct features manually for the priority test
    feats = SetupFeatures(
        above_ma20=True, above_ma50=True, above_ma200=True,
        dist_from_52wh_pct=-2.0,  # near 52wh
        vol_ratio_20d=1.6,         # above breakout vol-ratio proxy threshold
        range_30d_pct=18.0,
        pct_above_ma50=12.0,
        base_quality=4,
        has_breakout_vol=True,
        has_dryup=False,
    )
    assert classify_setup(feats) == "BREAKOUT"


def test_all_setups_have_params():
    """Every SetupType emitted by classifier must have SETUP_PARAMS."""
    for setup_name in ["BREAKOUT", "MOMENTUM", "COMEBACK",
                       "CONTRARIAN", "EARLY_BASE"]:
        assert setup_name in SETUP_PARAMS


def test_setup_params_have_sane_t1_ranges():
    """T1 ranges must be (min < max) and within reasonable bounds (0-25%)."""
    for setup, params in SETUP_PARAMS.items():
        assert 0.0 < params.t1_min_dist < params.t1_max_dist <= 0.25, \
            f"{setup}: T1 range {params.t1_min_dist}-{params.t1_max_dist}"


def test_setup_params_smaller_caps_have_smaller_t1_for_momentum():
    """MOMENTUM should have tighter T1 range than CONTRARIAN (bigger
    expected move on contrarian setups)."""
    assert (SETUP_PARAMS["MOMENTUM"].t1_max_dist
            < SETUP_PARAMS["CONTRARIAN"].t1_max_dist)


def test_features_compute_returns_none_for_short_history():
    df = _make_history([100, 101, 102])
    assert compute_features(df) is None
