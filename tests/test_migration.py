"""Tests for scripts/migrate_anju_portfolio.py — pure DB integration."""

import json
import sys
from pathlib import Path
import pytest

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from migrate_anju_portfolio import (
    already_migrated,
    get_or_create_regime,
    has_existing_live_signals,
    load_anju_portfolio,
    migrate_one,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    yield con, tmp_path
    con.close()


# ── load_anju_portfolio ─────────────────────────────────────────────────────

def test_load_portfolio_dict_format(tmp_path):
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps({
        "positions": [{"symbol": "X", "qty": 10, "entry": 100}]
    }))
    out = load_anju_portfolio(p)
    assert len(out) == 1
    assert out[0]["symbol"] == "X"


def test_load_portfolio_list_format(tmp_path):
    """Backward compat: bare list also accepted."""
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps([{"symbol": "Y", "qty": 5, "entry": 50}]))
    out = load_anju_portfolio(p)
    assert len(out) == 1


def test_load_portfolio_missing_file(tmp_path):
    assert load_anju_portfolio(tmp_path / "nope.json") == []


# ── get_or_create_regime ────────────────────────────────────────────────────

def test_get_or_create_regime_creates_when_missing(db):
    con, _ = db
    rid = get_or_create_regime(con, "2026-05-10")
    assert rid > 0


def test_get_or_create_regime_reuses_existing(db):
    con, _ = db
    rid1 = get_or_create_regime(con, "2026-05-10")
    rid2 = get_or_create_regime(con, "2026-05-10")
    assert rid1 == rid2


# ── migrate_one ──────────────────────────────────────────────────────────────

def test_migrate_one_creates_signal_and_fill(db):
    con, _ = db
    res = migrate_one(con, {
        "symbol": "RELIANCE", "qty": 50,
        "entry": 1400.5, "stop": 1330, "target1": 1500, "target2": 1600,
        "entry_date": "2026-04-15",
    })
    assert res["status"] == "migrated"
    sig = con.execute(
        "SELECT entry_price, suggested_qty FROM signals_current WHERE symbol='RELIANCE'"
    ).fetchone()
    assert sig["entry_price"] == 1400.5
    assert sig["suggested_qty"] == 50
    fill = con.execute(
        "SELECT fill_price, fill_qty, is_paper FROM fills "
        "WHERE signal_id = (SELECT id FROM signals_current WHERE symbol='RELIANCE')"
    ).fetchone()
    assert fill["fill_price"] == 1400.5
    assert fill["is_paper"] == 0   # live, not paper


def test_migrate_one_strips_ns_suffix(db):
    con, _ = db
    res = migrate_one(con, {
        "symbol": "BEL.NS", "qty": 100, "entry": 200,
        "entry_date": "2026-05-01",
    })
    assert res["symbol"] == "BEL"


def test_migrate_one_skips_zero_qty(db):
    con, _ = db
    res = migrate_one(con, {"symbol": "X", "qty": 0, "entry": 100})
    assert res["status"] == "skipped"
    assert "qty" in res["reason"]


def test_migrate_one_skips_zero_entry(db):
    con, _ = db
    res = migrate_one(con, {"symbol": "X", "qty": 10, "entry": 0})
    assert res["status"] == "skipped"


def test_migrate_one_skips_missing_symbol(db):
    con, _ = db
    res = migrate_one(con, {"qty": 10, "entry": 100})
    assert res["status"] == "skipped"


def test_migrate_one_is_idempotent(db):
    con, _ = db
    pos = {"symbol": "X", "qty": 50, "entry": 100, "entry_date": "2026-04-01"}
    first = migrate_one(con, pos)
    second = migrate_one(con, pos)
    assert first["status"] == "migrated"
    assert second["status"] == "skipped"
    assert "already migrated" in second["reason"]


def test_migrate_one_stores_source_in_breakdown(db):
    con, _ = db
    migrate_one(con, {
        "symbol": "X", "qty": 50, "entry": 100, "entry_date": "2026-04-01",
        "setup": "breakout", "rr": 2.5,
    })
    row = con.execute(
        "SELECT breakdown_json FROM signals_current WHERE symbol='X'"
    ).fetchone()
    breakdown = json.loads(row["breakdown_json"])
    assert breakdown["migrated_from"] == "anju-trader"
    assert "source_pos" in breakdown
    assert breakdown["source_pos"]["setup"] == "breakout"


# ── has_existing_live_signals (safety guard) ────────────────────────────────

def test_has_existing_live_signals_zero_on_empty_db(db):
    con, _ = db
    assert has_existing_live_signals(con) == 0


def test_has_existing_live_signals_counts_only_live(db):
    con, _ = db
    # Live signal
    con.execute("""INSERT INTO regime_snapshots
        (snapshot_date, state, min_score, nifty_close, payload_json)
        VALUES ('2026-05-10', 'Trending', 6, 22000, '{}')""")
    con.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json)
        VALUES ('2026-05-10', 'LIVE', 'SWING', 1, 15, 15, 'BUY',
                100, 95, 10, '{}')""")
    # Backtest signal
    con.execute("""INSERT INTO backtest_runs
        (name, start_date, end_date, universe, mode, capital_inr,
         config_json, status) VALUES
        ('bt','2024-01-01','2024-12-31','x','strict',1e7,'{}','COMPLETED')""")
    btid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json,
         backtest_run_id)
        VALUES ('2026-05-10', 'BT', 'SWING', 1, 15, 15, 'BUY',
                100, 95, 10, '{}', ?)""", (btid,))
    assert has_existing_live_signals(con) == 1   # backtest excluded
