"""Tests for anju_ai.llm.* — mock providers, no real API calls."""

from dataclasses import dataclass
import json
import pytest
from pydantic import BaseModel

from anju_ai.llm.base import (
    LLMResponse,
    OK, PARSE_ERROR, TIMEOUT, RATE_LIMITED, API_ERROR,
    _RateLimitError,
    call_with_retry,
    extract_json,
    parse_typed,
)
from anju_ai.llm.gemini import GeminiClient, _schema_instruction
from anju_ai.llm.claude import ClaudeClient
from anju_ai.llm.trace import log_trace, recent_traces, trace_health


# ── Fake response ────────────────────────────────────────────────────────────

@dataclass
class FakeResp:
    status_code: int
    _json: object
    text: str = ""

    def json(self):
        return self._json


# ── Sample schemas ───────────────────────────────────────────────────────────

class _Verdict(BaseModel):
    score: float
    label: str
    confidence: float


# ── extract_json / parse_typed ───────────────────────────────────────────────

def test_extract_json_unwraps_code_fence():
    text = '```json\n{"score": 1.0, "label": "ok", "confidence": 0.9}\n```'
    out = extract_json(text)
    assert out.strip().startswith("{")
    assert "ok" in out


def test_extract_json_strips_prose_preamble():
    text = 'Here is your answer:\n{"score": 1.0, "label": "ok", "confidence": 0.9}\nDone.'
    out = extract_json(text)
    assert out == '{"score": 1.0, "label": "ok", "confidence": 0.9}'


def test_parse_typed_success():
    out = parse_typed('{"score": 0.5, "label": "neutral", "confidence": 0.7}',
                      _Verdict)
    assert out is not None
    assert out.score == 0.5


def test_parse_typed_returns_none_on_invalid_json():
    assert parse_typed("not json", _Verdict) is None


def test_parse_typed_returns_none_on_missing_field():
    assert parse_typed('{"score": 0.5}', _Verdict) is None


# ── call_with_retry ──────────────────────────────────────────────────────────

def test_call_with_retry_returns_ok_on_first_success():
    api = lambda: ('{"score": 1.0, "label": "buy", "confidence": 0.8}',
                   100, 30, "test-model")
    out = call_with_retry(api, _Verdict, "test", 1)
    assert out.status == OK
    assert isinstance(out.parsed, _Verdict)
    assert out.parsed.score == 1.0


def test_call_with_retry_retries_on_parse_failure():
    calls = [0]
    def api():
        calls[0] += 1
        return ("not json initially", 10, 5, "m")
    def retry():
        return ('{"score": 0.5, "label": "ok", "confidence": 0.9}', 12, 6, "m")
    out = call_with_retry(api, _Verdict, "test", 1,
                          strict_retry_callable=retry)
    assert out.status == OK
    assert out.parsed.score == 0.5
    assert out.tokens_in == 22   # both calls counted


def test_call_with_retry_parse_error_after_double_fail():
    api = lambda: ("garbage", 10, 5, "m")
    retry = lambda: ("more garbage", 10, 5, "m")
    out = call_with_retry(api, _Verdict, "test", 1,
                          strict_retry_callable=retry)
    assert out.status == PARSE_ERROR
    assert out.parsed is None


def test_call_with_retry_handles_timeout():
    def api():
        raise TimeoutError("slow")
    out = call_with_retry(api, _Verdict, "test", 1)
    assert out.status == TIMEOUT


def test_call_with_retry_handles_rate_limit():
    def api():
        raise _RateLimitError("429")
    out = call_with_retry(api, _Verdict, "test", 1)
    assert out.status == RATE_LIMITED


def test_call_with_retry_handles_arbitrary_exception():
    def api():
        raise ValueError("boom")
    out = call_with_retry(api, _Verdict, "test", 1)
    assert out.status == API_ERROR
    assert "ValueError" in out.error_message


# ── GeminiClient ─────────────────────────────────────────────────────────────

def _gemini_text_payload(text: str, in_tok=100, out_tok=30):
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": in_tok,
                          "candidatesTokenCount": out_tok},
    }


def test_gemini_client_returns_ok_on_clean_json():
    def post(url, *, json=None, params=None, timeout=30.0):
        return FakeResp(200, _gemini_text_payload(
            '{"score": 1.0, "label": "buy", "confidence": 0.85}'))
    c = GeminiClient(api_key="fake-key", http_post=post)
    r = c.complete(prompt="rate this signal", schema=_Verdict,
                   prompt_name="test", prompt_version=1)
    assert r.status == OK
    assert r.parsed.label == "buy"
    assert r.tokens_in == 100
    assert r.cost_inr == 0.0   # free tier


def test_gemini_client_handles_429():
    def post(url, **kw):
        return FakeResp(429, {}, text='{"error":"rate"}')
    c = GeminiClient(api_key="fake-key", http_post=post)
    r = c.complete(prompt="x", schema=_Verdict,
                   prompt_name="t", prompt_version=1)
    assert r.status == RATE_LIMITED


def test_gemini_client_handles_500_as_api_error():
    def post(url, **kw):
        return FakeResp(500, {}, text="internal")
    c = GeminiClient(api_key="fake-key", http_post=post)
    r = c.complete(prompt="x", schema=_Verdict,
                   prompt_name="t", prompt_version=1)
    assert r.status == API_ERROR


def test_gemini_client_returns_error_when_no_api_key():
    c = GeminiClient(api_key="")
    r = c.complete(prompt="x", schema=_Verdict,
                   prompt_name="t", prompt_version=1)
    assert r.status == API_ERROR
    assert "GEMINI_API_KEY" in r.error_message


def test_gemini_schema_instruction_includes_schema():
    inst = _schema_instruction(_Verdict)
    assert "JSON" in inst
    assert "score" in inst   # from schema
    assert "label" in inst


# ── ClaudeClient ─────────────────────────────────────────────────────────────

def _claude_text_payload(text: str, in_tok=200, out_tok=80):
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    }


def test_claude_client_returns_ok_on_clean_json():
    def post(url, *, json=None, headers=None, timeout=30.0):
        return FakeResp(200, _claude_text_payload(
            '{"score": 0.7, "label": "watch", "confidence": 0.6}'))
    c = ClaudeClient(api_key="fake-key", http_post=post)
    r = c.complete(prompt="rate", schema=_Verdict,
                   prompt_name="weekly_critic", prompt_version=1)
    assert r.status == OK
    assert r.parsed.label == "watch"
    assert r.tokens_in == 200
    assert r.cost_inr > 0    # paid tier


def test_claude_client_no_api_key_errors():
    c = ClaudeClient(api_key="")
    r = c.complete(prompt="x", schema=_Verdict,
                   prompt_name="t", prompt_version=1)
    assert r.status == API_ERROR
    assert "ANTHROPIC_API_KEY" in r.error_message


# ── log_trace + recent_traces + trace_health ─────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    yield con
    con.close()


def test_log_trace_inserts_row(isolated_db):
    response = LLMResponse(
        status=OK, parsed=_Verdict(score=0.9, label="buy", confidence=0.7),
        raw_text='{"score":0.9,"label":"buy","confidence":0.7}',
        tokens_in=100, tokens_out=20, latency_ms=850,
        model="gemini-1.5-flash", prompt_name="catalyst_review",
        prompt_version=1, cost_inr=0.0,
    )
    tid = log_trace(isolated_db, "catalyst_review", response,
                    input_payload={"symbol": "RELIANCE"})
    assert tid > 0

    row = isolated_db.execute(
        "SELECT loop, model, status, cost_inr FROM reasoning_traces WHERE id=?",
        (tid,),
    ).fetchone()
    assert row["loop"] == "catalyst_review"
    assert row["status"] == "OK"
    assert row["model"] == "gemini-1.5-flash"


def test_log_trace_records_failure_with_message(isolated_db):
    response = LLMResponse(
        status=PARSE_ERROR, parsed=None, raw_text="garbage output",
        tokens_in=50, tokens_out=10, latency_ms=300,
        model="gemini-1.5-flash", prompt_name="catalyst_review",
        prompt_version=1, error_message="failed to parse output",
    )
    tid = log_trace(isolated_db, "catalyst_review", response)
    row = isolated_db.execute(
        "SELECT status, error_message, output_payload_json FROM reasoning_traces "
        "WHERE id=?", (tid,)
    ).fetchone()
    assert row["status"] == "PARSE_ERROR"
    assert "failed to parse" in row["error_message"]
    assert row["output_payload_json"] is None


def test_recent_traces_filters_by_loop(isolated_db):
    r1 = LLMResponse(status=OK, parsed=_Verdict(score=1, label="a", confidence=1),
                     raw_text="x", tokens_in=1, tokens_out=1, latency_ms=1,
                     model="m", prompt_name="catalyst_review", prompt_version=1)
    r2 = LLMResponse(status=OK, parsed=_Verdict(score=1, label="a", confidence=1),
                     raw_text="x", tokens_in=1, tokens_out=1, latency_ms=1,
                     model="m", prompt_name="post_mortem", prompt_version=1)
    log_trace(isolated_db, "catalyst_review", r1)
    log_trace(isolated_db, "post_mortem", r2)

    cr = recent_traces(isolated_db, loop="catalyst_review")
    assert len(cr) == 1
    assert cr[0]["loop"] == "catalyst_review"


def test_trace_health_counts_status(isolated_db):
    for kind in ["OK", "OK", "OK", "PARSE_ERROR"]:
        r = LLMResponse(
            status=kind, parsed=None, raw_text="x",
            tokens_in=1, tokens_out=1, latency_ms=100,
            model="m", prompt_name="t", prompt_version=1,
        )
        log_trace(isolated_db, "test", r)

    h = trace_health(isolated_db)
    assert h["total_24h"] == 4
    assert h["ok_rate"] == 75.0
    assert h["by_status"]["OK"]["count"] == 3
    assert h["by_status"]["PARSE_ERROR"]["count"] == 1
