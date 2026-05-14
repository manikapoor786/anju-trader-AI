#!/usr/bin/env python3
"""
anju_ai.tools.costs — Indian retail equity transaction cost model.

Fixes audit Finding 3.3: anju-trader has no cost model. Every backtest
result is gross-of-costs and overstates returns by 0.4–0.8% per round trip.

At ₹1.75 cr deployed, a 50%-win-rate 1.5:1 R:R system shows +1% gross
expectancy but barely +0.4% after costs. The cost halves the edge — or
eliminates it on smaller setups.

This module computes per-leg cost components matching a Zerodha CNC /
F&O fee structure (calibrated against actual brokerage statements monthly
in Phase 1.7).

Component reference (CNC equity delivery, Indian rupees, FY2025):
  Brokerage      ₹0 to ₹20 per executed order (Zerodha free for delivery, but
                 framework keeps a flat per-order figure for safety)
  STT            0.025% on SELL side only (delivery)
  Exchange       0.00345% per leg
  GST            18% on (brokerage + exchange)
  SEBI           0.0001% per leg
  Stamp duty     0.015% on BUY side only
  Slippage       per-segment, see runtime.yaml — covers market impact

A round trip = BUY + SELL → call cost_round_trip().
A single leg → call cost_leg().
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ── Default Zerodha CNC structure (FY2025) ────────────────────────────────────
# These match config/runtime.yaml — kept as module defaults so this module
# is usable standalone in tests / backtests without YAML.

DEFAULTS = {
    "brokerage_per_order_inr": 20.0,
    "stt_sell_pct":            0.025,    # delivery
    "exchange_pct":            0.00345,
    "gst_pct":                 18.0,
    "sebi_pct":                0.0001,
    "stamp_pct_buy":           0.015,
    "slippage_pct_by_segment": {
        "largecap": 0.05,
        "midcap":   0.15,
        "smallcap": 0.35,
    },
}


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class CostBreakdown(BaseModel):
    """Itemised cost breakdown for one leg or round trip."""
    brokerage:    float = 0.0
    stt:          float = 0.0
    exchange:     float = 0.0
    gst:          float = 0.0
    sebi:         float = 0.0
    stamp:        float = 0.0
    slippage:     float = 0.0
    total:        float = 0.0
    total_pct:    float = 0.0       # cost as % of trade value

    def add(self, other: "CostBreakdown") -> "CostBreakdown":
        """Combine two breakdowns (e.g. round-trip = buy + sell)."""
        return CostBreakdown(
            brokerage = self.brokerage + other.brokerage,
            stt       = self.stt       + other.stt,
            exchange  = self.exchange  + other.exchange,
            gst       = self.gst       + other.gst,
            sebi      = self.sebi      + other.sebi,
            stamp     = self.stamp     + other.stamp,
            slippage  = self.slippage  + other.slippage,
            total     = self.total     + other.total,
            total_pct = 0.0,          # caller recomputes vs total trade value
        )


# ── Core: one leg ─────────────────────────────────────────────────────────────

def cost_leg(price: float, qty: int, side: Literal["BUY", "SELL"],
             segment: Literal["largecap", "midcap", "smallcap"] = "midcap",
             cfg: dict | None = None) -> CostBreakdown:
    """Compute the cost of one fill (one direction)."""
    if cfg is None:
        cfg = DEFAULTS
    value = price * qty
    if value <= 0:
        return CostBreakdown()

    brokerage = float(cfg.get("brokerage_per_order_inr", 20.0))
    exchange  = value * float(cfg.get("exchange_pct", 0.00345)) / 100
    sebi      = value * float(cfg.get("sebi_pct", 0.0001)) / 100
    gst       = (brokerage + exchange) * float(cfg.get("gst_pct", 18.0)) / 100

    stt = 0.0
    stamp = 0.0
    if side == "SELL":
        stt = value * float(cfg.get("stt_sell_pct", 0.025)) / 100
    if side == "BUY":
        stamp = value * float(cfg.get("stamp_pct_buy", 0.015)) / 100

    slip_pct = float(cfg.get("slippage_pct_by_segment", {}).get(segment, 0.15))
    slippage = value * slip_pct / 100

    total = brokerage + stt + exchange + gst + sebi + stamp + slippage
    return CostBreakdown(
        brokerage=round(brokerage, 4),
        stt=round(stt, 4),
        exchange=round(exchange, 4),
        gst=round(gst, 4),
        sebi=round(sebi, 4),
        stamp=round(stamp, 4),
        slippage=round(slippage, 4),
        total=round(total, 2),
        total_pct=round(total / value * 100, 4),
    )


# ── Convenience: round trip ───────────────────────────────────────────────────

def cost_round_trip(buy_price: float, sell_price: float, qty: int,
                    segment: Literal["largecap", "midcap", "smallcap"] = "midcap",
                    cfg: dict | None = None) -> CostBreakdown:
    """BUY + SELL combined. total_pct is computed against the BUY value
    (i.e. how much of your invested capital went to costs)."""
    buy  = cost_leg(buy_price,  qty, "BUY",  segment, cfg)
    sell = cost_leg(sell_price, qty, "SELL", segment, cfg)
    rt = buy.add(sell)
    buy_value = buy_price * qty
    if buy_value > 0:
        rt.total_pct = round(rt.total / buy_value * 100, 4)
    return rt


# ── Convenience: P&L after costs ──────────────────────────────────────────────

def net_pnl(buy_price: float, sell_price: float, qty: int,
            segment: str = "midcap", cfg: dict | None = None) -> dict:
    """Return gross / costs / net P&L in ₹ and %. Used by outcome closure
    and backtest reporting (Phase 1.4)."""
    gross = (sell_price - buy_price) * qty
    costs = cost_round_trip(buy_price, sell_price, qty, segment, cfg)
    net = gross - costs.total
    buy_value = buy_price * qty
    return {
        "gross_inr":   round(gross, 2),
        "gross_pct":   round((sell_price - buy_price) / buy_price * 100, 4) if buy_price else 0.0,
        "costs_inr":   costs.total,
        "costs_pct":   costs.total_pct,
        "net_inr":     round(net, 2),
        "net_pct":     round(net / buy_value * 100, 4) if buy_value else 0.0,
        "breakdown":   costs.model_dump(),
    }
