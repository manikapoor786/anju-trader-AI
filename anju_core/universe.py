#!/usr/bin/env python3
"""
anju_core.universe — stock universe registry.

Phase 0: hardcoded static lists ported from anju-trader/scanner.py.
Phase 1: survivorship-clean point-in-time universes (NIFTY_500_AT_DATE etc.)
Phase 2: dynamic NSE-fetched lists with weekly cache refresh.

Usage:
    from anju_core.universe import get_universe
    syms = get_universe("nifty50")        # → ['RELIANCE.NS', 'TCS.NS', ...]
    syms = get_universe("nifty500")       # → ~500 symbols
"""

from __future__ import annotations


NIFTY_50 = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "BHARTIARTL.NS", "ICICIBANK.NS",
    "INFOSYS.NS", "SBIN.NS", "HINDUNILVR.NS", "ITC.NS", "LT.NS",
    "KOTAKBANK.NS", "AXISBANK.NS", "BAJFINANCE.NS", "MARUTI.NS", "ASIANPAINT.NS",
    "HCLTECH.NS", "SUNPHARMA.NS", "TITAN.NS", "WIPRO.NS", "ULTRACEMCO.NS",
    "NTPC.NS", "POWERGRID.NS", "NESTLEIND.NS", "TECHM.NS", "BAJAJFINSV.NS",
    "TATAMOTORS.NS", "M&M.NS", "ADANIENT.NS", "ADANIPORTS.NS", "COALINDIA.NS",
    "ONGC.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "INDUSINDBK.NS", "HINDALCO.NS",
    "GRASIM.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "EICHERMOT.NS",
    "HEROMOTOCO.NS", "BPCL.NS", "BRITANNIA.NS", "TATACONSUM.NS", "APOLLOHOSP.NS",
    "BAJAJ-AUTO.NS", "SHRIRAMFIN.NS", "SBILIFE.NS", "HDFCLIFE.NS", "LTIM.NS",
]

NIFTY_NEXT_50 = [
    "ADANIGREEN.NS", "ADANITRANS.NS", "AMBUJACEM.NS", "AUROPHARMA.NS", "BANDHANBNK.NS",
    "BERGEPAINT.NS", "BOSCHLTD.NS", "CANBK.NS", "CHOLAFIN.NS", "COLPAL.NS",
    "DABUR.NS", "DLF.NS", "GAIL.NS", "GODREJCP.NS", "GODREJPROP.NS",
    "HAVELLS.NS", "ICICIGI.NS", "ICICIPRULI.NS", "INDIGO.NS", "INDUSTOWER.NS",
    "IOC.NS", "IRCTC.NS", "JUBLFOOD.NS", "LICHSGFIN.NS", "LUPIN.NS",
    "MARICO.NS", "MCDOWELL-N.NS", "MPHASIS.NS", "MUTHOOTFIN.NS", "NAUKRI.NS",
    "OFSS.NS", "PAGEIND.NS", "PERSISTENT.NS", "PETRONET.NS", "PIDILITIND.NS",
    "PIIND.NS", "PNB.NS", "RECLTD.NS", "SAIL.NS", "SIEMENS.NS",
    "SRF.NS", "TORNTPHARM.NS", "TRENT.NS", "TVSMOTOR.NS", "UBL.NS",
    "UNITDSPR.NS", "UPL.NS", "VEDL.NS", "VOLTAS.NS", "ZOMATO.NS",
]

# Curated midcap subset (~80 names) — high-quality, liquid midcaps from
# anju-trader/scanner.py NIFTY_MIDCAP. Full 750-stock universe is a Phase 1
# item once we have survivorship-clean point-in-time membership.
NIFTY_MIDCAP = [
    "AARTIIND.NS", "ABCAPITAL.NS", "ABFRL.NS", "ACC.NS", "AIAENG.NS",
    "AJANTPHARM.NS", "ALKEM.NS", "APLLTD.NS", "ASHOKLEY.NS", "ASTRAL.NS",
    "ATUL.NS", "AUBANK.NS", "BALKRISIND.NS", "BATAINDIA.NS", "BEL.NS",
    "BHEL.NS", "BIOCON.NS", "BIRLASOFT.NS", "BRIGADE.NS", "CAMS.NS",
    "CESC.NS", "CGPOWER.NS", "COFORGE.NS", "CONCOR.NS", "CROMPTON.NS",
    "CUMMINSIND.NS", "DEEPAKNTR.NS", "DELHIVERY.NS", "DIXON.NS", "ENGINERSIN.NS",
    "ESCORTS.NS", "EXIDEIND.NS", "FEDERALBNK.NS", "GLENMARK.NS", "GRANULES.NS",
    "HFCL.NS", "HINDCOPPER.NS", "HINDPETRO.NS", "IDFCFIRSTB.NS", "IEX.NS",
    "IPCALAB.NS", "JKCEMENT.NS", "JSWENERGY.NS", "KPITTECH.NS", "LAURUSLABS.NS",
    "LTTS.NS", "MANAPPURAM.NS", "MAXHEALTH.NS", "MCX.NS", "METROPOLIS.NS",
    "MOTHERSON.NS", "NAVINFLUOR.NS", "NYKAA.NS", "OBEROIRLTY.NS", "OLECTRA.NS",
    "PHOENIXLTD.NS", "POLYCAB.NS", "PRESTIGE.NS", "RBLBANK.NS", "RELAXO.NS",
    "RVNL.NS", "SBICARD.NS", "SCHAEFFLER.NS", "SONACOMS.NS", "STARHEALTH.NS",
    "SUPREMEIND.NS", "SYNGENE.NS", "TATACOMM.NS", "TATAELXSI.NS", "THERMAX.NS",
    "TIINDIA.NS", "TIMKEN.NS", "TORNTPOWER.NS", "TRENT.NS", "VGUARD.NS",
    "VOLTAS.NS", "WHIRLPOOL.NS", "ZEEL.NS", "HAL.NS", "MAZDOCK.NS",
]

NIFTY_100 = list(dict.fromkeys(NIFTY_50 + NIFTY_NEXT_50))
NIFTY_180 = list(dict.fromkeys(NIFTY_50 + NIFTY_NEXT_50 + NIFTY_MIDCAP))

UNIVERSES: dict[str, list[str]] = {
    "nifty50":   NIFTY_50,
    "next50":    NIFTY_NEXT_50,
    "midcap":    NIFTY_MIDCAP,
    "nifty100":  NIFTY_100,
    "nifty180":  NIFTY_180,
    # Aliases for backward-compat with workflow inputs:
    "nifty500":  NIFTY_180,   # Phase 1 will swap in real 500
    "nifty750":  NIFTY_180,   # Phase 1 will swap in real 750
}


def get_universe(name: str) -> list[str]:
    """Return the symbol list for a named universe. Raises if unknown."""
    name = name.lower().strip()
    if name not in UNIVERSES:
        raise ValueError(
            f"Unknown universe '{name}'. Known: {sorted(UNIVERSES.keys())}"
        )
    return list(UNIVERSES[name])


def get_universe_at_date(name: str, as_of_date: str,
                         ohlcv_loader=None) -> list[str]:
    """Return universe symbols that were TRADEABLE on `as_of_date`.

    Drops survivorship bias by intersecting the static universe list with
    symbols that had bhavcopy data on that date. Stocks delisted before
    the date OR not yet listed are excluded.

    Args:
        name:        universe name (e.g. 'nifty100')
        as_of_date:  'YYYY-MM-DD'
        ohlcv_loader: callable(symbol, days) -> DataFrame. Defaults to
            anju_core.data_layer.get_ohlcv. Tests inject a mock.

    Returns: list of symbols that had data on/around as_of_date.

    Note: this is an APPROXIMATION of true point-in-time membership.
    True NSE constituent history (e.g. "was XYZ in NIFTY 500 on date Y?")
    requires monthly archive scraping — Phase 2 enhancement if needed.
    For Phase 1 backtests this is sufficient to drop dead symbols.
    """
    base = get_universe(name)

    if ohlcv_loader is None:
        from anju_core.data_layer import get_ohlcv as _g
        ohlcv_loader = _g

    import pandas as pd
    target = pd.to_datetime(as_of_date)

    out: list[str] = []
    for sym in base:
        try:
            df = ohlcv_loader(sym, days=10)
            if df is None or df.empty:
                continue
            df.index = pd.to_datetime(df.index)
            # Symbol must have at least one bar within ±10 days of target.
            window = (df.index >= target - pd.Timedelta(days=10)) & \
                     (df.index <= target + pd.Timedelta(days=10))
            if window.any():
                out.append(sym)
        except Exception:
            continue
    return out


def get_universe_with_cache(name: str, as_of_date: str,
                            cache: dict | None = None,
                            ohlcv_loader=None) -> list[str]:
    """Same as get_universe_at_date but memoised by date.
    Used by backtest engine to avoid re-checking the same date repeatedly."""
    if cache is None:
        cache = {}
    key = (name, as_of_date)
    if key not in cache:
        cache[key] = get_universe_at_date(name, as_of_date, ohlcv_loader)
    return list(cache[key])
