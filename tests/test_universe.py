"""Tests for anju_core.universe."""

import pytest

from anju_core.universe import (
    NIFTY_50,
    NIFTY_100,
    NIFTY_180,
    NIFTY_500,
    NIFTY_750,
    NIFTY_MICROCAP_250,
    NIFTY_NEXT_50,
    UNIVERSES,
    get_universe,
)


def test_nifty50_has_50_unique_symbols():
    assert len(NIFTY_50) == 50
    assert len(set(NIFTY_50)) == 50


def test_nifty_next_50_has_50_unique_symbols():
    assert len(NIFTY_NEXT_50) == 50
    assert len(set(NIFTY_NEXT_50)) == 50


def test_nifty100_no_duplicates():
    assert len(NIFTY_100) == len(set(NIFTY_100))
    assert len(NIFTY_100) == 100


def test_nifty180_no_duplicates_and_supersets_nifty100():
    assert len(NIFTY_180) == len(set(NIFTY_180))
    assert set(NIFTY_100).issubset(set(NIFTY_180))


def test_nifty500_loaded_from_nse_cache():
    """Phase 1.9: nifty500 must be the real NSE 500 list, not a stub."""
    assert len(NIFTY_500) == 500, (
        f"Expected 500 stocks, got {len(NIFTY_500)} — "
        "data/nse_universe_cache.csv missing or wrong size?"
    )
    assert len(set(NIFTY_500)) == 500


def test_nifty750_is_500_plus_microcap_250():
    assert len(NIFTY_750) == 750
    assert len(set(NIFTY_750)) == 750
    assert len(NIFTY_MICROCAP_250) == 250
    # nifty750 = nifty500 + microcap250 (in that order)
    assert NIFTY_750[:500] == NIFTY_500
    assert NIFTY_750[500:] == NIFTY_MICROCAP_250


def test_nifty500_overlaps_but_isnt_subset_of_legacy_180():
    """The real NSE 500 is reorganised by market cap and won't be a strict
    superset of our hardcoded NIFTY_100 (which has stale aliases like
    HDFCBANK.NS vs HDFC.NS). It should still substantially overlap."""
    overlap = set(NIFTY_500) & set(NIFTY_100)
    assert len(overlap) >= 70, (
        f"nifty500 should contain most of nifty100, got overlap {len(overlap)}"
    )


def test_all_universes_use_ns_suffix():
    for name, syms in UNIVERSES.items():
        for s in syms:
            assert s.endswith(".NS"), f"{name}: {s} missing .NS suffix"


def test_get_universe_returns_copy():
    a = get_universe("nifty50")
    b = get_universe("nifty50")
    assert a is not b   # modifying one mustn't affect the other
    assert a == b


def test_get_universe_case_insensitive():
    assert get_universe("NIFTY50") == get_universe("nifty50")
    assert get_universe(" Nifty50 ") == get_universe("nifty50")


def test_get_universe_unknown_raises():
    with pytest.raises(ValueError):
        get_universe("imaginary")


# ── Survivorship-clean (1.6) ─────────────────────────────────────────────────

import pandas as pd
from anju_core.universe import get_universe_at_date, get_universe_with_cache


def _mk_loader(active_symbols: dict[str, str]):
    """Return a mock ohlcv_loader. active_symbols: {sym: last_trading_date}.
    Symbols not in the dict return empty DataFrame (i.e. delisted)."""
    def loader(symbol, days):
        last = active_symbols.get(symbol)
        if not last:
            return pd.DataFrame()
        dates = pd.bdate_range(end=last, periods=days)
        return pd.DataFrame({
            "Open":   [100] * len(dates),
            "High":   [101] * len(dates),
            "Low":    [99]  * len(dates),
            "Close":  [100] * len(dates),
            "Volume": [1000] * len(dates),
        }, index=dates)
    return loader


def test_get_universe_at_date_drops_delisted():
    """Stocks with no data near as_of_date are excluded."""
    nifty50 = get_universe("nifty50")
    # Only first 5 symbols are "active" as of 2024-06-01
    active = {s: "2024-06-15" for s in nifty50[:5]}
    out = get_universe_at_date("nifty50", "2024-06-01",
                               ohlcv_loader=_mk_loader(active))
    assert len(out) == 5
    assert set(out) == set(nifty50[:5])


def test_get_universe_at_date_keeps_recently_listed():
    """A symbol active at the target date is kept."""
    nifty50 = get_universe("nifty50")
    active = {nifty50[0]: "2024-06-15"}
    out = get_universe_at_date("nifty50", "2024-06-10",
                               ohlcv_loader=_mk_loader(active))
    assert out == [nifty50[0]]


def test_get_universe_at_date_drops_not_yet_listed():
    """A symbol whose data only starts AFTER target date is dropped."""
    nifty50 = get_universe("nifty50")
    # last_date 2025-01-01 means data window is 10 bdays ending 2025-01-01.
    # Target 2024-01-01 is far before → no overlap → drop.
    active = {nifty50[0]: "2025-01-01"}
    out = get_universe_at_date("nifty50", "2024-01-01",
                               ohlcv_loader=_mk_loader(active))
    assert out == []


def test_get_universe_with_cache_memoises():
    """Cache prevents repeated ohlcv_loader calls for the same date."""
    nifty50 = get_universe("nifty50")
    active = {nifty50[0]: "2024-06-15"}
    calls = [0]
    def counting_loader(sym, days):
        calls[0] += 1
        return _mk_loader(active)(sym, days)

    cache: dict = {}
    a = get_universe_with_cache("nifty50", "2024-06-10",
                                cache=cache, ohlcv_loader=counting_loader)
    n1 = calls[0]
    b = get_universe_with_cache("nifty50", "2024-06-10",
                                cache=cache, ohlcv_loader=counting_loader)
    n2 = calls[0]
    assert a == b
    assert n2 == n1   # cache hit — no additional loader calls
