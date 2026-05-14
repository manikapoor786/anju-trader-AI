"""Tests for anju_ai.tools.catalyst — mock LLM, no network."""

from dataclasses import dataclass
import pytest

from anju_ai.tools.catalyst import (
    CatalystReviewInput,
    CatalystReviewOutput,
    EarningsItem,
    FilingItem,
    NewsItem,
    PositionContext,
    _load_prompt,
    _render_input,
    apply_catalyst_to_score,
    review_catalyst,
)
from anju_ai.llm.base import LLMResponse, OK, PARSE_ERROR


@dataclass
class FakeResp:
    status_code: int
    _json: object

    def json(self): return self._json


class _StubClient:
    """Stand-in for GeminiClient that returns canned responses."""
    name = "stub"
    def __init__(self, parsed=None, status=OK, raw="canned"):
        self._parsed = parsed
        self._status = status
        self._raw = raw

    def complete(self, prompt, schema, model, prompt_name, prompt_version,
                 max_tokens_in=1500, max_tokens_out=400,
                 temperature=0.2, timeout_s=30.0):
        return LLMResponse(
            status=self._status, parsed=self._parsed, raw_text=self._raw,
            tokens_in=120, tokens_out=80, latency_ms=1200,
            model=model, prompt_name=prompt_name, prompt_version=prompt_version,
        )


# ── Prompt loader ────────────────────────────────────────────────────────────

def test_load_prompt_v1_exists():
    text = _load_prompt("catalyst_review", 1)
    assert "CATALYST REVIEW" in text
    assert "EARNINGS_THIS_WEEK" in text


def test_load_prompt_strips_frontmatter():
    text = _load_prompt("catalyst_review", 1)
    assert not text.startswith("---")
    assert "name: catalyst_review" not in text  # frontmatter excluded


# ── _render_input ────────────────────────────────────────────────────────────

def test_render_input_minimal():
    inp = CatalystReviewInput(symbol="X", rule_based_score=12.0)
    out = _render_input(inp)
    assert "X" in out
    assert "12.0/100" in out
    assert "none in last 24h" in out
    assert "none in last 7 days" in out


def test_render_input_with_news_and_filings():
    inp = CatalystReviewInput(
        symbol="RELIANCE", company_name="Reliance Industries",
        sector="Energy", rule_based_score=22.5,
        news_24h=[NewsItem(title="Q4 results beat estimates",
                           source="MoneyControl", snippet="Revenue +18% YoY")],
        filings_7d=[FilingItem(kind="RESULTS",
                               headline="Quarterly results filed",
                               filed_at="2026-05-13")],
    )
    out = _render_input(inp)
    assert "Q4 results beat" in out
    assert "MoneyControl" in out
    assert "RESULTS" in out


def test_render_input_includes_earnings_and_position():
    inp = CatalystReviewInput(
        symbol="A", rule_based_score=15.0,
        earnings_calendar=EarningsItem(next_date="2026-05-20",
                                        consensus_eps=12.5,
                                        last_q_beat_pct=4.2),
        open_position=PositionContext(qty=100, entry_price=120,
                                       days_held=5, pnl_pct=3.2),
    )
    out = _render_input(inp)
    assert "2026-05-20" in out
    assert "12.5" in out
    assert "Held 5d" in out
    assert "+3.2%" in out


def test_render_input_truncates_long_lists():
    inp = CatalystReviewInput(
        symbol="A", rule_based_score=15.0,
        news_24h=[NewsItem(title=f"News {i}") for i in range(20)],
        filings_7d=[FilingItem(kind="X", headline=f"F{i}") for i in range(20)],
    )
    out = _render_input(inp)
    # First 10 news, first 8 filings (per render code)
    assert out.count("News ") == 10
    assert out.count("[X]") == 8


# ── review_catalyst ──────────────────────────────────────────────────────────

def test_review_catalyst_happy_path():
    canned = CatalystReviewOutput(
        catalyst_score=0.6, confidence=0.75,
        primary_driver="Q4 beat", reasoning="Beat by 4.2%, raised guidance.",
        flags=["SECTOR_TAILWIND"], suggested_action="STRENGTHEN",
    )
    client = _StubClient(parsed=canned)
    inp = CatalystReviewInput(symbol="X", rule_based_score=18.0)
    r = review_catalyst(inp, client=client)
    assert r.status == OK
    assert r.parsed.suggested_action == "STRENGTHEN"
    assert r.parsed.catalyst_score == 0.6


def test_review_catalyst_handles_parse_error_from_client():
    client = _StubClient(parsed=None, status=PARSE_ERROR, raw="garbage")
    inp = CatalystReviewInput(symbol="X", rule_based_score=10.0)
    r = review_catalyst(inp, client=client)
    assert r.status == PARSE_ERROR
    assert r.parsed is None


# ── apply_catalyst_to_score ──────────────────────────────────────────────────

def test_apply_catalyst_weight_zero_returns_unchanged():
    cat = CatalystReviewOutput(
        catalyst_score=1.0, confidence=1.0, primary_driver="x",
        reasoning="x", suggested_action="STRENGTHEN",
    )
    out = apply_catalyst_to_score(20.0, cat, catalyst_weight=0.0)
    assert out == 20.0


def test_apply_catalyst_weight_positive_boosts_score():
    cat = CatalystReviewOutput(
        catalyst_score=0.5, confidence=0.8, primary_driver="x",
        reasoning="x", suggested_action="STRENGTHEN",
    )
    out = apply_catalyst_to_score(20.0, cat, catalyst_weight=0.2)
    # 20 * (1 + 0.5 * 0.2) = 20 * 1.1 = 22.0
    assert out == pytest.approx(22.0)


def test_apply_catalyst_weight_negative_reduces_score():
    cat = CatalystReviewOutput(
        catalyst_score=-0.5, confidence=0.8, primary_driver="x",
        reasoning="x", suggested_action="WEAKEN",
    )
    out = apply_catalyst_to_score(20.0, cat, catalyst_weight=0.2)
    # 20 * (1 + -0.5 * 0.2) = 20 * 0.9 = 18.0
    assert out == pytest.approx(18.0)


def test_apply_catalyst_block_returns_minus_one():
    cat = CatalystReviewOutput(
        catalyst_score=-0.9, confidence=0.9, primary_driver="fraud",
        reasoning="auditor resigned", suggested_action="BLOCK",
    )
    out = apply_catalyst_to_score(20.0, cat, catalyst_weight=0.2)
    assert out == -1.0


def test_apply_catalyst_none_returns_rule_score():
    out = apply_catalyst_to_score(20.0, None, catalyst_weight=0.2)
    assert out == 20.0


# ── Schema validation ────────────────────────────────────────────────────────

def test_catalyst_score_must_be_in_range():
    with pytest.raises(Exception):
        CatalystReviewOutput(
            catalyst_score=2.0,  # out of [-1, 1]
            confidence=0.5, primary_driver="x", reasoning="x",
            suggested_action="STRENGTHEN",
        )


def test_suggested_action_must_be_valid():
    with pytest.raises(Exception):
        CatalystReviewOutput(
            catalyst_score=0.5, confidence=0.5,
            primary_driver="x", reasoning="x",
            suggested_action="MAYBE",  # not in literal
        )
