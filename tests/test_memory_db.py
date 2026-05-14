"""Tests for anju_ai.memory.db — migrations + table creation + invariants."""

import json
import sqlite3
import pytest

from anju_ai.memory.db import (
    apply_migrations,
    audit_log,
    connect,
    init_if_needed,
    table_exists,
)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Each test gets its own clean memory.db."""
    p = tmp_path / "memory.db"
    monkeypatch.setenv("ANJU_MEMORY_DB", str(p))
    yield p


def test_apply_migrations_creates_all_tables(db_path):
    con = init_if_needed(db_path)
    expected = {
        "schema_versions", "regime_snapshots", "flows_snapshots",
        "reasoning_traces", "signals", "fills", "outcomes",
        "lessons", "revisions", "news_items", "filings", "audit",
    }
    for t in expected:
        assert table_exists(con, t), f"Table missing: {t}"
    con.close()


def test_signals_current_view_returns_latest(db_path):
    con = init_if_needed(db_path)
    # Insert prerequisite regime
    con.execute("""INSERT INTO regime_snapshots
        (snapshot_date, state, min_score, nifty_close, payload_json)
        VALUES ('2026-05-14', 'Trending', 6, 22000.0, '{}')""")
    regime_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert original signal
    con.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json)
        VALUES ('2026-05-14', 'RELIANCE', 'SWING', ?, 12.0, 12.0,
                'WATCH', 1400.0, 1350.0, 50, '{}')""", (regime_id,))
    sig1_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert correction that supersedes the first
    con.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json,
         supersedes)
        VALUES ('2026-05-14', 'RELIANCE', 'SWING', ?, 15.0, 15.0,
                'BUY', 1400.0, 1350.0, 50, '{}', ?)""", (regime_id, sig1_id))

    # signals_current should return only the corrected row
    rows = con.execute("SELECT verdict, final_score FROM signals_current "
                       "WHERE symbol='RELIANCE'").fetchall()
    assert len(rows) == 1
    assert rows[0]["verdict"] == "BUY"
    assert rows[0]["final_score"] == 15.0
    con.close()


def test_apply_migrations_idempotent(db_path):
    con = init_if_needed(db_path)
    # Second apply must be a no-op
    n_applied = apply_migrations(con)
    assert n_applied == 0
    con.close()


def test_schema_versions_table_records_applied(db_path):
    con = init_if_needed(db_path)
    rows = con.execute("SELECT version, notes FROM schema_versions").fetchall()
    assert len(rows) >= 1
    assert rows[0]["version"] == 1
    con.close()


def test_foreign_keys_enforced(db_path):
    con = init_if_needed(db_path)
    # Trying to insert a fill referencing non-existent signal must fail
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("""INSERT INTO fills
            (signal_id, fill_date, fill_price, fill_qty, gross_value)
            VALUES (99999, '2026-05-14', 100.0, 10, 1000.0)""")
    con.close()


def test_audit_log_appends_row(db_path):
    con = init_if_needed(db_path)
    audit_id = audit_log(
        con, event_type="SIGNAL_GENERATED",
        summary="Generated 17 signals for nifty500",
        severity="INFO", payload_json=json.dumps({"count": 17}),
    )
    assert audit_id > 0
    row = con.execute("SELECT event_type, severity, summary FROM audit "
                      "WHERE id=?", (audit_id,)).fetchone()
    assert row["event_type"] == "SIGNAL_GENERATED"
    assert row["severity"] == "INFO"
    assert "17 signals" in row["summary"]
    con.close()


def test_wal_mode_set(db_path):
    con = init_if_needed(db_path)
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    con.close()


def test_regime_upsert_does_not_break_fk(db_path):
    """Regression: morning_scan re-runs in the same day must upsert the
    regime_snapshots row WITHOUT breaking FK constraints on signals that
    already reference it. INSERT OR REPLACE would DELETE+INSERT which
    violates FK; ON CONFLICT DO UPDATE preserves the row id."""
    con = init_if_needed(db_path)
    # First detect: insert today's regime
    con.execute("""INSERT INTO regime_snapshots
        (snapshot_date, state, min_score, nifty_close, payload_json)
        VALUES ('2026-05-14', 'Trending', 6, 22000.0, '{}')""")
    regime_id = con.execute(
        "SELECT id FROM regime_snapshots WHERE snapshot_date='2026-05-14'"
    ).fetchone()["id"]

    # Insert a child signal referencing this regime
    con.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json)
        VALUES ('2026-05-14', 'RELIANCE', 'SWING', ?, 12.0, 12.0, 'WATCH',
                1400.0, 1350.0, 50, '{}')""", (regime_id,))

    # Second detect (intraday re-run) — UPSERT, FK preserved
    con.execute(
        """INSERT INTO regime_snapshots
              (snapshot_date, state, min_score, nifty_close, payload_json)
           VALUES ('2026-05-14', 'Volatile', 8, 21900.0, '{}')
           ON CONFLICT(snapshot_date) DO UPDATE SET
              state = excluded.state,
              min_score = excluded.min_score,
              nifty_close = excluded.nifty_close"""
    )

    # Row id is unchanged, FK still valid, state updated
    new_regime_id = con.execute(
        "SELECT id FROM regime_snapshots WHERE snapshot_date='2026-05-14'"
    ).fetchone()["id"]
    assert new_regime_id == regime_id

    state = con.execute(
        "SELECT state FROM regime_snapshots WHERE id=?", (regime_id,)
    ).fetchone()["state"]
    assert state == "Volatile"

    # Child signal still reachable via its FK
    sig = con.execute(
        "SELECT verdict FROM signals WHERE regime_id=?", (regime_id,)
    ).fetchone()
    assert sig["verdict"] == "WATCH"
    con.close()
