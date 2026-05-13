#!/usr/bin/env python3
"""
anju_core.regime — Market Regime Classifier

Forked from anju-trader/regime_detector.py without behavioural changes.
Only difference: imports get_ohlcv / get_index from anju_core.data_layer
instead of anju-trader's flat module layout.

Classifies the Indian equity market into one of 4 states:
  Trending  — cur > MA20 > MA50 > MA200, low volatility, broad breadth
  Sideways  — mixed MAs, moderate conditions
  Volatile  — high 10-day range, elevated fear
  Bear      — price below MA200

Fail-safe: data outage defaults to Bear (strict, min_score 9). A network
blip can never accidentally widen the scanner.

CLI:
    python -m anju_core.regime                  # print regime JSON
    python -m anju_core.regime --github-env     # write to $GITHUB_ENV
    python -m anju_core.regime --quiet          # JSON only, no prints

API:
    from anju_core.regime import detect
    regime = detect()
    # regime['state'] in {'Trending', 'Sideways', 'Volatile', 'Bear'}
"""

import argparse
import json
import os
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from anju_core.data_layer import get_index, get_ohlcv  # noqa: F401

warnings.filterwarnings("ignore")


# ── State definitions ─────────────────────────────────────────────────────────
#
# Priority order (first match wins):
#   Bear     → cur < MA200
#   Volatile → 10d vol > 5.5% AND cur > MA200
#   Trending → cur > MA20 > MA50 > MA200, vol < 4.5%, breadth > 55%
#   Sideways → everything else

STATES = {
    "Trending": {
        "emoji":        "🟢",
        "scanner_mode": "strict",
        "min_score":    6,
        "recommendations": [
            "Normal scan — momentum is your friend",
            "Focus on stage 2 breakouts and pocket pivots",
            "Full position sizing permitted",
        ],
    },
    "Sideways": {
        "emoji":        "🟡",
        "scanner_mode": "aggressive",
        "min_score":    4,
        "recommendations": [
            "Wider net — look for range breakouts and early bases",
            "Reduce position sizes by 25%",
            "Favour stocks with strong relative strength",
        ],
    },
    "Volatile": {
        "emoji":        "🟠",
        "scanner_mode": "strict",
        "min_score":    8,
        "recommendations": [
            "High bar — only highest-conviction setups",
            "Reduce position sizes by 50%",
            "Widen stops to absorb intraday noise",
        ],
    },
    "Bear": {
        "emoji":        "🔴",
        "scanner_mode": "strict",
        "min_score":    9,
        "recommendations": [
            "Avoid new longs — cash is a position",
            "Watch for short setups or defensive sectors",
            "Only act on score ≥9 with confirmed RS",
        ],
    },
}

# Nifty 50 constituents — used for accurate advance/decline breadth.
_NIFTY50_BREADTH_TICKERS = [
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","BHARTIARTL.NS","ICICIBANK.NS",
    "INFOSYS.NS","SBIN.NS","HINDUNILVR.NS","ITC.NS","LT.NS",
    "KOTAKBANK.NS","AXISBANK.NS","BAJFINANCE.NS","ASIANPAINT.NS","MARUTI.NS",
    "TITAN.NS","SUNPHARMA.NS","ULTRACEMCO.NS","NTPC.NS","ADANIPORTS.NS",
    "WIPRO.NS","HCLTECH.NS","POWERGRID.NS","M&M.NS","NESTLEIND.NS",
    "TATAMOTORS.NS","BAJAJFINSV.NS","ONGC.NS","JSWSTEEL.NS","TATACONSUM.NS",
    "GRASIM.NS","TECHM.NS","COALINDIA.NS","INDUSINDBK.NS","CIPLA.NS",
    "DRREDDY.NS","DIVISLAB.NS","BPCL.NS","EICHERMOT.NS","APOLLOHOSP.NS",
    "TATAPOWER.NS","HINDALCO.NS","SBILIFE.NS","HDFCLIFE.NS","BAJAJ-AUTO.NS",
    "BEL.NS","HAL.NS","ADANIENT.NS","SHRIRAMFIN.NS","VEDL.NS",
]

# Sector ETFs — display only (not used for breadth_pct calculation)
SECTOR_ETFS = {
    "Banking": "^NSEBANK",
    "IT":      "^CNXIT",
    "Pharma":  "^CNXPHARMA",
    "Auto":    "^CNXAUTO",
    "Metal":   "^CNXMETAL",
    "Energy":  "^CNXENERGY",
    "FMCG":    "^CNXFMCG",
    "Infra":   "^CNXINFRA",
}


# ── Data fetching ─────────────────────────────────────────────────────────────

def _fetch_nifty() -> dict:
    """Fetch Nifty 1y data and compute MAs + volatility."""
    try:
        df = get_index("^NSEI", days=365)
        if df is None or len(df) < 50:
            return {}

        close = df["Close"].astype(float)
        cur   = float(close.iloc[-1])

        ma20  = float(close.rolling(20).mean().iloc[-1])  if len(close) >= 20  else None
        ma50  = float(close.rolling(50).mean().iloc[-1])  if len(close) >= 50  else None
        ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

        # 10-day high-low range as volatility proxy
        hi10    = float(close.iloc[-10:].max())
        lo10    = float(close.iloc[-10:].min())
        vol_pct = round((hi10 - lo10) / lo10 * 100, 2)

        def pct_from(ma):
            return round((cur - ma) / ma * 100, 1) if ma else None

        return {
            "price":         round(cur, 2),
            "ma20":          round(ma20,  2) if ma20  else None,
            "ma50":          round(ma50,  2) if ma50  else None,
            "ma200":         round(ma200, 2) if ma200 else None,
            "vol_10d_pct":   vol_pct,
            "above_ma20":    cur > ma20  if ma20  else None,
            "above_ma50":    cur > ma50  if ma50  else None,
            "above_ma200":   cur > ma200 if ma200 else None,
            "ma20_pct":      pct_from(ma20),
            "ma50_pct":      pct_from(ma50),
            "ma200_pct":     pct_from(ma200),
            "ma20_rising":   float(close.rolling(20).mean().iloc[-1]) >
                             float(close.rolling(20).mean().iloc[-5]) if len(close) >= 25 else None,
        }
    except Exception as e:
        return {"error": str(e)}


def _fetch_breadth() -> dict:
    """Compute breadth from all 50 Nifty constituents — true advance/decline count.
    If too many fetches fail (>30% loss), fall back to neutral 50% to avoid trusting partial data."""
    try:
        import yfinance as yf

        def _delta(sym):
            try:
                df = yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=True)
                if df is None or df.empty or len(df) < 2:
                    return None
                c = df["Close"].dropna()
                return float(c.iloc[-1]) - float(c.iloc[-2]) if len(c) >= 2 else None
            except Exception:
                return None

        advances = declines = unchanged = 0
        with ThreadPoolExecutor(max_workers=20) as ex:
            for delta in ex.map(_delta, _NIFTY50_BREADTH_TICKERS):
                if delta is None:
                    continue
                if delta > 0:   advances  += 1
                elif delta < 0: declines  += 1
                else:           unchanged += 1

        fetched = advances + declines + unchanged
        total_attempted = len(_NIFTY50_BREADTH_TICKERS)
        if fetched < total_attempted * 0.7:
            breadth_pct = 50
        else:
            breadth_pct = round(advances / total_attempted * 100)

        # Sector ETFs for display only
        sectors_data = {}
        for name, sym in SECTOR_ETFS.items():
            try:
                df = get_index(sym, days=5)
                if df is not None and len(df) >= 2:
                    chg = ((float(df["Close"].iloc[-1]) - float(df["Close"].iloc[-2]))
                           / float(df["Close"].iloc[-2]) * 100)
                    sectors_data[name] = round(chg, 2)
            except Exception:
                continue

        return {
            "breadth_pct": breadth_pct,
            "positive":    advances,
            "negative":    declines,
            "total":       fetched,
            "sectors":     sectors_data,
        }
    except Exception:
        return {"breadth_pct": 50, "positive": 0, "negative": 0, "total": 0, "sectors": {}}


# ── Classification (pure — easy to test) ──────────────────────────────────────

def _classify(nifty: dict, breadth: dict) -> tuple[str, str]:
    """Return (state, label) from raw data. Pure function — no I/O."""
    cur          = nifty.get("price", 0)
    ma20         = nifty.get("ma20")
    ma50         = nifty.get("ma50")
    ma200        = nifty.get("ma200")
    vol          = nifty.get("vol_10d_pct", 3.0)
    above_ma200  = nifty.get("above_ma200", True)
    above_ma50   = nifty.get("above_ma50", True)
    ma20_rising  = nifty.get("ma20_rising", True)
    breadth_pct  = breadth.get("breadth_pct", 50)

    # Bear
    if not above_ma200:
        if vol > 5.5:
            return "Bear", "Bear + High Volatility — stay in cash"
        return "Bear", "Downtrend below MA200 — avoid new longs"

    # Volatile
    if vol > 5.5:
        return "Volatile", f"Volatile ({vol}% 10d range) — high uncertainty"

    # Trending
    if (ma20 and ma50 and ma200 and
            cur > ma20 > ma50 > ma200 and
            vol < 4.5 and
            breadth_pct >= 55 and
            ma20_rising):
        if vol < 2.5:
            return "Trending", "Strong uptrend — low volatility, broad breadth"
        return "Trending", "Uptrend — healthy momentum and breadth"

    # Sideways
    if above_ma50 and not (ma20 and ma50 and cur > ma20 > ma50):
        return "Sideways", "Mixed signals — MA20 below MA50, range-bound"

    if above_ma200 and not above_ma50:
        return "Sideways", "Pullback — between MA50 and MA200, watch support"

    if breadth_pct < 45:
        return "Sideways", f"Narrow breadth ({breadth_pct}% positive) — selective market"

    return "Sideways", "Sideways — no clear directional edge"


# ── Public API ────────────────────────────────────────────────────────────────

def detect(quiet: bool = False) -> dict:
    """Detect current market regime.

    Returns dict with: state, label, emoji, scanner_mode, min_score,
    recommendations, data, detected_at.

    Fail-safe: data outage → Bear (strict, min_score 9).
    """
    if not quiet:
        print("  Fetching Nifty data...", end="", flush=True)
    nifty = _fetch_nifty()
    if not quiet:
        print(" ✓" if nifty and "price" in nifty else " ✗")

    if not quiet:
        print("  Fetching breadth...", end="", flush=True)
    breadth = _fetch_breadth()
    if not quiet:
        print(f" ✓ ({breadth.get('positive', 0)}/{breadth.get('total', 0)} stocks up)")

    if "price" not in nifty:
        err = nifty.get("error", "unknown")
        state, label = "Bear", f"Could not fetch Nifty data ({err}) — fail-safe to Bear"
    else:
        state, label = _classify(nifty, breadth)

    meta = STATES[state]

    regime = {
        "state":           state,
        "label":           label,
        "emoji":           meta["emoji"],
        "scanner_mode":    meta["scanner_mode"],
        "min_score":       meta["min_score"],
        "recommendations": meta["recommendations"],
        "data": {
            **nifty,
            "breadth_pct": breadth.get("breadth_pct", 50),
            "sectors":     breadth.get("sectors", {}),
        },
        "detected_at": datetime.now().isoformat(),
    }

    if not quiet:
        print(f"  → {meta['emoji']} {state}: {label}")
        print(f"  → Scanner: mode={meta['scanner_mode']}  min_score={meta['min_score']}")

    return regime


def write_github_env(regime: dict) -> None:
    """Write regime vars to $GITHUB_ENV for use in later workflow steps."""
    gh_env = os.getenv("GITHUB_ENV", "")
    if not gh_env:
        print("⚠️  GITHUB_ENV not set — printing instead")
        print(f"REGIME_STATE={regime['state']}")
        print(f"REGIME_SCANNER_MODE={regime['scanner_mode']}")
        print(f"REGIME_MIN_SCORE={regime['min_score']}")
        return

    with open(gh_env, "a") as f:
        f.write(f"REGIME_STATE={regime['state']}\n")
        f.write(f"REGIME_SCANNER_MODE={regime['scanner_mode']}\n")
        f.write(f"REGIME_MIN_SCORE={regime['min_score']}\n")
        f.write(f"REGIME_LABEL={regime['label']}\n")
    print("  ✅ Wrote regime vars to GITHUB_ENV")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market regime detector")
    parser.add_argument("--github-env", action="store_true",
                        help="Write REGIME_* vars to $GITHUB_ENV (for GitHub Actions)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress progress prints, output JSON only")
    args = parser.parse_args()

    if not args.quiet:
        print(f"\n🧭 Regime Detector — {datetime.now().strftime('%d %b %Y, %H:%M')}")

    regime = detect(quiet=args.quiet)

    if args.github_env:
        write_github_env(regime)

    out = {k: v for k, v in regime.items() if k != "data"}
    print(json.dumps(out, indent=2))
