"""Tests for anju_ai.loops.anomaly_qa — stubbed LLM, no network."""

import pytest

from anju_ai.loops.anomaly_qa import (
    Anomaly,
    AnomalyQAInput,
    AnomalyQAOutput,
    _load_prompt,
    _render,
    build_snapshot,
    collect_open_position_count,
    collect_recent_errors,
    collect_signal_history,
    review_anomalies,
)
from anju_ai.llm.base import LLMResponse, OK, PARSE_ERROR


class _Stub:
    name = "stub"
    def __init__(self, parsed=None, status=OK):
        self._parsed = parsed
        self._status = status
    def complete(self, prompt, schema, model, prompt_name, prompt_version,
                 max_tokens_in=3000, max_tokens_out=800,
                 temperature=0.2, timeout_s=30.0):
        return LLMResponse(
            status=self._status, parsed=self._parsed, raw_text="canned",
            tokens_in=500, tokens_out=200, latency_ms=1500,
            model=model, prompt_name=prompt_name, prompt_version=prompt_version,
        )


# ── Prompt + render ──────────────────────────────────────────────────────────

def test_load_prompt_v1_exists():
    text = _load_prompt("anomaly_qa", 1)
    assert "ANOMALY QA" in text
    assert "CRITICAL" in text
    assert "DATA_STALE" in text


def test_render_includes_all_sections():
    inp = AnomalyQAInput(
        snapshot_time="2026-05-14T10:00:00",
        workflow_health={"morning_scan": {"ok": 5, "bad": 0}},
        data_freshness={"bhavcopy": "2026-05-14"},
        regime_history=[{"date": "2026-05-14", "state": "Trending", "min_score": 6}],
        signal_count_history=[{"date": "2026-05-14", "n": 12}],
        llm_trace_health={"total_24h": 50, "ok_rate": 96.0},
        open_position_count=8,
        recent_errors=[],
    )
    out = _render(inp)
    assert "SYSTEM SNAPSHOT" in out
    assert "Workflow health" in out
    assert "Data freshness" in out
    assert "Regime history" in out
    assert "morning_scan" in out
    assert "2026-05-14" in out


# ── review_anomalies ─────────────────────────────────────────────────────────

def test_review_happy_path_returns_anomalies():
    canned = AnomalyQAOutput(anomalies=[
        Anomaly(severity="WARN", category="LLM_PARSE_ERRORS",
                description="20% parse error rate over 24h",
                suggested_fix="check Gemini API key + prompt"),
    ])
    inp = AnomalyQAInput(
        snapshot_time="now", workflow_health={}, data_freshness={},
        regime_history=[], signal_count_history=[],
        llm_trace_health={}, open_position_count=0, recent_errors=[],
    )
    r = review_anomalies(inp, client=_Stub(parsed=canned))
    assert r.status == OK
    assert len(r.parsed.anomalies) == 1
    assert r.parsed.anomalies[0].severity == "WARN"


def test_review_handles_empty_anomalies():
    canned = AnomalyQAOutput(anomalies=[])
    inp = AnomalyQAInput(
        snapshot_time="now", workflow_health={}, data_freshness={},
        regime_history=[], signal_count_history=[],
        llm_trace_health={}, open_position_count=0, recent_errors=[],
    )
    r = review_anomalies(inp, client=_Stub(parsed=canned))
    assert r.status == OK
    assert r.parsed.anomalies == []


def test_review_propagates_llm_error():
    inp = AnomalyQAInput(
        snapshot_time="now", workflow_health={}, data_freshness={},
        regime_history=[], signal_count_history=[],
        llm_trace_health={}, open_position_count=0, recent_errors=[],
    )
    r = review_anomalies(inp, client=_Stub(parsed=None, status=PARSE_ERROR))
    assert r.status == PARSE_ERROR


# ── Snapshot collectors (DB integration) ─────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    yield con
    con.close()


def test_build_snapshot_on_empty_db(db):
    snap = build_snapshot(db)
    assert isinstance(snap, AnomalyQAInput)
    assert snap.open_position_count == 0
    assert snap.regime_history == []
    assert snap.signal_count_history == []
    assert snap.recent_errors == []


def test_collect_open_position_count_with_outcome(db):
    db.execute("""INSERT INTO regime_snapshots
        (snapshot_date, state, min_score, nifty_close, payload_json)
        VALUES ('2026-05-10', 'Trending', 6, 22000, '{}')""")
    rid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json)
        VALUES ('2026-05-10', 'A', 'SWING', ?, 15, 15, 'BUY',
                100, 95, 10, '{}')""", (rid,))
    sid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO fills
        (signal_id, fill_date, fill_price, fill_qty, gross_value, is_paper)
        VALUES (?, '2026-05-10', 100, 10, 1000, 1)""", (sid,))
    assert collect_open_position_count(db) == 1   # not yet closed

    # Close it — count should drop to 0
    fid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO outcomes
        (fill_id, outcome_date, outcome_kind, exit_price, days_held,
         gross_pnl_paise, costs_total_paise, net_pnl_paise, net_pnl_pct)
        VALUES (?, '2026-05-13', 'LOSS_STOP', 95, 3, -5000, 100, -5100, -5.1)""",
                (fid,))
    assert collect_open_position_count(db) == 0


def test_collect_signal_history_excludes_backtest_signals(db):
    db.execute("""INSERT INTO regime_snapshots
        (snapshot_date, state, min_score, nifty_close, payload_json)
        VALUES ('2026-05-10', 'Trending', 6, 22000, '{}')""")
    rid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO backtest_runs
        (name, start_date, end_date, universe, mode, capital_inr,
         config_json, status) VALUES
        ('bt1','2024-01-01','2024-12-31','x','strict',1e7,'{}','COMPLETED')""")
    btid = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 1 live signal, 1 backtest signal — should only count live
    today = "2026-05-14"
    db.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json,
         backtest_run_id)
        VALUES (?, 'A', 'SWING', ?, 15, 15, 'BUY', 100, 95, 10, '{}', NULL)""",
        (today, rid))
    db.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json,
         backtest_run_id)
        VALUES (?, 'B', 'SWING', ?, 15, 15, 'BUY', 100, 95, 10, '{}', ?)""",
        (today, rid, btid))

    hist = collect_signal_history(db, days=30)
    today_row = next(h for h in hist if h["date"] == today)
    assert today_row["n"] == 1   # only live counted


def test_collect_recent_errors_filters_severity(db):
    from anju_ai.memory.db import audit_log
    audit_log(db, "X", "info msg", severity="INFO")
    audit_log(db, "Y", "warn msg", severity="WARN")
    audit_log(db, "Z", "crit msg", severity="CRITICAL")
    errors = collect_recent_errors(db, limit=10)
    severities = {e["severity"] for e in errors}
    assert severities == {"WARN", "CRITICAL"}
