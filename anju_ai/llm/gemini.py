#!/usr/bin/env python3
"""
anju_ai.llm.gemini — Gemini client (Google AI Studio free tier by default).

Free tier (Gemini 1.5 Flash, as of 2025):
  - 1,500 requests/day
  - 15 RPM
  - 1M tokens/min input

Our projected daily usage:
  - catalyst_review: ~30 calls/day
  - post_mortem:     ~5 calls/day
  - anomaly_qa:      ~8 calls/day
  - deep_review:     ~1 call/day on demand
  Total:             ~50 calls/day → comfortably within free tier.

API endpoint:
  https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent

Requires GEMINI_API_KEY in env. Uses raw HTTP — avoids the google-generativeai
SDK as a dep so the module is fully unit-testable without network.
"""

from __future__ import annotations

import json
import os
import time
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


# ── HTTP session ──────────────────────────────────────────────────────────────

_retry = _Retry(total=2, backoff_factor=0.5,
                status_forcelist=[500, 502, 503, 504])
_http = requests.Session()
_http.mount("https://", HTTPAdapter(max_retries=_retry))


# ── Endpoint ──────────────────────────────────────────────────────────────────

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _endpoint(model: str) -> str:
    return f"{BASE_URL}/{model}:generateContent"


# ── Schema-to-instruction helper ──────────────────────────────────────────────

def _schema_instruction(schema: Type[BaseModel]) -> str:
    """Generate a Gemini-friendly instruction block from a Pydantic schema.
    Gemini follows clear instructions about the JSON shape; we don't need
    the full response_schema API (which has its own quirks)."""
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    return (
        "\n\nYou MUST respond with valid JSON only — no prose, no code "
        "fences, no preamble. The JSON MUST conform to this schema:\n"
        f"{schema_json}\n"
    )


# ── Client ────────────────────────────────────────────────────────────────────

class GeminiClient:
    """Free-tier-friendly Gemini client. Implements LLMClient protocol."""

    name = "gemini"

    def __init__(self, api_key: str | None = None,
                 http_post=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        # Optional test override — must accept (url, json, params, timeout)
        self._post = http_post or self._real_post

    def _real_post(self, url: str, *, json=None, params=None, timeout=30.0):
        return _http.post(url, json=json, params=params, timeout=timeout)

    def complete(
        self,
        prompt: str,
        schema: Type[BaseModel],
        model: str = "gemini-1.5-flash",
        prompt_name: str = "anon",
        prompt_version: int = 1,
        max_tokens_in: int = 4000,
        max_tokens_out: int = 800,
        temperature: float = 0.2,
        timeout_s: float = 30.0,
    ) -> LLMResponse:
        """Hit Gemini, parse the response as `schema`. Retries once on parse
        error with a stricter "JSON only" instruction."""

        if not self.api_key:
            return LLMResponse(
                status="API_ERROR", parsed=None, raw_text="",
                tokens_in=0, tokens_out=0, latency_ms=0,
                model=model, prompt_name=prompt_name,
                prompt_version=prompt_version,
                error_message="GEMINI_API_KEY not set",
            )

        full_prompt = prompt + _schema_instruction(schema)

        def _do_call(prompt_text: str):
            body = {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens_out,
                    "responseMimeType": "application/json",
                },
            }
            r = self._post(_endpoint(model), json=body,
                           params={"key": self.api_key}, timeout=timeout_s)
            if r.status_code == 429:
                raise _RateLimitError(f"Gemini 429: {_safe_text(r)[:200]}")
            if r.status_code >= 500:
                raise RuntimeError(f"Gemini 5xx {r.status_code}: {_safe_text(r)[:200]}")
            if r.status_code != 200:
                raise RuntimeError(f"Gemini {r.status_code}: {_safe_text(r)[:300]}")

            payload = r.json()
            text = _extract_text(payload)
            usage = payload.get("usageMetadata", {}) or {}
            tokens_in  = int(usage.get("promptTokenCount", 0) or 0)
            tokens_out = int(usage.get("candidatesTokenCount", 0) or 0)
            return text, tokens_in, tokens_out, model

        api_call = lambda: _do_call(full_prompt)
        strict_retry = lambda: _do_call(
            "RESPONSE MUST BE PURE JSON CONFORMING TO THE SCHEMA. "
            "NO TEXT, NO CODE FENCES, NO EXPLANATION.\n\n" + full_prompt
        )

        return call_with_retry(
            api_call=api_call, schema=schema,
            prompt_name=prompt_name, prompt_version=prompt_version,
            cost_per_call_inr=0.0,        # Gemini Flash free tier
            strict_retry_callable=strict_retry,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_text(payload: dict) -> str:
    """Pull the model's text out of Gemini's nested response shape."""
    try:
        candidates = payload.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip()
    except Exception:
        return ""


def _safe_text(response) -> str:
    try:
        return response.text
    except Exception:
        return ""
