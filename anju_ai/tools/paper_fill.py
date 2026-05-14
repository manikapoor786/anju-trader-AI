#!/usr/bin/env python3
"""
anju_ai.tools.paper_fill — simulated fill with realistic slippage.

A signal generated at EOD price T must be paper-filled at T+1 open with
modelled slippage to reflect real fill realities. Without this, paper
backtests overstate returns by 0.3-1.5% per trade depending on liquidity.

Slippage model (linear in qty, segment-aware):
    base_slippage_pct = config.slippage_pct_by_segment[segment]
    size_factor = qty / avg_daily_volume_10d * 0.5   # large size → more slippage
    final_slippage_pct = base_slippage_pct + size_factor * 0.5

A 1% position in a stock with 1 cr daily volume has ~0% size impact.
A 10% position (rare) starts to materially move the price.

Costs included separately by anju_ai.tools.costs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class FillInput(BaseModel):
    """Input to simulate one fill."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    signal_date: str                       # 'YYYY-MM-DD' — date the signal fired
    intended_price: float                  # signal-time close (reference only)
    qty: int
    side: Literal["BUY", "SELL"] = "BUY"
    df_post_signal: pd.DataFrame           # df rows for dates >= signal_date+1
    segment: Literal["largecap", "midcap", "smallcap"] = "midcap"
    base_slippage_pct: float = 0.15        # config-driven default
    avg_volume_10d: float | None = None    # for size-impact calc


class FillResult(BaseModel):
    """Outcome of a fill simulation."""
    fill_date: str
    fill_price: float                      # actual modelled fill price
    intended_price: float                  # what the signal said
    slippage_pct: float                    # absolute % slippage vs intended
    slippage_inr: float                    # ₹ cost of slippage
    qty: int
    side: str
    is_filled: bool                        # False if no T+1 data (e.g. delisted)
    rejection_reason: str | None = None


def simulate_fill(inp: FillInput) -> FillResult:
    """Compute a realistic fill at the next available open price + slippage."""
    if inp.df_post_signal is None or inp.df_post_signal.empty:
        return FillResult(
            fill_date="", fill_price=inp.intended_price,
            intended_price=inp.intended_price, slippage_pct=0.0,
            slippage_inr=0.0, qty=inp.qty, side=inp.side,
            is_filled=False, rejection_reason="No post-signal data available",
        )

    # Use the first available open price after the signal date
    first = inp.df_post_signal.iloc[0]
    fill_date = (str(inp.df_post_signal.index[0])[:10]
                 if hasattr(inp.df_post_signal.index[0], "strftime")
                 else str(inp.df_post_signal.index[0]))
    open_price = float(first["Open"])

    # Size impact: position size vs avg daily volume
    size_factor = 0.0
    if inp.avg_volume_10d and inp.avg_volume_10d > 0:
        position_ratio = (inp.qty * open_price) / (inp.avg_volume_10d * open_price)
        # If you're trading >5% of daily volume, slippage scales materially
        size_factor = min(position_ratio * 10, 1.0)   # capped contribution

    total_slippage_pct = inp.base_slippage_pct + size_factor * 0.5

    # BUY slips up (paid more); SELL slips down (received less)
    direction = 1 if inp.side == "BUY" else -1
    fill_price = open_price * (1 + direction * total_slippage_pct / 100)
    fill_price = round(fill_price, 2)

    slippage_inr = round(abs(fill_price - open_price) * inp.qty, 2)

    return FillResult(
        fill_date=fill_date,
        fill_price=fill_price,
        intended_price=inp.intended_price,
        slippage_pct=round(total_slippage_pct, 3),
        slippage_inr=slippage_inr,
        qty=inp.qty,
        side=inp.side,
        is_filled=True,
    )


def classify_segment(price: float, avg_volume_10d: float | None = None) -> str:
    """Rough segment classification from price + volume. Phase 1 will switch
    this to point-in-time market-cap-based segmentation."""
    if avg_volume_10d and avg_volume_10d > 5_000_000:
        return "largecap"
    if avg_volume_10d and avg_volume_10d > 1_000_000:
        return "midcap"
    return "smallcap"
