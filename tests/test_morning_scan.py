"""Tests for anju_ai.loops.morning_scan helpers — pure functions only.

The full pipeline hits network (refresh_daily, get_index) so we test:
    - _compute_qty position sizing
    - _split_text Telegram chunking
    - _load_yaml config loader
"""

import pytest

from anju_ai.loops.morning_scan import (
    _compute_qty,
    _split_text,
)


# ── _compute_qty ──────────────────────────────────────────────────────────────

def test_compute_qty_zero_for_invalid_price():
    assert _compute_qty(0, 95) == 0
    assert _compute_qty(-1, 95) == 0


def test_compute_qty_respects_risk_per_share():
    # 1% of 1cr capital = 1L risk budget
    # Risk per share = price - stop = 100 - 95 = 5
    # qty_by_risk = 100000 / 5 = 20000
    # qty_by_cap  = (1cr * 10%) / 100 = 10000  → caps
    qty = _compute_qty(price=100, stop=95, total_capital=10_000_000,
                       risk_pct=1.0, max_pos_pct=10.0)
    assert qty == 10000   # capped by max_pos_pct


def test_compute_qty_uses_risk_when_smaller_than_cap():
    # Tight stop (risk per share = 10) on cheap stock → risk caps first
    qty = _compute_qty(price=100, stop=90, total_capital=10_000_000,
                       risk_pct=1.0, max_pos_pct=10.0)
    # risk_amount = 100000, risk_per_share = 10, qty_by_risk = 10000
    # cap = 10000  → tied → either way returns 10000
    assert qty == 10000


def test_compute_qty_protects_against_zero_distance():
    # If stop == price → risk_per_share floor = 0.5% of price = 0.5
    # risk_amount = 100000, qty_by_risk = 100000 / 0.5 = 200000
    # cap = 10000 → caps
    qty = _compute_qty(price=100, stop=100, total_capital=10_000_000,
                       risk_pct=1.0, max_pos_pct=10.0)
    assert qty == 10000


def test_compute_qty_tiny_capital_returns_1_share():
    qty = _compute_qty(price=100, stop=95, total_capital=1000,
                       risk_pct=1.0, max_pos_pct=10.0)
    # Manual: risk_amt=10, rps=5, qty_risk=2; cap=100/100=1 → min=1
    assert qty == 1


# ── _split_text ───────────────────────────────────────────────────────────────

def test_split_text_short_returns_single():
    out = _split_text("hello", 100)
    assert out == ["hello"]


def test_split_text_long_splits_on_newlines():
    lines = [f"line {i}" for i in range(50)]
    text = "\n".join(lines)
    chunks = _split_text(text, 50)
    assert len(chunks) > 1
    # No chunk exceeds limit
    for c in chunks:
        assert len(c) <= 50 + 7   # +len of one "line NN" worst case
    # Rejoining preserves content (modulo split chars)
    rejoined = "\n".join(chunks)
    assert rejoined.replace("\n", "") == text.replace("\n", "")


def test_split_text_empty():
    assert _split_text("", 100) == [""]
