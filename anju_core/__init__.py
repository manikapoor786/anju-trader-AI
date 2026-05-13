"""anju_core — proven primitives forked from anju-trader.

These modules are battle-tested in production and reused as-is.
Do not modify without a corresponding test + ADR entry.

Modules (Phase 0 plan):
    data_layer    NSE bhavcopy + yfinance fallback (forked from anju-trader)
    regime        4-state market regime classifier (forked)
    universe      Stock universe definitions (forked + survivorship-cleaned in Phase 1)
    kite          Kite Connect auth + sync (forked)
"""

__version__ = "0.0.1"
