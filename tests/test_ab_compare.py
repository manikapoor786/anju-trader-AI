"""Tests for anju_ai.loops.ab_compare — pure functions only."""

import pytest

from anju_ai.loops.ab_compare import count_signals_last_n_days, render_report


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))


def test_count_signals_empty_db_returns_zeros():
    stats = count_signals_last_n_days(30)
    assert stats["total_signals"] == 0
    assert stats["outcomes"] == 0
    assert stats["wins"] == 0
    assert stats["win_rate"] is None


def test_count_signals_includes_recent_only():
    from anju_ai.memory.db import init_if_needed
    from datetime import datetime, timedelta

    con = init_if_needed()
    # Prereq regime
    con.execute("""INSERT INTO regime_snapshots
        (snapshot_date, state, min_score, nifty_close, payload_json)
        VALUES ('2026-05-14', 'Trending', 6, 22000.0, '{}')""")
    rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    today  = datetime.now().strftime("%Y-%m-%d")
    old    = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    # 2 recent signals
    for _ in range(2):
        con.execute("""INSERT INTO signals
            (signal_date, symbol, horizon, regime_id, rule_score, final_score,
             verdict, entry_price, suggested_stop, suggested_qty, breakdown_json)
            VALUES (?, 'A', 'SWING', ?, 12.0, 12.0, 'BUY', 100.0, 95.0, 10, '{}')""",
            (today, rid))
    # 1 old signal (outside 30d window)
    con.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json)
        VALUES (?, 'B', 'SWING', ?, 12.0, 12.0, 'BUY', 100.0, 95.0, 10, '{}')""",
        (old, rid))
    con.close()

    stats = count_signals_last_n_days(30)
    assert stats["total_signals"] == 2
    assert stats["by_verdict"] == {"BUY": 2}


def test_render_report_handles_empty():
    stats = {"days": 30, "since": "2026-04-14", "total_signals": 0,
             "by_verdict": {}, "outcomes": 0, "wins": 0, "win_rate": None}
    out = render_report(stats)
    assert "anju-AI" in out
    assert "anju-trader" in out
    assert "30" in out
    assert "Phase 0 v0" in out
    assert "no closed outcomes yet" in out


def test_render_report_shows_win_rate():
    stats = {"days": 30, "since": "2026-04-14", "total_signals": 15,
             "by_verdict": {"BUY": 10, "WATCH": 5},
             "outcomes": 8, "wins": 5, "win_rate": 62.5}
    out = render_report(stats)
    assert "62.5%" in out
    assert "<b>15</b>" in out
