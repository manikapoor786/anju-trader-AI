#!/usr/bin/env python3
"""
anju_core.sectors — sector classification + sector ETF mapping.

Ported from anju-trader/scanner.py SECTOR_STOCKS + extended with realty
and defence (which moved heavily in the last 24 months and anju-trader-AI
needs to recognise).

This is a static mapping covering ~120 high-activity NSE stocks. For
stocks not in this dict, sector_for_symbol() returns None — those stocks
just don't get the sectoral bonus/penalty (neutral). Future Phase 2.x:
dynamic lookup via yfinance .info['sector'] with on-disk cache.
"""

from __future__ import annotations


# Sector → list of bare symbols (NO .NS suffix; use as_ns() to convert).
# Each list is a representative sample, not exhaustive. ~10-15 stocks per
# sector — enough to compute "is sector in a wave?" reliably.
SECTOR_STOCKS: dict[str, list[str]] = {
    "Defence":  [
        "BEL", "HAL", "PARAS", "GRSE", "BDL", "COCHINSHIP", "MAZDOCK",
        "DATAPATTNS", "MIDHANI", "ASTRAMICRO", "BHEL", "MODEFENCE",
    ],
    "Banking":  [
        "HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK", "SBIN",
        "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "RBLBANK",
        "INDUSINDBK", "PNB", "CANBK", "UNIONBANK", "BANKINDIA",
    ],
    "IT":       [
        "TCS", "INFOSYS", "HCLTECH", "WIPRO", "TECHM", "LTIM", "MPHASIS",
        "PERSISTENT", "COFORGE", "TATAELXSI", "LTTS", "KPITTECH",
        "BIRLASOFT", "OFSS",
    ],
    "Pharma":   [
        "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "AUROPHARMA",
        "TORNTPHARM", "ALKEM", "IPCALAB", "LAURUSLABS", "GLENMARK",
        "ZYDUSLIFE", "BIOCON", "AJANTPHARM",
    ],
    "Auto":     [
        "MARUTI", "TATAMOTORS", "M&M", "EICHERMOT", "BAJAJ-AUTO",
        "HEROMOTOCO", "TVSMOTOR", "ASHOKLEY", "BALKRISIND", "ESCORTS",
        "MOTHERSON", "BHARATFORG", "EXIDEIND", "SONACOMS",
    ],
    "Metal":    [
        "TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "COALINDIA",
        "NATIONALUM", "HINDZINC", "NMDC", "SAIL", "JINDALSTEL",
        "HINDCOPPER", "MOIL", "WELCORP", "RATNAMANI",
    ],
    "Energy":   [
        "RELIANCE", "ONGC", "BPCL", "IOC", "HPCL", "GAIL", "TATAPOWER",
        "ADANIGREEN", "NTPC", "POWERGRID", "ADANIPOWER", "TORNTPOWER",
        "JSWENERGY", "CESC", "NHPC",
    ],
    "FMCG":     [
        "HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "MARICO",
        "GODREJCP", "EMAMILTD", "TATACONSUM", "COLPAL", "UBL",
        "MCDOWELL-N", "VBL", "PATANJALI",
    ],
    "Infra":    [
        "LT", "GMRINFRA", "IRB", "NCC", "KEC", "NBCC", "RVNL", "IRFC",
        "CONCOR", "GMRAIRPORT", "KECINTERN", "HUDCO", "CAPACITE",
    ],
    "Realty":   [
        "DLF", "GODREJPROP", "PRESTIGE", "OBEROIRLTY", "BRIGADE",
        "SOBHA", "PHOENIXLTD", "LODHA", "ANANTRAJ", "BOMDYEING",
        "SUNTECK", "MAHLIFE",
    ],
    "Cement":   [
        "ULTRACEMCO", "AMBUJACEM", "ACC", "SHREECEM", "DALBHARAT",
        "RAMCOCEM", "JKCEMENT", "INDIACEM",
    ],
    "Chemicals": [
        "PIDILITIND", "SRF", "ATUL", "NAVINFLUOR", "AARTIIND", "PIIND",
        "DEEPAKNTR", "GRANULES", "UPL", "BASF",
    ],
    "Telecom":  [
        "BHARTIARTL", "IDEA", "HFCL", "TATACOMM", "TEJAS", "INDUSTOWER",
    ],
    "Consumer Durables": [
        "TITAN", "ASIANPAINT", "BERGEPAINT", "HAVELLS", "VOLTAS",
        "WHIRLPOOL", "CROMPTON", "RELAXO", "BATAINDIA", "VGUARD",
        "POLYCAB", "SUPREMEIND",
    ],
}

# Sector ETF tickers — for fetching sector 1W performance.
# yfinance supports these symbols when available; fall back to None otherwise.
SECTOR_ETFS: dict[str, str] = {
    "Defence":   "MODEFENCE.NS",       # NSE defence ETF (only one available)
    "Banking":   "^NSEBANK",
    "IT":        "^CNXIT",
    "Pharma":    "^CNXPHARMA",
    "Auto":      "^CNXAUTO",
    "Metal":     "^CNXMETAL",
    "Energy":    "^CNXENERGY",
    "FMCG":      "^CNXFMCG",
    "Infra":     "^CNXINFRA",
    "Realty":    "^CNXREALTY",
}

# Reverse index: bare symbol → sector name (computed once at import).
_SYMBOL_TO_SECTOR: dict[str, str] = {}
for sector, syms in SECTOR_STOCKS.items():
    for s in syms:
        _SYMBOL_TO_SECTOR[s.upper()] = sector


def sector_for_symbol(symbol: str) -> str | None:
    """Return sector name for `symbol` (with or without .NS suffix).
    Returns None for stocks not in the static mapping (neutral — no
    sectoral bonus/penalty applied)."""
    bare = symbol.upper().replace(".NS", "").replace(".BSE", "")
    return _SYMBOL_TO_SECTOR.get(bare)


def stocks_in_sector(sector: str) -> list[str]:
    """Return bare symbols in `sector`, or empty list if unknown."""
    return list(SECTOR_STOCKS.get(sector, []))
