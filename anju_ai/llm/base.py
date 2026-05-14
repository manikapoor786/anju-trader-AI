#!/usr/bin/env python3
"""
anju_ai.llm.base — common LLM client interface + structured-output helpers.

Per AGENT_PROTOCOL.md §1 the LLM is a *constrained reasoner*:
  - typed inputs (Pydantic model in)
  - typed outputs (Pydantic model out, parsed from JSON the model emits)
  - bounded toolbox (separate concern; this module is just text in / text out)
  - reasoning budget per call (tokens, timeout)
  - read-only on memory (writes happen in calling loop)

LLMResponse is what every client returns. The structure is fixed so
reasoning_traces inserts work uniformly across providers.

A failed parse retries ONCE with stricter "JSON only, no prose" instruction.
A second failure returns LLMResponse with status='PARSE_ERROR' and the
raw text, so the calling loop can log/skip and continue.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol, Type

from pydantic import BaseModel, ValidationError


# ── Status enum (string for SQLite friendliness) ──────────────────────────────

OK            = "OK"
PARSE_ERROR   = "PARSE_ERROR"
TIMEOUT       = "TIMEOUT"
RATE_LIMITED  = "RATE_LIMITED"
BUDGET        = "BUDGET_EXCEEDED"
API_ERROR     = "API_ERROR"


# ── Response ──────────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """Uniform response from any LLM client. parsed is None on errors."""
    status:          str               # OK / PARSE_ERROR / TIMEOUT / ...
    parsed:          BaseModel | None  # the typed output, when status=OK
    raw_text:        str               # full model output before parsing
    tokens_in:       int
    tokens_out:      int
    latency_ms:      int
    model:           str
    prompt_name:     str
    prompt_version:  int
    cost_inr:        float = 0.0
    error_message:   str = ""
    tool_calls:      list = None


# ── JSON-extraction helper ────────────────────────────────────────────────────

_CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    """Strip code fences / preambles to get a clean JSON string.
    Models often wrap JSON in ```json ... ``` or prefix it with prose.
    Returns the most likely JSON substring."""
    text = (text or "").strip()
    # 1. Try fenced block first
    m = _CODE_FENCE.search(text)
    if m:
        return m.group(1).strip()
    # 2. Try to find the first { ... last matching }
    if "{" in text and "}" in text:
        start = text.index("{")
        end = text.rindex("}")
        if end > start:
            return text[start:end + 1]
    return text


def parse_typed(text: str, schema: Type[BaseModel]) -> BaseModel | None:
    """Try to parse text into the given Pydantic schema. Returns None on
    failure — caller handles retry."""
    try:
        payload = extract_json(text)
        data = json.loads(payload)
        return schema(**data)
    except (json.JSONDecodeError, ValidationError, TypeError):
        return None


# ── Client interface ──────────────────────────────────────────────────────────

class LLMClient(Protocol):
    """Every provider implementation must satisfy this."""

    name: str  # 'gemini' | 'claude' | ...

    def complete(
        self,
        prompt: str,
        schema: Type[BaseModel],
        model: str,
        prompt_name: str,
        prompt_version: int,
        max_tokens_in: int = 4000,
        max_tokens_out: int = 800,
        temperature: float = 0.2,
        timeout_s: float = 30.0,
    ) -> LLMResponse: ...


# ── Retry + parse wrapper used by all providers ───────────────────────────────

def call_with_retry(
    api_call,                     # callable() -> (raw_text, tokens_in, tokens_out, model)
    schema: Type[BaseModel],
    prompt_name: str,
    prompt_version: int,
    cost_per_call_inr: float = 0.0,
    strict_retry_prompt: str | None = None,
    strict_retry_callable=None,
) -> LLMResponse:
    """Run an API call, parse the typed output, retry once on parse error.

    Args:
        api_call: 0-arg callable that hits the provider and returns
            (raw_text:str, tokens_in:int, tokens_out:int, model:str).
            Raises on transport/timeout errors — caller catches.
        schema: Pydantic model to parse into.
        prompt_name / prompt_version: identification for reasoning_traces.
        cost_per_call_inr: estimated INR cost (0 for free tier).
        strict_retry_prompt: if set + first parse fails, prepend this
            instruction and retry once via `strict_retry_callable`.
        strict_retry_callable: 0-arg callable to invoke on retry. Allows
            providers to vary the system prompt without leaking that
            concern up here.

    Returns LLMResponse with appropriate status set.
    """
    t0 = time.time()
    try:
        raw_text, tokens_in, tokens_out, model = api_call()
    except TimeoutError:
        return LLMResponse(
            status=TIMEOUT, parsed=None, raw_text="",
            tokens_in=0, tokens_out=0,
            latency_ms=int((time.time() - t0) * 1000),
            model="", prompt_name=prompt_name, prompt_version=prompt_version,
            error_message="provider timeout",
        )
    except _RateLimitError as e:
        return LLMResponse(
            status=RATE_LIMITED, parsed=None, raw_text="",
            tokens_in=0, tokens_out=0,
            latency_ms=int((time.time() - t0) * 1000),
            model="", prompt_name=prompt_name, prompt_version=prompt_version,
            error_message=str(e),
        )
    except Exception as e:
        return LLMResponse(
            status=API_ERROR, parsed=None, raw_text="",
            tokens_in=0, tokens_out=0,
            latency_ms=int((time.time() - t0) * 1000),
            model="", prompt_name=prompt_name, prompt_version=prompt_version,
            error_message=f"{type(e).__name__}: {e}",
        )

    parsed = parse_typed(raw_text, schema)
    if parsed is not None:
        return LLMResponse(
            status=OK, parsed=parsed, raw_text=raw_text,
            tokens_in=tokens_in, tokens_out=tokens_out,
            latency_ms=int((time.time() - t0) * 1000),
            model=model, prompt_name=prompt_name, prompt_version=prompt_version,
            cost_inr=cost_per_call_inr,
        )

    # First parse failed — try strict retry if provider supplied a callable
    if strict_retry_callable is not None:
        try:
            raw_text2, ti2, to2, m2 = strict_retry_callable()
        except Exception:
            raw_text2 = ""
            ti2 = to2 = 0
            m2 = model
        parsed2 = parse_typed(raw_text2, schema)
        if parsed2 is not None:
            return LLMResponse(
                status=OK, parsed=parsed2, raw_text=raw_text2,
                tokens_in=tokens_in + ti2,
                tokens_out=tokens_out + to2,
                latency_ms=int((time.time() - t0) * 1000),
                model=m2, prompt_name=prompt_name, prompt_version=prompt_version,
                cost_inr=cost_per_call_inr * 2,
            )

    return LLMResponse(
        status=PARSE_ERROR, parsed=None, raw_text=raw_text,
        tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=int((time.time() - t0) * 1000),
        model=model, prompt_name=prompt_name, prompt_version=prompt_version,
        cost_inr=cost_per_call_inr,
        error_message=f"failed to parse output as {schema.__name__}",
    )


class _RateLimitError(Exception):
    """Internal sentinel — providers raise this on HTTP 429."""


def is_rate_limit(exc: Exception) -> bool:
    """Helper for provider classes to recognise rate-limit errors."""
    return isinstance(exc, _RateLimitError)
