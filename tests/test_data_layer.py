"""Tests for anju_core.data_layer.

All tests are offline — no network calls, no real bhavcopy fetches.
The DB lives in a tmp_path per test via the ANJU_HISTORICAL_DB env var.
"""

import io
import os
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own temp historical.db. Avoids polluting real data/."""
    db = tmp_path / "test_historical.db"
    monkeypatch.setenv("ANJU_HISTORICAL_DB", str(db))
    # Re-import to pick up the new env var
    import importlib
    import anju_core.data_layer as dl
    importlib.reload(dl)
    yield dl


# ── _bhavcopy_url ─────────────────────────────────────────────────────────────

def test_bhavcopy_url_format(isolated_db):
    url = isolated_db._bhavcopy_url("2026-05-13")
    assert url == (
        "https://nsearchives.nseindia.com/content/cm/"
        "BhavCopy_NSE_CM_0_0_0_20260513_F_0000.csv.zip"
    )


# ── _weekday_dates ────────────────────────────────────────────────────────────

def test_weekday_dates_excludes_weekends(isolated_db):
    # Mon 2026-05-11 → Sun 2026-05-17
    start = datetime(2026, 5, 11)
    end   = datetime(2026, 5, 17)
    out = isolated_db._weekday_dates(start, end)
    assert out == [
        "2026-05-11",  # Mon
        "2026-05-12",  # Tue
        "2026-05-13",  # Wed
        "2026-05-14",  # Thu
        "2026-05-15",  # Fri
    ]


def test_weekday_dates_single_day(isolated_db):
    # Saturday only → empty
    sat = datetime(2026, 5, 16)
    assert isolated_db._weekday_dates(sat, sat) == []


# ── _init_db + _dates_missing ─────────────────────────────────────────────────

def test_init_db_creates_tables(isolated_db):
    con = isolated_db._db_connect()
    isolated_db._init_db(con)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    con.close()
    assert {"daily_ohlcv", "bhavcopy_log"} <= names


def test_dates_missing_empty_db_returns_all(isolated_db):
    con = isolated_db._db_connect()
    isolated_db._init_db(con)
    out = isolated_db._dates_missing(con, ["2026-05-13", "2026-05-14"])
    con.close()
    assert sorted(out) == ["2026-05-13", "2026-05-14"]


def test_dates_missing_partial_known(isolated_db):
    con = isolated_db._db_connect()
    isolated_db._init_db(con)
    con.execute(
        "INSERT INTO bhavcopy_log(date, rows, source) VALUES (?,?,?)",
        ("2026-05-13", 100, "bhavcopy"),
    )
    con.commit()
    out = isolated_db._dates_missing(con, ["2026-05-13", "2026-05-14"])
    con.close()
    assert out == ["2026-05-14"]


# ── _parse_bhavcopy ───────────────────────────────────────────────────────────

def _make_new_format_zip(rows: list[dict]) -> bytes:
    """Create a fake new-format NSE bhavcopy ZIP from row dicts."""
    df = pd.DataFrame(rows)
    csv_bytes = df.to_csv(index=False).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("BhavCopy_NSE_CM_test.csv", csv_bytes)
    return buf.getvalue()


def test_parse_bhavcopy_new_format(isolated_db):
    rows = [
        {"TckrSymb": "RELIANCE", "SctySrs": "EQ",
         "OpnPric": 1400.0, "HghPric": 1410.0,
         "LwPric": 1390.0, "ClsPric": 1405.0, "TtlTradgVol": 1000000},
        {"TckrSymb": "TCS", "SctySrs": "EQ",
         "OpnPric": 3500.0, "HghPric": 3520.0,
         "LwPric": 3490.0, "ClsPric": 3510.0, "TtlTradgVol": 500000},
        # Non-EQ row — should be filtered out
        {"TckrSymb": "FILTERED", "SctySrs": "BE",
         "OpnPric": 100.0, "HghPric": 105.0,
         "LwPric": 99.0, "ClsPric": 102.0, "TtlTradgVol": 1000},
    ]
    zip_bytes = _make_new_format_zip(rows)

    out = isolated_db._parse_bhavcopy(zip_bytes, "2026-05-13")
    assert out is not None
    assert len(out) == 2
    assert set(out["symbol"]) == {"RELIANCE", "TCS"}
    assert (out["date"] == "2026-05-13").all()
    # Float prices preserved, volume coerced to int
    assert out.loc[out["symbol"] == "RELIANCE", "close"].iloc[0] == 1405.0
    assert out.loc[out["symbol"] == "RELIANCE", "volume"].dtype.kind in "iu"


def test_parse_bhavcopy_old_format(isolated_db):
    rows = [
        {"SYMBOL": "INFY", "SERIES": "EQ",
         "OPEN": 1500.0, "HIGH": 1510.0,
         "LOW": 1495.0, "CLOSE": 1505.0, "TOTTRDQTY": 800000},
    ]
    df = pd.DataFrame(rows)
    csv_bytes = df.to_csv(index=False).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cm130526bhav.csv", csv_bytes)

    out = isolated_db._parse_bhavcopy(buf.getvalue(), "2026-05-13")
    assert out is not None
    assert len(out) == 1
    assert out.iloc[0]["symbol"] == "INFY"
    assert out.iloc[0]["close"] == 1505.0


def test_parse_bhavcopy_invalid_zip_returns_none(isolated_db):
    assert isolated_db._parse_bhavcopy(b"not a zip file", "2026-05-13") is None


def test_parse_bhavcopy_unknown_format_returns_none(isolated_db):
    df = pd.DataFrame([{"random_col": 1, "other_col": 2}])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("weird.csv", df.to_csv(index=False).encode())
    assert isolated_db._parse_bhavcopy(buf.getvalue(), "2026-05-13") is None


# ── get_ohlcv ─────────────────────────────────────────────────────────────────

def test_get_ohlcv_from_local_db(isolated_db):
    con = isolated_db._db_connect()
    isolated_db._init_db(con)
    # Insert enough rows (≥30) to skip yfinance fallback
    from datetime import timedelta
    base = datetime(2025, 1, 1)
    rows = []
    for i in range(40):
        d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        rows.append(("FAKE", d, 100 + i, 102 + i, 99 + i, 101 + i, 1000 * (i + 1)))
    con.executemany(
        "INSERT INTO daily_ohlcv(symbol,date,open,high,low,close,volume) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()

    df = isolated_db.get_ohlcv("FAKE.NS", days=500, fallback_yf=False)
    assert len(df) == 40
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df["Close"].iloc[0] == 101.0
    assert df["Close"].iloc[-1] == 140.0


def test_get_ohlcv_missing_no_fallback_returns_empty(isolated_db):
    df = isolated_db.get_ohlcv("NONEXISTENT_SYMBOL_XYZ", days=100, fallback_yf=False)
    assert df.empty
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
