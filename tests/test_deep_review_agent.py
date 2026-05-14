"""Tests for anju_ai.loops.deep_review_agent — stubbed LLM, no network."""

import pandas as pd
import pytest

from anju_ai.loops.deep_review_agent import (
    DeepReviewInput,
    DeepReviewOutput,
    KeyLevels,
    OptionsRecommendation,
    _df_summary,
    _load_prompt,
    _render,
    render_report_telegram,
    review,
)
from anju_ai.llm.base import LLMResponse, OK, PARSE_ERROR


class _Stub:
    name = "stub"
    def __init__(self, parsed=None, status=OK):
        self._parsed = parsed
        self._status = status
    def complete(self, prompt, schema, model, prompt_name, prompt_version,
                 max_tokens_in=10000, max_tokens_out=3000,
                 temperature=0.3, timeout_s=60.0):
        return LLMResponse(
            status=self._status, parsed=self._parsed, raw_text="canned",
            tokens_in=2000, tokens_out=800, latency_ms=4000,
            model=model, prompt_name=prompt_name, prompt_version=prompt_version,
        )


# ── Prompt + render ──────────────────────────────────────────────────────────

def test_load_prompt_v1_exists():
    text = _load_prompt("deep_review", 1)
    assert "DEEP REVIEW" in text
    assert "bull_case" in text


def test_render_includes_all_sections():
    inp = DeepReviewInput(
        symbol="X", horizon="BOTH", user_question="Is this strong?",
        rule_based_score=15.0, rule_based_verdict="BUY",
        daily_summary={"label": "1D"}, weekly_summary={"label": "1W"},
        hourly_summary={"label": "1H"}, flows_summary={"insider": {}},
        news_30d=[], filings_90d=[], similar_past_trades=[],
    )
    out = _render(inp)
    assert "SYMBOL: X" in out
    assert "Horizon: BOTH" in out
    assert "Is this strong?" in out
    assert "DAILY SUMMARY" in out
    assert "WEEKLY SUMMARY" in out
    assert "FLOWS" in out


def test_render_includes_news_and_filings_when_present():
    inp = DeepReviewInput(
        symbol="X", horizon="BOTH", rule_based_score=10, rule_based_verdict="WATCH",
        daily_summary={}, weekly_summary={}, hourly_summary={},
        flows_summary={},
        news_30d=[{"title": "Earnings beat", "source": "MoneyControl"}],
        filings_90d=[{"kind": "RESULTS", "headline": "Q4 results filed"}],
        similar_past_trades=[],
    )
    out = _render(inp)
    assert "Earnings beat" in out
    assert "Q4 results filed" in out


def test_render_includes_similar_trades():
    inp = DeepReviewInput(
        symbol="X", horizon="BOTH", rule_based_score=15, rule_based_verdict="BUY",
        daily_summary={}, weekly_summary={}, hourly_summary={},
        flows_summary={},
        news_30d=[], filings_90d=[],
        similar_past_trades=[{
            "symbol": "Y", "final_score": 14.5,
            "outcome_kind": "WIN_T1", "net_pnl_pct": 5.2,
            "days_held": 8, "lesson": "Held above MA20",
        }],
    )
    out = _render(inp)
    assert "WIN_T1" in out
    assert "Y" in out


# ── _df_summary ──────────────────────────────────────────────────────────────

def test_df_summary_handles_none():
    out = _df_summary(None, "1D")
    assert out["bars"] == 0


def test_df_summary_handles_short_df():
    df = pd.DataFrame({"Open": [1], "High": [1], "Low": [1],
                       "Close": [1], "Volume": [100]})
    out = _df_summary(df, "1D")
    assert out["bars"] == 0


def test_df_summary_computes_key_metrics():
    n = 100
    df = pd.DataFrame({
        "Open":   [100 + i for i in range(n)],
        "High":   [101 + i for i in range(n)],
        "Low":    [99 + i for i in range(n)],
        "Close":  [100 + i for i in range(n)],
        "Volume": [1_000_000] * n,
    }, index=pd.bdate_range(start="2025-01-01", periods=n))
    out = _df_summary(df, "1D")
    assert out["bars"] == 100
    assert out["current_price"] == 199.0
    assert out["52w_high"] == 200.0
    assert out["52w_low"] == 99.0
    assert out["avg_vol_20"] == 1_000_000


# ── review ───────────────────────────────────────────────────────────────────

def _sample_input():
    return DeepReviewInput(
        symbol="X", horizon="BOTH", rule_based_score=15, rule_based_verdict="BUY",
        daily_summary={}, weekly_summary={}, hourly_summary={},
        flows_summary={}, news_30d=[], filings_90d=[], similar_past_trades=[],
    )


def test_review_happy_path():
    canned = DeepReviewOutput(
        bull_case=["MA20 rising", "above weekly pivot"],
        bear_case=["weekly RSI 78", "no fresh catalyst"],
        base_case_outcome="3-5% in 2 weeks",
        swing_verdict="BUY", positional_verdict="WATCH",
        key_levels=KeyLevels(support=95.0, resistance=110.0, invalidation=92.0),
        confidence=0.7,
        blind_spots=["Q4 results in 5 days"],
    )
    r = review(_sample_input(), client=_Stub(parsed=canned))
    assert r.status == OK
    assert r.parsed.swing_verdict == "BUY"
    assert r.parsed.confidence == 0.7


def test_review_propagates_error():
    r = review(_sample_input(),
               client=_Stub(parsed=None, status=PARSE_ERROR))
    assert r.status == PARSE_ERROR


# ── Schema validation ───────────────────────────────────────────────────────

def test_confidence_must_be_in_range():
    with pytest.raises(Exception):
        DeepReviewOutput(
            bull_case=["a"], bear_case=["b"], base_case_outcome="x",
            swing_verdict="BUY", positional_verdict="BUY",
            key_levels=KeyLevels(support=1, resistance=2, invalidation=0.5),
            confidence=1.5,  # > 1.0
            blind_spots=["x"],
        )


def test_bull_case_must_have_at_least_one():
    with pytest.raises(Exception):
        DeepReviewOutput(
            bull_case=[],  # empty — should fail min_length=1
            bear_case=["a"], base_case_outcome="x",
            swing_verdict="BUY", positional_verdict="BUY",
            key_levels=KeyLevels(support=1, resistance=2, invalidation=0.5),
            confidence=0.5, blind_spots=["x"],
        )


# ── Render Telegram ──────────────────────────────────────────────────────────

def test_render_telegram_includes_all_sections():
    out = DeepReviewOutput(
        bull_case=["bull1", "bull2"],
        bear_case=["bear1"],
        base_case_outcome="sideways consolidation",
        swing_verdict="WATCH", positional_verdict="BUY",
        key_levels=KeyLevels(support=100.0, resistance=120.0, invalidation=95.0),
        options_recommendation=OptionsRecommendation(
            instrument="ATM_CALL", rationale="IVP low, score high"),
        confidence=0.75,
        blind_spots=["earnings in 3 days"],
    )
    msg = render_report_telegram("RELIANCE", out)
    assert "Deep Review" in msg
    assert "RELIANCE" in msg
    assert "75%" in msg or "0.75" in msg
    assert "bull1" in msg
    assert "bear1" in msg
    assert "ATM_CALL" in msg
    assert "earnings" in msg
