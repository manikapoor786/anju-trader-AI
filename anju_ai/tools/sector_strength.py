#!/usr/bin/env python3
"""
anju_ai.tools.sector_strength — sector wave detection.

Phase 2.0: computes whether a stock's sector is in a "wave" — meaning
peers in the same sector are also showing strength. The user identified
this as a key mental filter:

  "single day volume is huge than 20day avg, very high volumes, above
   all MA's, sectoral strength, other shares of sectors are also positive"

We compute three signals per stock + date:

  1. is_sector_top3 — is the stock's sector in the top 3 by 1W return?
  2. sector_breadth — what fraction of sector peers are above their MA50?
  3. is_sector_bottom3 — is the sector lagging? (used for penalty)

Combined into a SectorContext result that scoring.py consumes as a
soft bonus / penalty.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd
from pydantic import BaseModel

from anju_core.sectors import (
    SECTOR_ETFS,
    SECTOR_STOCKS,
    sector_for_symbol,
)


class SectorContext(BaseModel):
    """Sector strength context for one stock at one date.

    Used as a scoring input — the verdict layer in scoring.py applies
    bonuses/penalties based on the flags here.
    """
    symbol: str
    sector: str | None
    is_top3_1w: bool         # sector is in top 3 by 1W return
    is_bottom3_1w: bool      # sector is in bottom 3 by 1W return
    peer_breadth_pct: float  # 0.0 to 100.0 — % of named peers above MA50
    sector_1w_pct: float     # the sector's 1W return (for diagnostics)


def _safe_ma50(df: pd.DataFrame) -> tuple[float, float] | None:
    """Return (cur_price, ma50) for a price series. None if insufficient."""
    if df is None or len(df) < 50:
        return None
    try:
        close = df["Close"].astype(float)
        cur = float(close.iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        if pd.isna(ma50):
            return None
        return cur, ma50
    except Exception:
        return None


def compute_sector_perf(
    ohlcv_loader: Callable[[str, int], pd.DataFrame] | None = None,
    index_loader: Callable[[str, int], pd.DataFrame] | None = None,
) -> dict[str, float]:
    """Compute 1-week % return for each sector with an ETF.

    Returns a dict {sector_name: pct_return_1w}. Sectors whose ETF can't
    be fetched are silently omitted (they just don't appear in top/bottom
    rankings — neutral effect).

    Note: ETFs are indices (^NSEBANK etc.) which require yfinance, not
    bhavcopy. So we accept an index_loader callable; default to
    anju_core.data_layer.get_index.
    """
    if index_loader is None:
        from anju_core.data_layer import get_index
        index_loader = get_index

    out: dict[str, float] = {}
    for sector, ticker in SECTOR_ETFS.items():
        try:
            df = index_loader(ticker, days=30)
            if df is None or len(df) < 7:
                continue
            close = df["Close"].astype(float)
            # 5-bar return (~1 week)
            ret = float(
                (close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100
            )
            out[sector] = round(ret, 2)
        except Exception:
            continue
    return out


def rank_sectors(sector_perf: dict[str, float]) -> tuple[list[str], list[str]]:
    """Given {sector: 1w_pct}, return (top3, bottom3) sector names."""
    if not sector_perf:
        return [], []
    ordered = sorted(sector_perf.items(), key=lambda x: x[1], reverse=True)
    top3 = [s for s, _ in ordered[:3]]
    bottom3 = [s for s, _ in ordered[-3:]] if len(ordered) >= 3 else []
    return top3, bottom3


def compute_peer_breadth(
    sector: str,
    ohlcv_loader: Callable[[str, int], pd.DataFrame],
) -> float:
    """Return % of named peers in `sector` whose Close > MA50.

    A "wave" is in motion when 60%+ of peers are above their MA50.
    Returns 0.0 if no peers can be fetched.
    """
    peers = SECTOR_STOCKS.get(sector, [])
    if not peers:
        return 0.0

    above = 0
    counted = 0
    for sym in peers:
        try:
            df = ohlcv_loader(sym + ".NS", 70)
            ctx = _safe_ma50(df)
            if ctx is None:
                continue
            counted += 1
            if ctx[0] > ctx[1]:
                above += 1
        except Exception:
            continue

    if counted == 0:
        return 0.0
    return round(above / counted * 100, 1)


def compute_context(
    symbol: str,
    ohlcv_loader: Callable[[str, int], pd.DataFrame] | None = None,
    sector_perf: dict[str, float] | None = None,
    breadth_cache: dict[str, float] | None = None,
) -> SectorContext:
    """Compute SectorContext for one stock at the current date.

    Args:
        symbol: stock symbol (with or without .NS)
        ohlcv_loader: callable for peer breadth fetch. Defaults to
            anju_core.data_layer.get_ohlcv.
        sector_perf: precomputed {sector: 1w_pct}. If None, compute
            (expensive — prefer passing in for batch scoring).
        breadth_cache: precomputed {sector: peer_breadth_pct}. Same.

    Returns SectorContext with neutral flags if the stock has no known
    sector or its sector ETF can't be fetched.
    """
    sector = sector_for_symbol(symbol)
    if sector is None:
        return SectorContext(
            symbol=symbol, sector=None,
            is_top3_1w=False, is_bottom3_1w=False,
            peer_breadth_pct=0.0, sector_1w_pct=0.0,
        )

    if sector_perf is None:
        sector_perf = compute_sector_perf()
    top3, bottom3 = rank_sectors(sector_perf)

    if breadth_cache is None or sector not in breadth_cache:
        if ohlcv_loader is None:
            from anju_core.data_layer import get_ohlcv
            ohlcv_loader = get_ohlcv
        breadth = compute_peer_breadth(sector, ohlcv_loader)
    else:
        breadth = breadth_cache[sector]

    return SectorContext(
        symbol=symbol,
        sector=sector,
        is_top3_1w=(sector in top3),
        is_bottom3_1w=(sector in bottom3),
        peer_breadth_pct=breadth,
        sector_1w_pct=sector_perf.get(sector, 0.0),
    )
