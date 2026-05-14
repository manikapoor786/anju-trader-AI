"""Tests for anju_ai.loops.weekly_critic — stubbed LLM, no network."""

import json
import pytest

from anju_ai.loops.weekly_critic import (
    ExpectancyStats,
    LessonSummary,
    RevisionProposal,
    WeeklyCriticInput,
    WeeklyCriticOutput,
    _bucket,
    _group_stats,
    _load_prompt,
    _render,
    _stats_for_trades,
    collect_input,
    render_telegram,
    review_week,
    save_revisions,
)
from anju_ai.llm.base import LLMResponse, OK, PARSE_ERROR


class _Stub:
    name = "stub"
    def __init__(self, parsed=None, status=OK):
        self._parsed = parsed
        self._status = status
    def complete(self, prompt, schema, model, prompt_name, prompt_version,
                 max_tokens_in=8000, max_tokens_out=2000,
                 temperature=0.2, timeout_s=60.0):
        return LLMResponse(
            status=self._status, parsed=self._parsed, raw_text="canned",
            tokens_in=2500, tokens_out=600, latency_ms=4500,
            model=model, prompt_name=prompt_name, prompt_version=prompt_version,
            cost_inr=5.0,
        )


# ── Prompt ───────────────────────────────────────────────────────────────────

def test_load_prompt_v1_exists():
    text = _load_prompt("weekly_critic", 1)
    assert "WEEKLY CRITIC" in text
    assert "EVIDENCE-CITED" in text


# ── Bucket / stats helpers ───────────────────────────────────────────────────

def test_bucket_groups_by_5():
    assert _bucket(12) == "10-14"
    assert _bucket(15) == "15-19"
    assert _bucket(8.5) == "05-09"


def test_stats_for_trades_empty_returns_zeros():
    out = _stats_for_trades([])
    assert out.trades == 0
    assert out.win_rate == 0
    assert out.net_expectancy_pct == 0


def test_stats_for_trades_computes_correctly():
    trades = [
        {"outcome_kind": "WIN_T1", "net_pnl_pct": 5.0, "score": 15, "regime": "Trending"},
        {"outcome_kind": "WIN_T1", "net_pnl_pct": 8.0, "score": 16, "regime": "Trending"},
        {"outcome_kind": "LOSS_STOP", "net_pnl_pct": -4.0, "score": 12, "regime": "Sideways"},
    ]
    s = _stats_for_trades(trades)
    assert s.trades == 3
    assert s.win_rate == pytest.approx(66.7, abs=0.1)
    assert s.avg_winner_pct == pytest.approx(6.5, abs=0.01)
    assert s.avg_loser_pct == pytest.approx(-4.0, abs=0.01)


def test_group_stats_splits_by_key():
    trades = [
        {"outcome_kind": "WIN_T1", "net_pnl_pct": 5.0, "score": 12, "regime": "A"},
        {"outcome_kind": "WIN_T1", "net_pnl_pct": 8.0, "score": 18, "regime": "B"},
    ]
    g = _group_stats(trades, lambda t: _bucket(t["score"]))
    assert "10-14" in g
    assert "15-19" in g
    assert g["10-14"].trades == 1
    assert g["15-19"].trades == 1


# ── _render ──────────────────────────────────────────────────────────────────

def _sample_input(outcomes_count: int = 10) -> WeeklyCriticInput:
    return WeeklyCriticInput(
        week="2026-W19",
        signals_count=25, outcomes_count=outcomes_count,
        headline_stats=ExpectancyStats(
            trades=outcomes_count, win_rate=60.0,
            avg_winner_pct=5.0, avg_loser_pct=-3.0,
            net_expectancy_pct=1.2),
        expectancy_by_score_bucket={
            "10-14": ExpectancyStats(trades=3, win_rate=33.3,
                                      avg_winner_pct=4, avg_loser_pct=-3,
                                      net_expectancy_pct=-0.5),
            "15-19": ExpectancyStats(trades=7, win_rate=71.4,
                                      avg_winner_pct=6, avg_loser_pct=-2.5,
                                      net_expectancy_pct=1.8),
        },
        expectancy_by_regime={},
        expectancy_by_entry_model={},
        recent_lessons=[
            LessonSummary(lesson_id=1, classification="EDGE_BROKEN",
                           primary_factor="weekly downtrend missed",
                           lesson="MTFA filter too lenient"),
        ],
        flagged_lessons=[
            LessonSummary(lesson_id=1, classification="EDGE_BROKEN",
                           primary_factor="weekly downtrend missed",
                           lesson="MTFA filter too lenient"),
        ],
        recent_approved_revisions=[],
    )


def test_render_includes_headline_and_buckets():
    inp = _sample_input()
    out = _render(inp)
    assert "2026-W19" in out
    assert "HEADLINE" in out
    assert "BY SCORE BUCKET" in out
    assert "10-14" in out


def test_render_includes_lessons_when_present():
    inp = _sample_input()
    out = _render(inp)
    assert "LESSONS THIS WEEK" in out
    assert "weekly downtrend missed" in out


# ── review_week ──────────────────────────────────────────────────────────────

def test_review_week_happy_path():
    canned = WeeklyCriticOutput(
        summary="Good week. 60% win rate, +1.2% expectancy.",
        flags=["RECURRING_LESSON_PATTERN"],
        proposals=[RevisionProposal(
            kind="FILTER", target="tools.scoring.mtfa_threshold",
            current_value="45", proposed_value="50",
            rationale="3 of 7 losses had w_rsi between 45-50.",
            expected_impact="+0.3%/trade",
            confidence=0.65, backtest_required=True,
        )],
    )
    r = review_week(_sample_input(), client=_Stub(parsed=canned))
    assert r.status == OK
    assert len(r.parsed.proposals) == 1
    assert r.parsed.proposals[0].kind == "FILTER"


def test_review_week_returns_empty_proposals_ok():
    canned = WeeklyCriticOutput(summary="Quiet week — no revisions.",
                                 proposals=[])
    r = review_week(_sample_input(), client=_Stub(parsed=canned))
    assert r.status == OK
    assert r.parsed.proposals == []


# ── save_revisions (DB integration) ──────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    # Seed a reasoning trace (FK target)
    con.execute("""INSERT INTO reasoning_traces
        (loop, prompt_name, prompt_version, model,
         input_tokens, output_tokens, latency_ms,
         input_payload_json, output_payload_json, raw_llm_output, status)
        VALUES ('weekly_critic','weekly_critic',1,'claude-sonnet-4-6',
                2500,500,4000,'{}','{}','','OK')""")
    yield con
    con.close()


def test_save_revisions_persists_each_proposal(db):
    out = WeeklyCriticOutput(
        summary="x", flags=[],
        proposals=[
            RevisionProposal(
                kind="PARAMETER", target="cfg.max_open",
                current_value="15", proposed_value="12",
                rationale="reduce concentration risk",
                expected_impact="-2% max DD",
                confidence=0.7, backtest_required=False,
            ),
            RevisionProposal(
                kind="WEIGHT", target="scoring.dryup",
                current_value="3", proposed_value="2",
                rationale="dryup-only signals had -0.5% expectancy",
                expected_impact="cuts bottom 8% of score volume",
                confidence=0.55, backtest_required=True,
            ),
        ]
    )
    ids = save_revisions(db, out, "2026-W19", reasoning_trace_id=1)
    assert len(ids) == 2

    rows = db.execute("SELECT kind, status FROM revisions ORDER BY id").fetchall()
    assert rows[0]["kind"] == "PARAMETER"
    assert rows[0]["status"] == "AWAITING_APPROVAL"   # no backtest required
    assert rows[1]["kind"] == "WEIGHT"
    assert rows[1]["status"] == "BACKTESTING"          # backtest required


# ── render_telegram ──────────────────────────────────────────────────────────

def test_render_telegram_empty_proposals():
    out = WeeklyCriticOutput(summary="quiet week", proposals=[])
    msg = render_telegram(out, [])
    assert "Weekly Critic" in msg
    assert "No revisions proposed" in msg


def test_render_telegram_includes_approve_reject_commands():
    out = WeeklyCriticOutput(
        summary="x", proposals=[
            RevisionProposal(
                kind="FILTER", target="t",
                current_value="a", proposed_value="b",
                rationale="r", expected_impact="i",
                confidence=0.6, backtest_required=False),
        ]
    )
    msg = render_telegram(out, [42])
    assert "/approve_42" in msg
    assert "/reject_42" in msg
    assert "Proposal #42" in msg


# ── collect_input (DB integration smoke) ─────────────────────────────────────

def test_collect_input_empty_db_returns_zero_outcomes(db):
    inp = collect_input(db)
    assert inp.outcomes_count == 0
    assert inp.signals_count == 0
    assert inp.headline_stats.trades == 0
