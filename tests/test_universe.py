"""Tests for anju_core.universe."""

import pytest

from anju_core.universe import (
    NIFTY_50,
    NIFTY_100,
    NIFTY_180,
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
