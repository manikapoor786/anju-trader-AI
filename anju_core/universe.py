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


# ─────────────────────────────────────────────────────────────────────
# Phase 1.9: real nifty500 / nifty750 via NSE official constituents.
#
# Source: nsepython-fetched lists ("NIFTY 500" + "NIFTY MICROCAP 250"),
# cached to data/nse_universe_cache.csv. Refreshed weekly via the
# fetch_nse_constituents() helper below.
#
# Layout of the cache file:
#   rows  1-500  =  NIFTY 500 constituents (ranked by market cap)
#   rows 501-750 =  NIFTY MICROCAP 250 (stocks ranked 501-750 by NSE)
# Combined: ~750 unique symbols covering the full liquid Indian universe.
# ─────────────────────────────────────────────────────────────────────

import os
from pathlib import Path


def _cache_path() -> Path:
    """Return path to the NSE universe cache CSV (anju-trader-AI/data/)."""
    return Path(__file__).resolve().parents[1] / "data" / "nse_universe_cache.csv"


def _load_cache() -> list[str]:
    """Load the cached NSE 750 symbol list. Returns empty list if missing."""
    p = _cache_path()
    if not p.exists():
        return []
    try:
        return [
            line.strip() + ".NS"
            for line in p.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    except Exception:
        return []


def _ensure_cache_fresh(max_age_days: int = 7) -> list[str]:
    """Return the cached list, refreshing from NSE if older than max_age_days.

    Falls back to the cached file if nsepython is unavailable or fetch fails.
    Used at import time so live signals always have a fresh universe.
    """
    p = _cache_path()
    if p.exists():
        age_days = (
            (os.path.getmtime(p)
             - 0) / 86400
        )
        import time
        age_days = (time.time() - os.path.getmtime(p)) / 86400
        if age_days < max_age_days:
            return _load_cache()

    # Try to refresh
    fetched = fetch_nse_constituents()
    if fetched and len(fetched) >= 500:
        return fetched
    # Fall back to whatever we have cached
    return _load_cache()


def fetch_nse_constituents() -> list[str] | None:
    """Fetch live "NIFTY 500" + "NIFTY MICROCAP 250" from NSE India.

    Writes the result to data/nse_universe_cache.csv on success.
    Returns None if nsepython is unavailable or fetch fails — caller
    should fall back to the cached list.
    """
    try:
        from nsepython import nsefetch
    except ImportError:
        return None

    TARGET_INDICES = ["NIFTY 500", "NIFTY MICROCAP 250"]
    all_symbols: list[str] = []
    for index_name in TARGET_INDICES:
        try:
            url = (
                "https://www.nseindia.com/api/equity-stockIndices"
                f"?index={index_name.replace(' ', '%20')}"
            )
            data = nsefetch(url)
            records = data.get("data", [])
            syms = [
                r["symbol"].strip() + ".NS"
                for r in records
                if r.get("symbol", "").strip()
                and not r["symbol"].startswith("NIFTY")
            ]
            all_symbols.extend(syms)
        except Exception:
            continue

    if len(all_symbols) < 500:
        return None

    unique = list(dict.fromkeys(all_symbols))
    try:
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(s.replace(".NS", "") for s in unique) + "\n")
    except Exception:
        pass
    return unique


# Build real nifty500/nifty750 from cache at import time.
# Falls back to NIFTY_180 if the cache is missing.
_NSE_FULL = _load_cache()
if _NSE_FULL:
    NIFTY_500 = _NSE_FULL[:500]
    NIFTY_750 = _NSE_FULL                       # full 750 incl. microcaps
    NIFTY_MICROCAP_250 = _NSE_FULL[500:]
else:
    # Defensive fallback — should only hit during fresh checkout before
    # the cache file exists. Real cache committed to git.
    NIFTY_500 = NIFTY_180
    NIFTY_750 = NIFTY_180
    NIFTY_MICROCAP_250 = []


UNIVERSES: dict[str, list[str]] = {
    "nifty50":      NIFTY_50,
    "next50":       NIFTY_NEXT_50,
    "midcap":       NIFTY_MIDCAP,
    "nifty100":     NIFTY_100,
    "nifty180":     NIFTY_180,
    "nifty500":     NIFTY_500,
    "nifty750":     NIFTY_750,
    "microcap250":  NIFTY_MICROCAP_250,
}


# ─────────────────────────────────────────────────────────────────────
# Phase 2.0: market-cap segmentation for risk-aware position sizing.
#
# The Phase 1.8 nifty500 backtest produced -81% drawdown because the same
# 10% position size was applied to smallcaps (where -20% gap-downs are
# common) and large caps alike. Segment-aware sizing caps catastrophic
# trade losses at ~0.4-0.5% of portfolio even when individual stocks
# crater 20%.
# ─────────────────────────────────────────────────────────────────────

# Build symbol → rank index from the cached NSE 750 (ranked by market cap).
_RANK_INDEX: dict[str, int] = {}
for _i, _sym in enumerate(_NSE_FULL):
    _RANK_INDEX[_sym] = _i + 1   # 1-indexed rank


def market_cap_rank(symbol: str) -> int | None:
    """Return 1-750 market-cap rank for a symbol (lower = bigger), or None
    if the symbol isn't in the NSE 500 + Microcap 250 cache."""
    bare_ns = symbol.upper().replace(".BSE", "")
    if not bare_ns.endswith(".NS"):
        bare_ns += ".NS"
    return _RANK_INDEX.get(bare_ns)


def cap_segment(symbol: str) -> str:
    """Return cap segment for sizing rules: 'large' | 'mid' | 'small' |
    'micro' | 'unknown'. Used by backtest + live sizing.

    Cutoffs follow NSE convention:
      large:  rank 1-100   (Nifty 100)
      mid:    rank 101-250 (Nifty Midcap 150)
      small:  rank 251-500 (Nifty Smallcap 250)
      micro:  rank 501-750 (Nifty Microcap 250)
      unknown: symbol not in cache — use the most conservative sizing.
    """
    rank = market_cap_rank(symbol)
    if rank is None:
        return "unknown"
    if rank <= 100:
        return "large"
    if rank <= 250:
        return "mid"
    if rank <= 500:
        return "small"
    return "micro"


# Sizing rules per segment.
# risk_pct: percentage of capital risked per trade (max loss = qty × stop_distance)
# max_position_pct: cap on position size as % of capital
#
# Smaller risk + cap for smaller caps prevents one bad smallcap from
# blowing up the portfolio. Catastrophic loss math:
#   large  -20% × 10% pos = -2.0% portfolio
#   mid    -20% ×  6% pos = -1.2% portfolio
#   small  -20% ×  3% pos = -0.6% portfolio
#   micro  -20% ×  2% pos = -0.4% portfolio
SEGMENT_SIZING: dict[str, dict[str, float]] = {
    "large":   {"risk_pct": 1.0,  "max_position_pct": 10.0},
    "mid":     {"risk_pct": 0.8,  "max_position_pct": 6.0},
    "small":   {"risk_pct": 0.6,  "max_position_pct": 3.0},
    "micro":   {"risk_pct": 0.4,  "max_position_pct": 2.0},
    "unknown": {"risk_pct": 0.4,  "max_position_pct": 2.0},  # conservative
}


def sizing_for_symbol(symbol: str) -> dict[str, float]:
    """Return {risk_pct, max_position_pct} for sizing a trade in `symbol`."""
    return dict(SEGMENT_SIZING[cap_segment(symbol)])


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
