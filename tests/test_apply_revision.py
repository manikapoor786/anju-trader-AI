"""Tests for anju_ai.loops.apply_revision — DB integration, no network."""

import json
import pytest

from anju_ai.loops.apply_revision import (
    apply_parameter_change,
    approve,
    load_revision,
    reject,
)


@pytest.fixture
def db_with_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    # Seed reasoning trace + revision
    con.execute("""INSERT INTO reasoning_traces
        (loop, prompt_name, prompt_version, model,
         input_tokens, output_tokens, latency_ms,
         input_payload_json, output_payload_json, raw_llm_output, status)
        VALUES ('weekly_critic','weekly_critic',1,'c',100,50,1000,'{}','{}','','OK')""")
    tid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("""INSERT INTO revisions
        (proposed_at, week, kind, target, current_value, proposed_value,
         rationale, expected_impact, confidence, backtest_required,
         status, reasoning_trace_id)
        VALUES (datetime('now'), '2026-W19', 'PARAMETER',
                'tools.scoring.MIN_BASE_SCORE', '3', '5',
                'reduce false signals', '+0.2%/trade', 0.65, 0,
                'AWAITING_APPROVAL', ?)""", (tid,))
    rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    yield con, rid, tmp_path
    con.close()


# ── load_revision ────────────────────────────────────────────────────────────

def test_load_revision_existing(db_with_revision):
    con, rid, _ = db_with_revision
    rev = load_revision(con, rid)
    assert rev is not None
    assert rev["status"] == "AWAITING_APPROVAL"
    assert rev["kind"] == "PARAMETER"


def test_load_revision_missing(db_with_revision):
    con, _, _ = db_with_revision
    assert load_revision(con, 999) is None


# ── approve ──────────────────────────────────────────────────────────────────

def test_approve_parameter_writes_params_file(db_with_revision, monkeypatch):
    con, rid, tmp_path = db_with_revision
    # Redirect ROOT for apply_parameter_change
    from anju_ai.loops import apply_revision as ar
    monkeypatch.setattr(ar, "ROOT", tmp_path)

    ok, msg = approve(con, rid)
    assert ok
    assert "APPROVED" in msg.upper()

    # Status updated
    rev = load_revision(con, rid)
    assert rev["status"] == "APPLIED"   # PARAMETER auto-applies

    # File written
    params_file = tmp_path / "config" / "model_params.json"
    assert params_file.exists()
    params = json.loads(params_file.read_text())
    assert params["tools.scoring.MIN_BASE_SCORE"] == "5"


def test_approve_idempotent_already_approved(db_with_revision):
    con, rid, _ = db_with_revision
    con.execute("UPDATE revisions SET status='APPROVED' WHERE id=?", (rid,))
    ok, msg = approve(con, rid)
    assert not ok
    assert "already approved" in msg


def test_approve_rejected_revision_fails(db_with_revision):
    con, rid, _ = db_with_revision
    con.execute("UPDATE revisions SET status='REJECTED' WHERE id=?", (rid,))
    ok, msg = approve(con, rid)
    assert not ok
    assert "rejected" in msg.lower()


def test_approve_backtest_required_pending_fails(db_with_revision):
    con, rid, _ = db_with_revision
    con.execute(
        "UPDATE revisions SET status='BACKTESTING', backtest_required=1 WHERE id=?",
        (rid,))
    ok, msg = approve(con, rid)
    assert not ok
    assert "backtest" in msg.lower()


def test_approve_missing_revision_fails(db_with_revision):
    con, _, _ = db_with_revision
    ok, msg = approve(con, 999)
    assert not ok
    assert "not found" in msg


def test_approve_weight_kind_does_not_auto_apply(db_with_revision, monkeypatch):
    con, rid, tmp_path = db_with_revision
    from anju_ai.loops import apply_revision as ar
    monkeypatch.setattr(ar, "ROOT", tmp_path)
    con.execute("UPDATE revisions SET kind='WEIGHT' WHERE id=?", (rid,))

    ok, msg = approve(con, rid)
    assert ok
    assert "manual code change" in msg
    rev = load_revision(con, rid)
    assert rev["status"] == "APPROVED"   # not APPLIED


# ── reject ──────────────────────────────────────────────────────────────────

def test_reject_with_reason_updates_status(db_with_revision):
    con, rid, _ = db_with_revision
    ok, msg = reject(con, rid, reason="data too noisy")
    assert ok
    rev = load_revision(con, rid)
    assert rev["status"] == "REJECTED"
    assert rev["decision_reason"] == "data too noisy"


def test_reject_without_reason_works(db_with_revision):
    con, rid, _ = db_with_revision
    ok, msg = reject(con, rid)
    assert ok
    rev = load_revision(con, rid)
    assert rev["status"] == "REJECTED"
    assert rev["decision_reason"] is None


def test_reject_idempotent(db_with_revision):
    con, rid, _ = db_with_revision
    con.execute("UPDATE revisions SET status='REJECTED' WHERE id=?", (rid,))
    ok, msg = reject(con, rid)
    assert not ok
    assert "already rejected" in msg


def test_reject_approved_fails(db_with_revision):
    con, rid, _ = db_with_revision
    con.execute("UPDATE revisions SET status='APPROVED' WHERE id=?", (rid,))
    ok, msg = reject(con, rid)
    assert not ok
    assert "already approved" in msg


# ── apply_parameter_change ───────────────────────────────────────────────────

def test_apply_parameter_writes_new_file(tmp_path, monkeypatch):
    from anju_ai.loops import apply_revision as ar
    monkeypatch.setattr(ar, "ROOT", tmp_path)
    ok = apply_parameter_change("a.b.c", "42")
    assert ok
    params = json.loads((tmp_path / "config" / "model_params.json").read_text())
    assert params["a.b.c"] == "42"


def test_apply_parameter_merges_with_existing(tmp_path, monkeypatch):
    from anju_ai.loops import apply_revision as ar
    monkeypatch.setattr(ar, "ROOT", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "model_params.json").write_text(
        json.dumps({"existing.param": "1"}))
    apply_parameter_change("new.param", "2")
    params = json.loads((tmp_path / "config" / "model_params.json").read_text())
    assert params == {"existing.param": "1", "new.param": "2"}
