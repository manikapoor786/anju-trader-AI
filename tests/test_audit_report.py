"""Tests for anju_ai.loops.audit_report — DB integration, no network."""

import pytest

from anju_ai.loops.audit_report import (
    anomaly_summary,
    build_report,
    lessons_summary,
    llm_summary,
    outcomes_summary,
    revisions_summary,
    signals_summary,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    # Seed minimal data
    con.execute("""INSERT INTO regime_snapshots
        (snapshot_date, state, min_score, nifty_close, payload_json)
        VALUES ('2026-05-10', 'Trending', 6, 22000, '{}')""")
    yield con
    con.close()


def test_signals_summary_empty_returns_zero(db):
    out = signals_summary(db, "2026-01-01")
    assert out["total"] == 0


def test_signals_summary_excludes_backtest(db):
    db.execute("""INSERT INTO backtest_runs
        (name, start_date, end_date, universe, mode, capital_inr,
         config_json, status) VALUES
        ('bt1','2024-01-01','2024-12-31','x','strict',1e7,'{}','COMPLETED')""")
    btid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Live signal
    db.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json)
        VALUES ('2026-05-14', 'A', 'SWING', 1, 15, 15, 'BUY',
                100, 95, 10, '{}')""")
    # Backtest signal
    db.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json,
         backtest_run_id)
        VALUES ('2026-05-14', 'B', 'SWING', 1, 15, 15, 'BUY',
                100, 95, 10, '{}', ?)""", (btid,))
    out = signals_summary(db, "2026-01-01")
    assert out["total"] == 1
    assert out["by_verdict"]["BUY"] == 1


def test_outcomes_summary_computes_win_rate(db):
    db.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json)
        VALUES ('2026-05-10', 'A', 'SWING', 1, 15, 15, 'BUY',
                100, 95, 10, '{}')""")
    sid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO fills
        (signal_id, fill_date, fill_price, fill_qty, gross_value, is_paper)
        VALUES (?, '2026-05-10', 100, 10, 1000, 1)""", (sid,))
    fid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO outcomes
        (fill_id, outcome_date, outcome_kind, exit_price, days_held,
         gross_pnl_paise, costs_total_paise, net_pnl_paise, net_pnl_pct)
        VALUES (?, '2026-05-13', 'WIN_T1', 110, 3, 10000, 100, 9900, 9.9)""", (fid,))
    out = outcomes_summary(db, "2026-01-01")
    assert out["total"] == 1
    assert out["wins"] == 1
    assert out["win_rate_pct"] == 100.0


def test_llm_summary_aggregates_by_loop(db):
    for kind in ["catalyst_review", "catalyst_review", "post_mortem"]:
        db.execute("""INSERT INTO reasoning_traces
            (loop, prompt_name, prompt_version, model,
             input_tokens, output_tokens, latency_ms,
             input_payload_json, output_payload_json, raw_llm_output,
             status, cost_inr)
            VALUES (?, ?, 1, 'm', 100, 50, 1000, '{}', '{}', '', 'OK', 0.5)""",
            (kind, kind))
    out = llm_summary(db, "2020-01-01T00:00:00")
    assert "catalyst_review" in out["by_loop"]
    assert "post_mortem" in out["by_loop"]
    assert out["by_loop"]["catalyst_review"]["ok"] == 2
    assert out["by_loop"]["post_mortem"]["ok"] == 1
    assert out["total_cost_inr"] == 1.5


def test_lessons_summary_counts_flagged(db):
    db.execute("""INSERT INTO reasoning_traces
        (loop, prompt_name, prompt_version, model,
         input_tokens, output_tokens, latency_ms,
         input_payload_json, output_payload_json, raw_llm_output, status)
        VALUES ('post_mortem','post_mortem',1,'m',100,50,1000,'{}','{}','','OK')""")
    tid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json)
        VALUES ('2026-05-10', 'A', 'SWING', 1, 15, 15, 'BUY',
                100, 95, 10, '{}')""")
    sid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO fills
        (signal_id, fill_date, fill_price, fill_qty, gross_value, is_paper)
        VALUES (?, '2026-05-10', 100, 10, 1000, 1)""", (sid,))
    fid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO outcomes
        (fill_id, outcome_date, outcome_kind, exit_price, days_held,
         gross_pnl_paise, costs_total_paise, net_pnl_paise, net_pnl_pct)
        VALUES (?, '2026-05-13', 'LOSS_STOP', 95, 3, -5000, 100, -5100, -5.1)""",
        (fid,))
    oid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Two lessons, one flagged
    db.execute("""INSERT INTO lessons
        (outcome_id, classification, primary_factor, lesson,
         suggests_revision, reasoning_trace_id)
        VALUES (?, 'EDGE_BROKEN', 'x', 'y', 1, ?)""", (oid, tid))
    db.execute("""INSERT INTO lessons
        (outcome_id, classification, primary_factor, lesson,
         suggests_revision, reasoning_trace_id)
        VALUES (?, 'BAD_LUCK', 'x', 'y', 0, ?)""", (oid, tid))

    out = lessons_summary(db, "2020-01-01T00:00:00")
    assert out["total"] == 2
    assert out["flagged_for_revision"] == 1
    assert out["by_classification"]["EDGE_BROKEN"] == 1
    assert out["by_classification"]["BAD_LUCK"] == 1


def test_revisions_summary_groups_by_status(db):
    db.execute("""INSERT INTO reasoning_traces
        (loop, prompt_name, prompt_version, model,
         input_tokens, output_tokens, latency_ms,
         input_payload_json, output_payload_json, raw_llm_output, status)
        VALUES ('weekly_critic','weekly_critic',1,'c',100,50,1000,'{}','{}','','OK')""")
    tid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO revisions
        (proposed_at, week, kind, target, current_value, proposed_value,
         rationale, expected_impact, confidence, backtest_required,
         status, reasoning_trace_id)
        VALUES (datetime('now'), '2026-W19', 'PARAMETER', 't', 'a', 'b',
                'r', 'i', 0.7, 0, 'AWAITING_APPROVAL', ?)""", (tid,))
    db.execute("""INSERT INTO revisions
        (proposed_at, week, kind, target, current_value, proposed_value,
         rationale, expected_impact, confidence, backtest_required,
         status, reasoning_trace_id)
        VALUES (datetime('now'), '2026-W19', 'WEIGHT', 't', 'a', 'b',
                'r', 'i', 0.7, 1, 'APPROVED', ?)""", (tid,))
    out = revisions_summary(db, "2020-01-01T00:00:00")
    assert out["AWAITING_APPROVAL"] == 1
    assert out["APPROVED"] == 1


def test_anomaly_summary_filters_by_event_type(db):
    from anju_ai.memory.db import audit_log
    audit_log(db, "ANOMALY_QA_RUN", "warn detected", severity="WARN")
    audit_log(db, "ANOMALY_QA_RUN", "critical found", severity="CRITICAL")
    audit_log(db, "REGIME_DETECTED", "info only", severity="INFO")
    out = anomaly_summary(db, "2020-01-01T00:00:00")
    assert out["WARN"] == 1
    assert out["CRITICAL"] == 1
    assert "INFO" not in out   # not an ANOMALY event


def test_build_report_renders_all_sections(db):
    text = build_report(db, days=7)
    assert "Audit Report" in text
    assert "Signals" in text
    assert "Outcomes" in text
    assert "LLM" in text or "Lessons" in text
    assert "Anomalies" in text
