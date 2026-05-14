"""Tests for anju_ai.loops.eod_postmortem — stubbed LLM, no network."""

from dataclasses import dataclass
import json
import pytest

from anju_ai.loops.eod_postmortem import (
    PostMortemFillContext,
    PostMortemInput,
    PostMortemOutcomeContext,
    PostMortemOutput,
    PostMortemSignalContext,
    SimilarTrade,
    _load_prompt,
    _render,
    find_recent_closed_outcomes,
    find_similar_past_lessons,
    review_outcome,
    save_lesson,
)
from anju_ai.llm.base import LLMResponse, OK, PARSE_ERROR


class _Stub:
    name = "stub"
    def __init__(self, parsed=None, status=OK):
        self._parsed = parsed
        self._status = status
    def complete(self, prompt, schema, model, prompt_name, prompt_version,
                 max_tokens_in=2500, max_tokens_out=500,
                 temperature=0.3, timeout_s=30.0):
        return LLMResponse(
            status=self._status, parsed=self._parsed, raw_text="canned",
            tokens_in=200, tokens_out=80, latency_ms=900,
            model=model, prompt_name=prompt_name, prompt_version=prompt_version,
        )


def _input() -> PostMortemInput:
    return PostMortemInput(
        signal=PostMortemSignalContext(
            symbol="X", score=18.0, verdict="BUY",
            entry_model="🚀 Breakout Entry", regime="Trending",
            tags=["💧 Dry-Up", "🏗️ Base"],
        ),
        fill=PostMortemFillContext(
            fill_date="2026-05-10", fill_price=100.0, qty=50),
        outcome=PostMortemOutcomeContext(
            outcome_kind="LOSS_STOP", exit_date="2026-05-13",
            exit_price=95.0, days_held=3, net_pnl_pct=-5.5,
            mfe_pct=2.0, mae_pct=-5.5),
    )


# ── Prompt loader ────────────────────────────────────────────────────────────

def test_load_prompt_v1_exists():
    text = _load_prompt("post_mortem", 1)
    assert "POST-MORTEM" in text
    assert "EDGE_WORKING" in text


def test_load_prompt_strips_frontmatter():
    text = _load_prompt("post_mortem", 1)
    assert not text.startswith("---")


# ── _render ──────────────────────────────────────────────────────────────────

def test_render_includes_all_three_sections():
    out = _render(_input())
    assert "SIGNAL CONTEXT" in out
    assert "FILL" in out
    assert "OUTCOME" in out
    assert "LOSS_STOP" in out
    assert "X" in out


def test_render_handles_no_similar_trades():
    out = _render(_input())
    assert "SIMILAR PAST TRADES" not in out


def test_render_includes_similar_trades_when_present():
    inp = _input()
    inp.similar_past_trades = [
        SimilarTrade(lesson_id=42, classification="EDGE_WORKING",
                     primary_factor="strong base", lesson="continue"),
    ]
    out = _render(inp)
    assert "SIMILAR PAST TRADES" in out
    assert "[42]" in out


# ── review_outcome ───────────────────────────────────────────────────────────

def test_review_outcome_happy_path():
    canned = PostMortemOutput(
        classification="EDGE_BROKEN", primary_factor="weekly downtrend missed",
        lesson="MTFA filter should reject when w_rsi < 45.",
        suggests_revision=True, revision_hint="tighten MTFA threshold to 45",
    )
    resp = review_outcome(_input(), client=_Stub(parsed=canned))
    assert resp.status == OK
    assert resp.parsed.classification == "EDGE_BROKEN"


def test_review_outcome_returns_parse_error_when_client_fails():
    resp = review_outcome(_input(), client=_Stub(parsed=None, status=PARSE_ERROR))
    assert resp.status == PARSE_ERROR


# ── DB helpers (integration) ─────────────────────────────────────────────────

@pytest.fixture
def db_with_closed_trade(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    # Seed prereqs
    con.execute("""INSERT INTO regime_snapshots
        (snapshot_date, state, min_score, nifty_close, payload_json)
        VALUES ('2026-05-10', 'Trending', 6, 22000, '{}')""")
    rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_qty, breakdown_json)
        VALUES ('2026-05-10', 'X', 'SWING', ?, 18.0, 18.0, 'BUY',
                100, 95, 50, '{}')""", (rid,))
    sid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("""INSERT INTO fills
        (signal_id, fill_date, fill_price, fill_qty, gross_value, is_paper)
        VALUES (?, '2026-05-10', 100, 50, 5000, 1)""", (sid,))
    fid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("""INSERT INTO outcomes
        (fill_id, outcome_date, outcome_kind, exit_price, days_held,
         gross_pnl_paise, costs_total_paise, net_pnl_paise, net_pnl_pct,
         max_favourable_excursion, max_adverse_excursion)
        VALUES (?, '2026-05-13', 'LOSS_STOP', 95, 3, -25000, 100, -25100, -5.0,
                2.0, -5.5)""", (fid,))
    oid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    yield con, oid, sid, fid
    con.close()


def test_find_recent_closed_outcomes_picks_unprocessed(db_with_closed_trade):
    con, oid, _, _ = db_with_closed_trade
    rows = find_recent_closed_outcomes(con, limit=10, since="2026-01-01")
    assert len(rows) == 1
    assert rows[0]["outcome_id"] == oid
    assert rows[0]["outcome_kind"] == "LOSS_STOP"


def test_find_recent_skips_already_post_mortemed(db_with_closed_trade):
    con, oid, _, _ = db_with_closed_trade
    # Pretend a lesson already exists
    con.execute("""INSERT INTO reasoning_traces
        (loop, prompt_name, prompt_version, model,
         input_tokens, output_tokens, latency_ms,
         input_payload_json, output_payload_json, raw_llm_output, status)
        VALUES ('post_mortem','post_mortem',1,'m',100,50,1000,'{}','{}','','OK')""")
    tid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("""INSERT INTO lessons
        (outcome_id, classification, primary_factor, lesson,
         suggests_revision, reasoning_trace_id)
        VALUES (?, 'EDGE_WORKING', 'x', 'y', 0, ?)""", (oid, tid))
    rows = find_recent_closed_outcomes(con, limit=10, since="2026-01-01")
    assert rows == []   # already processed → skipped


def test_save_lesson_persists_correctly(db_with_closed_trade):
    con, oid, _, _ = db_with_closed_trade
    # Need a reasoning_trace first (FK)
    con.execute("""INSERT INTO reasoning_traces
        (loop, prompt_name, prompt_version, model,
         input_tokens, output_tokens, latency_ms,
         input_payload_json, output_payload_json, raw_llm_output, status)
        VALUES ('post_mortem','post_mortem',1,'m',100,50,1000,'{}','{}','','OK')""")
    tid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    out = PostMortemOutput(
        classification="EDGE_BROKEN", primary_factor="weekly downtrend",
        lesson="add MTFA filter", suggests_revision=True,
        revision_hint="tighten MTFA",
    )
    lid = save_lesson(con, oid, out, tid)
    assert lid > 0
    row = con.execute("SELECT classification, suggests_revision, revision_hint "
                      "FROM lessons WHERE id=?", (lid,)).fetchone()
    assert row["classification"] == "EDGE_BROKEN"
    assert row["suggests_revision"] == 1
    assert row["revision_hint"] == "tighten MTFA"
