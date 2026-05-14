#!/usr/bin/env python3
"""
anju_core.data_layer — NSE Historical Data Cache

Forked from anju-trader/data_layer.py without behavioural changes.
Only differences:
  - DB lives at <project_root>/data/historical.db (not project root)
  - ANJU_HISTORICAL_DB env var overrides the path (useful in tests)

Downloads NSE daily bhavcopy (one ZIP = all ~2000 equity symbols) and stores
in historical.db SQLite. Replaces per-symbol yfinance calls in scanner code.

1 bhavcopy download = ~2000 symbols  →  ~750x fewer HTTP calls vs yfinance.

CLI:
    python -m anju_core.data_layer --refresh             # last 365 calendar days
    python -m anju_core.data_layer --refresh --days 730  # last 2 years
    python -m anju_core.data_layer --stats               # row counts + date range
    python -m anju_core.data_layer --symbol RELIANCE     # spot-check last 10 rows

API:
    from anju_core import get_ohlcv, get_index, refresh_daily

    df = get_ohlcv("RELIANCE.NS", days=500)   # pd.DataFrame OHLCV, date index
    df = get_index("^NSEI", days=500)         # Nifty 50 (yfinance, cached)
    refresh_daily(days_back=365)              # idempotent — skips known dates
"""

import argparse
import io
import os
import sqlite3
import tempfile
import warnings
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as _Retry

warnings.filterwarnings("ignore")


# ── DB path resolution ────────────────────────────────────────────────────────
# Default: <project_root>/data/historical.db
# Override: $ANJU_HISTORICAL_DB (useful for tests)
def _resolve_db_path() -> Path:
    if env := os.getenv("ANJU_HISTORICAL_DB"):
        return Path(env)
    # anju_core/data_layer.py → parents[1] is project root
    project_root = Path(__file__).resolve().parents[1]
    db = project_root / "data" / "historical.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


DB_PATH = _resolve_db_path()


# ── HTTP session ──────────────────────────────────────────────────────────────

_retry = _Retry(total=3, backoff_factor=1.0,
                status_forcelist=[429, 500, 502, 503, 504])
_http = requests.Session()
_http.mount("https://", HTTPAdapter(max_retries=_retry))
_http.mount("http://",  HTTPAdapter(max_retries=_retry))
_http.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
})

_NSE_SESSION_WARMED = False


def _warm_nse_session() -> None:
    """Visit nseindia.com once per process to get the session cookie that
    archives.nseindia.com requires. Skips on subsequent calls."""
    global _NSE_SESSION_WARMED
    if _NSE_SESSION_WARMED:
        return
    try:
        _http.get("https://www.nseindia.com/", timeout=10)
        _NSE_SESSION_WARMED = True
    except Exception:
        pass


# ── Database ──────────────────────────────────────────────────────────────────

def _db_connect() -> sqlite3.Connection:
    # Re-resolve the path on each call so $ANJU_HISTORICAL_DB env var
    # changes (e.g. in tests) take effect immediately.
    con = sqlite3.connect(_resolve_db_path(), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _init_db(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE TABLE IF NOT EXISTS daily_ohlcv (
            symbol  TEXT NOT NULL,
            date    TEXT NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            PRIMARY KEY (symbol, date)
        );
        CREATE INDEX IF NOT EXISTS idx_do_symbol ON daily_ohlcv(symbol);
        CREATE INDEX IF NOT EXISTS idx_do_date   ON daily_ohlcv(date);

        CREATE TABLE IF NOT EXISTS bhavcopy_log (
            date       TEXT PRIMARY KEY,
            rows       INTEGER,
            source     TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    con.commit()


# ── Date helpers ──────────────────────────────────────────────────────────────

def _ist_today() -> datetime:
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def _weekday_dates(start: datetime, end: datetime) -> list:
    """Return list of 'YYYY-MM-DD' for Mon–Fri between start and end inclusive."""
    dates = []
    cur = start.date() if hasattr(start, "date") else start
    end = end.date()   if hasattr(end,   "date") else end
    while cur <= end:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return dates


def _dates_missing(con: sqlite3.Connection, date_strs: list) -> list:
    """Return dates not yet present in bhavcopy_log."""
    if not date_strs:
        return []
    known = {
        r[0] for r in con.execute(
            "SELECT date FROM bhavcopy_log WHERE date IN ({})".format(
                ",".join("?" * len(date_strs))
            ),
            date_strs,
        ).fetchall()
    }
    return [d for d in date_strs if d not in known]


# ── Bhavcopy download ─────────────────────────────────────────────────────────

def _bhavcopy_url(date_str: str) -> str:
    """NSE new-format bhavcopy URL (since ~2022)."""
    d = date_str.replace("-", "")
    return (
        f"https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip"
    )


def _parse_bhavcopy(raw_bytes: bytes, date_str: str) -> pd.DataFrame | None:
    """Parse bhavcopy ZIP → rows with columns [symbol, date, open, high, low,
    close, volume]. Handles both the new (TckrSymb / OpnPric …) and old
    (SYMBOL / OPEN …) CSV formats. Returns None on failure."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            csv_name = next((n for n in z.namelist() if n.endswith(".csv")), None)
            if not csv_name:
                return None
            with z.open(csv_name) as f:
                df = pd.read_csv(f, low_memory=False)
    except Exception:
        return None

    if "TckrSymb" in df.columns:
        eq = df[df.get("SctySrs", df.get("Sgmt", pd.Series())).astype(str).str.strip() == "EQ"].copy()
        if eq.empty:
            eq = df[df.get("FinInstrmTp", pd.Series()).astype(str).str.contains("EQ", na=False)].copy()
        if eq.empty:
            eq = df.copy()
        eq = eq.rename(columns={
            "TckrSymb": "symbol",
            "OpnPric":  "open",
            "HghPric":  "high",
            "LwPric":   "low",
            "ClsPric":  "close",
            "TtlTradgVol": "volume",
        })
        eq["symbol"] = eq["symbol"].astype(str).str.strip()
    elif "SYMBOL" in df.columns:
        eq = df[df.get("SERIES", df.get("Series", pd.Series())).astype(str).str.strip() == "EQ"].copy()
        eq = eq.rename(columns={
            "SYMBOL":    "symbol",
            "OPEN":      "open",
            "HIGH":      "high",
            "LOW":       "low",
            "CLOSE":     "close",
            "TOTTRDQTY": "volume",
        })
        eq["symbol"] = eq["symbol"].astype(str).str.strip()
    else:
        return None

    needed = {"symbol", "open", "high", "low", "close", "volume"}
    if not needed.issubset(eq.columns):
        return None

    eq = eq[list(needed)].copy()
    eq["date"] = date_str

    for col in ("open", "high", "low", "close"):
        eq[col] = pd.to_numeric(eq[col], errors="coerce")
    eq["volume"] = pd.to_numeric(eq["volume"], errors="coerce").fillna(0).astype(int)

    eq = eq.dropna(subset=["open", "high", "low", "close"])
    eq = eq[eq["symbol"].str.len() > 0]
    return eq[["symbol", "date", "open", "high", "low", "close", "volume"]]


def _fetch_and_store_bhavcopy(con: sqlite3.Connection, date_str: str) -> int:
    """Download + parse + store bhavcopy for one trading date. Returns row count
    inserted (0 on failure — date is still logged to avoid re-tries)."""
    _warm_nse_session()
    url = _bhavcopy_url(date_str)
    try:
        r = _http.get(url, timeout=30)
        if r.status_code == 404:
            con.execute(
                "INSERT OR IGNORE INTO bhavcopy_log (date, rows, source) VALUES (?,?,?)",
                (date_str, 0, "holiday"),
            )
            con.commit()
            return 0
        if not r.ok:
            return 0
        raw = r.content
    except Exception:
        return 0

    df = _parse_bhavcopy(raw, date_str)
    if df is None or df.empty:
        con.execute(
            "INSERT OR IGNORE INTO bhavcopy_log (date, rows, source) VALUES (?,?,?)",
            (date_str, 0, "parse_error"),
        )
        con.commit()
        return 0

    rows = df.to_dict("records")
    con.executemany(
        """INSERT OR IGNORE INTO daily_ohlcv
               (symbol, date, open, high, low, close, volume)
           VALUES (:symbol, :date, :open, :high, :low, :close, :volume)""",
        rows,
    )
    con.execute(
        "INSERT OR REPLACE INTO bhavcopy_log (date, rows, source) VALUES (?,?,?)",
        (date_str, len(rows), "bhavcopy"),
    )
    con.commit()
    return len(rows)


# ── yfinance fallback ─────────────────────────────────────────────────────────

def _yf_fetch_symbol(symbol: str, days: int) -> pd.DataFrame | None:
    """Single-symbol yfinance fetch → normalised DataFrame.
    Used when bhavcopy has insufficient rows (newly listed, delisted, indices)."""
    try:
        import yfinance as yf
        yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_dl_"))
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=f"{max(days // 365 + 1, 2)}y", auto_adjust=True)
        if df is None or len(df) < 10:
            return None
        try:    df.index = df.index.tz_convert(None)
        except Exception: pass
        try:    df.index = df.index.tz_localize(None)
        except Exception: pass
        df.index = pd.to_datetime([str(x)[:10] for x in df.index])
        required = ["Open", "High", "Low", "Close", "Volume"]
        missing_cols = [c for c in required if c not in df.columns]
        if missing_cols:
            if "Close" in missing_cols and "Adj Close" in df.columns:
                df["Close"] = df["Adj Close"]
                missing_cols.remove("Close")
            if missing_cols:
                raise ValueError(f"Missing columns from yfinance: {missing_cols}")
        return df[required].copy()
    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def refresh_daily(days_back: int = 365, verbose: bool = True) -> int:
    """Download missing bhavcopy files for the last `days_back` calendar days.
    Idempotent — skips dates already logged. Returns total new rows inserted."""
    con = _db_connect()
    _init_db(con)

    end   = _ist_today()
    start = end - timedelta(days=days_back)
    all_dates = _weekday_dates(start, end)
    missing   = _dates_missing(con, all_dates)

    if verbose:
        print(f"  📥 refresh_daily: {len(missing)} dates to fetch "
              f"(of {len(all_dates)} weekdays in last {days_back}d)")

    total = 0
    for i, d in enumerate(missing, 1):
        n = _fetch_and_store_bhavcopy(con, d)
        total += n
        if verbose:
            status = f"{n} rows" if n else "holiday/skip"
            print(f"  [{i:>3}/{len(missing)}] {d}  {status}")

    con.close()
    if verbose:
        print(f"  ✅ Done — {total} new rows inserted")
    return total


def get_ohlcv(symbol: str, days: int = 500, fallback_yf: bool = True) -> pd.DataFrame:
    """Return daily OHLCV DataFrame for `symbol` (with or without .NS suffix).

    Columns: Open, High, Low, Close, Volume
    Index:   datetime (date only)

    Data source priority:
      1. historical.db (bhavcopy)
      2. yfinance (if fallback_yf=True and DB has < 30 rows for symbol)
    """
    bare = symbol.upper().replace(".NS", "").replace(".BSE", "")

    con = _db_connect()
    _init_db(con)

    cutoff = (_ist_today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    rows = con.execute(
        """SELECT date, open, high, low, close, volume
           FROM daily_ohlcv
           WHERE symbol = ? AND date >= ?
           ORDER BY date""",
        (bare, cutoff),
    ).fetchall()
    con.close()

    if len(rows) >= 30:
        df = pd.DataFrame(rows, columns=["date", "Open", "High", "Low", "Close", "Volume"])
        df.index = pd.to_datetime(df["date"])
        df = df.drop(columns="date")
        return df.tail(days)

    if fallback_yf:
        print(f"  ℹ️  {bare}: bhavcopy has {len(rows)} rows — falling back to yfinance", flush=True)
        df = _yf_fetch_symbol(symbol if "." in symbol else symbol + ".NS", days)
        if df is not None:
            return df.tail(days)

    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def get_index(symbol: str = "^NSEI", days: int = 500) -> pd.DataFrame:
    """Return OHLCV for an index (Nifty='^NSEI', BankNifty='^NSEBANK',
    VIX='^INDIAVIX'). Always via yfinance — indices are not in equity bhavcopy."""
    df = _yf_fetch_symbol(symbol, days)
    if df is not None:
        return df.tail(days)
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def get_universe(min_rows: int = 100) -> list:
    """Return symbols that have at least `min_rows` trading days in the DB.
    Useful for building survivorship-clean historical universes (Phase 1)."""
    con = _db_connect()
    _init_db(con)
    rows = con.execute(
        "SELECT symbol FROM daily_ohlcv GROUP BY symbol HAVING COUNT(*) >= ?",
        (min_rows,),
    ).fetchall()
    con.close()
    return [r[0] + ".NS" for r in rows]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_stats() -> None:
    con = _db_connect()
    _init_db(con)
    total    = con.execute("SELECT COUNT(*) FROM daily_ohlcv").fetchone()[0]
    syms     = con.execute("SELECT COUNT(DISTINCT symbol) FROM daily_ohlcv").fetchone()[0]
    dates    = con.execute("SELECT MIN(date), MAX(date) FROM daily_ohlcv").fetchone()
    logged   = con.execute("SELECT COUNT(*) FROM bhavcopy_log").fetchone()[0]
    holidays = con.execute("SELECT COUNT(*) FROM bhavcopy_log WHERE source='holiday'").fetchone()[0]
    con.close()
    print(f"\n📊 historical.db @ {DB_PATH}")
    print(f"  Symbols     : {syms:,}")
    print(f"  Total rows  : {total:,}")
    print(f"  Date range  : {dates[0]}  →  {dates[1]}")
    print(f"  Days logged : {logged} ({holidays} holidays/skipped)")


def _spot_check(symbol: str, days: int = 10) -> None:
    df = get_ohlcv(symbol, days=days)
    if df.empty:
        print(f"  No data for {symbol}")
        return
    print(f"\n  {symbol} — last {min(days, len(df))} rows (from DB or yfinance):")
    print(df.tail(days).to_string())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NSE data layer — bhavcopy cache")
    parser.add_argument("--refresh", action="store_true",
                        help="Download missing bhavcopy dates")
    parser.add_argument("--days", type=int, default=365,
                        help="Calendar days to look back for --refresh (default 365)")
    parser.add_argument("--stats", action="store_true",
                        help="Print DB statistics")
    parser.add_argument("--symbol", type=str, default="",
                        help="Spot-check OHLCV for a symbol (e.g. RELIANCE)")
    args = parser.parse_args()

    if args.refresh:
        refresh_daily(days_back=args.days)
    if args.stats:
        _print_stats()
    if args.symbol:
        _spot_check(args.symbol, days=10)
    if not any([args.refresh, args.stats, args.symbol]):
        parser.print_help()
