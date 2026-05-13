"""anju_core — proven primitives forked from anju-trader.

These modules are battle-tested in production and reused as-is.
Do not modify without a corresponding test + ADR entry.

Public API:
    get_ohlcv(symbol, days)       Daily OHLCV from local bhavcopy cache
    get_index(symbol, days)       Index OHLCV (yfinance — Nifty, BankNifty, VIX)
    get_universe(min_rows)        Symbols with at least N days of history
    refresh_daily(days_back)      Idempotent bhavcopy backfill
"""

from anju_core.data_layer import (
    get_ohlcv,
    get_index,
    get_universe,
    refresh_daily,
)

__version__ = "0.0.2"

__all__ = [
    "get_ohlcv",
    "get_index",
    "get_universe",
    "refresh_daily",
]
