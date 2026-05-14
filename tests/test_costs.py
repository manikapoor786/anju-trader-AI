"""Tests for anju_ai.tools.costs — Indian retail equity cost model."""

import pytest

from anju_ai.tools.costs import (
    DEFAULTS,
    CostBreakdown,
    cost_leg,
    cost_round_trip,
    net_pnl,
)


# ── cost_leg ──────────────────────────────────────────────────────────────────

def test_buy_leg_charges_stamp_not_stt():
    """Stamp duty applies on BUY; STT only on SELL (delivery)."""
    out = cost_leg(price=1000, qty=10, side="BUY")
    assert out.stamp > 0
    assert out.stt == 0


def test_sell_leg_charges_stt_not_stamp():
    out = cost_leg(price=1000, qty=10, side="SELL")
    assert out.stt > 0
    assert out.stamp == 0


def test_gst_is_18pct_of_brokerage_plus_exchange():
    out = cost_leg(price=1000, qty=10, side="BUY")
    expected_gst = (out.brokerage + out.exchange) * 0.18
    assert out.gst == pytest.approx(expected_gst, rel=1e-3)


def test_slippage_smallcap_higher_than_largecap():
    a = cost_leg(price=100, qty=1000, side="BUY", segment="largecap")
    b = cost_leg(price=100, qty=1000, side="BUY", segment="midcap")
    c = cost_leg(price=100, qty=1000, side="BUY", segment="smallcap")
    assert a.slippage < b.slippage < c.slippage


def test_zero_value_returns_empty_breakdown():
    out = cost_leg(price=0, qty=10, side="BUY")
    assert out.total == 0
    assert out.brokerage == 0


def test_total_is_sum_of_components():
    out = cost_leg(price=1000, qty=10, side="SELL")
    expected = (out.brokerage + out.stt + out.exchange + out.gst
                + out.sebi + out.stamp + out.slippage)
    assert out.total == pytest.approx(expected, abs=0.01)


def test_total_pct_is_correct_share_of_value():
    out = cost_leg(price=100, qty=100, side="BUY")
    # value = 10000; total_pct = total / 10000 * 100
    assert out.total_pct == pytest.approx(out.total / 100, rel=1e-3)


# ── cost_round_trip ───────────────────────────────────────────────────────────

def test_round_trip_combines_buy_and_sell():
    buy  = cost_leg(price=100, qty=100, side="BUY")
    sell = cost_leg(price=110, qty=100, side="SELL")
    rt   = cost_round_trip(buy_price=100, sell_price=110, qty=100)
    assert rt.total == pytest.approx(buy.total + sell.total, abs=0.01)
    assert rt.stt > 0          # from sell leg
    assert rt.stamp > 0        # from buy leg


def test_round_trip_total_pct_is_against_buy_value():
    rt = cost_round_trip(buy_price=100, sell_price=200, qty=100)
    # total_pct = total_cost / (100 * 100) * 100
    assert rt.total_pct == pytest.approx(rt.total / 100, rel=1e-3)


# ── net_pnl ───────────────────────────────────────────────────────────────────

def test_net_pnl_subtracts_costs_from_gross():
    out = net_pnl(buy_price=100, sell_price=110, qty=100)
    # gross = (110-100)*100 = 1000
    assert out["gross_inr"] == 1000
    assert out["gross_pct"] == 10.0
    assert out["costs_inr"] > 0
    assert out["net_inr"] == pytest.approx(out["gross_inr"] - out["costs_inr"], abs=0.01)
    assert out["net_pct"] < out["gross_pct"]


def test_net_pnl_zero_position_handled():
    out = net_pnl(buy_price=100, sell_price=110, qty=0)
    assert out["gross_inr"] == 0
    assert out["costs_inr"] == 0
    assert out["net_inr"] == 0


def test_cost_eats_meaningful_portion_of_small_winner():
    # 2% gross winner on midcap — costs should eat a noticeable chunk
    out = net_pnl(buy_price=1000, sell_price=1020, qty=100, segment="midcap")
    # gross 2%, costs should be 0.3-0.6% range → net ~1.4-1.7%
    assert out["gross_pct"] == 2.0
    assert 0.2 < out["costs_pct"] < 1.0
    assert out["net_pct"] < out["gross_pct"]


def test_smallcap_costs_meaningfully_more_than_largecap():
    a = net_pnl(buy_price=100, sell_price=105, qty=1000, segment="largecap")
    b = net_pnl(buy_price=100, sell_price=105, qty=1000, segment="smallcap")
    # Smallcap slippage (0.35%) is way higher → net % much lower
    assert b["net_pct"] < a["net_pct"]
    assert (a["net_pct"] - b["net_pct"]) > 0.4   # at least 40 bps gap


# ── DEFAULTS / config override ────────────────────────────────────────────────

def test_custom_cfg_changes_result():
    custom = dict(DEFAULTS, brokerage_per_order_inr=50.0)
    a = cost_leg(price=100, qty=10, side="BUY")
    b = cost_leg(price=100, qty=10, side="BUY", cfg=custom)
    assert b.brokerage > a.brokerage
    assert b.total > a.total


def test_defaults_present_for_all_known_keys():
    required = {"brokerage_per_order_inr", "stt_sell_pct", "exchange_pct",
                "gst_pct", "sebi_pct", "stamp_pct_buy", "slippage_pct_by_segment"}
    assert required <= set(DEFAULTS.keys())


# ── CostBreakdown.add ─────────────────────────────────────────────────────────

def test_breakdown_add_combines_fields():
    a = CostBreakdown(brokerage=10, stt=5, total=15)
    b = CostBreakdown(brokerage=20, stt=10, total=30)
    c = a.add(b)
    assert c.brokerage == 30
    assert c.stt == 15
    assert c.total == 45
