#!/usr/bin/env python3
"""
anju_ai.llm.claude — Claude (Anthropic) client.

Used ONLY by the weekly_critic loop in Phase 3.2. Once per week, ~₹5-10
per call. All high-volume loops stay on Gemini free tier.

Requires ANTHROPIC_API_KEY in env. Uses raw HTTP — no anthropic SDK dep,
fully unit-testable.

API: https://api.anthropic.com/v1/messages  (Messages API)
"""

from __future__ import annotations

import json
import os
from typing import Type

import requests
from pydantic import BaseModel
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as _Retry

from anju_ai.llm.base import (
    LLMResponse,
    _RateLimitError,
    call_with_retry,
)


_retry = _Retry(total=2, backoff_factor=0.5,
                status_forcelist=[500, 502, 503, 504])
_http = requests.Session()
_http.mount("https://", HTTPAdapter(max_retries=_retry))


BASE_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


# Per-input-token + per-output-token cost (₹). Calibrate against actual
# Anthropic billing monthly in Phase 1.7. As of 2025 Sonnet pricing:
#   $3 / 1M in, $15 / 1M out  →  at ₹85/$ ≈ ₹0.000255/in, ₹0.001275/out
COST_PER_INPUT_TOKEN_INR = {
    "claude-sonnet-4-6": 0.000255,
    "claude-haiku-4-5":  0.0000680,
    "claude-haiku-4-5-20251001": 0.0000680,
}
COST_PER_OUTPUT_TOKEN_INR = {
    "claude-sonnet-4-6": 0.001275,
    "claude-haiku-4-5":  0.000340,
    "claude-haiku-4-5-20251001": 0.000340,
}


def _schema_instruction(schema: Type[BaseModel]) -> str:
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    return (
        "\n\nRespond with ONE JSON object conforming to this schema. "
        "No prose, no code fences:\n"
        f"{schema_json}\n"
    )


class ClaudeClient:
    """Anthropic Messages API client. Implements LLMClient protocol."""

    name = "claude"

    def __init__(self, api_key: str | None = None, http_post=None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._post = http_post or self._real_post

    def _real_post(self, url: str, *, json=None, headers=None, timeout=30.0):
        return _http.post(url, json=json, headers=headers, timeout=timeout)

    def complete(
        self,
        prompt: str,
        schema: Type[BaseModel],
        model: str = "claude-sonnet-4-6",
        prompt_name: str = "anon",
        prompt_version: int = 1,
        max_tokens_in: int = 8000,
        max_tokens_out: int = 2000,
        temperature: float = 0.2,
        timeout_s: float = 60.0,
    ) -> LLMResponse:

        if not self.api_key:
            return LLMResponse(
                status="API_ERROR", parsed=None, raw_text="",
                tokens_in=0, tokens_out=0, latency_ms=0,
                model=model, prompt_name=prompt_name,
                prompt_version=prompt_version,
                error_message="ANTHROPIC_API_KEY not set",
            )

        full_prompt = prompt + _schema_instruction(schema)

        def _do_call(prompt_text: str):
            body = {
                "model": model,
                "max_tokens": max_tokens_out,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt_text}],
            }
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            }
            r = self._post(BASE_URL, json=body, headers=headers, timeout=timeout_s)
            if r.status_code == 429:
                raise _RateLimitError(f"Claude 429: {_safe_text(r)[:200]}")
            if r.status_code >= 500:
                raise RuntimeError(f"Claude 5xx {r.status_code}: {_safe_text(r)[:200]}")
            if r.status_code != 200:
                raise RuntimeError(f"Claude {r.status_code}: {_safe_text(r)[:300]}")

            payload = r.json()
            text = _extract_text(payload)
            usage = payload.get("usage", {}) or {}
            tokens_in  = int(usage.get("input_tokens", 0) or 0)
            tokens_out = int(usage.get("output_tokens", 0) or 0)
            return text, tokens_in, tokens_out, model

        cost_call = (COST_PER_INPUT_TOKEN_INR.get(model, 0.000255) * max_tokens_in +
                     COST_PER_OUTPUT_TOKEN_INR.get(model, 0.001275) * max_tokens_out)

        api_call = lambda: _do_call(full_prompt)
        strict_retry = lambda: _do_call(
            "RESPONSE MUST BE PURE JSON CONFORMING TO THE SCHEMA. "
            "NO TEXT, NO CODE FENCES.\n\n" + full_prompt
        )

        out = call_with_retry(
            api_call=api_call, schema=schema,
            prompt_name=prompt_name, prompt_version=prompt_version,
            cost_per_call_inr=cost_call,
            strict_retry_callable=strict_retry,
        )

        # Replace estimated cost with actual when we have token usage
        if out.status == "OK" and out.tokens_in > 0:
            actual = (COST_PER_INPUT_TOKEN_INR.get(model, 0.000255) * out.tokens_in +
                      COST_PER_OUTPUT_TOKEN_INR.get(model, 0.001275) * out.tokens_out)
            out.cost_inr = round(actual, 4)
        return out


def _extract_text(payload: dict) -> str:
    """Pull the assistant message text out of Claude's response shape."""
    try:
        blocks = payload.get("content", [])
        if not blocks:
            return ""
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    except Exception:
        return ""


def _safe_text(response) -> str:
    try:
        return response.text
    except Exception:
        return ""
