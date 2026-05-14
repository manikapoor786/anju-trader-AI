#!/usr/bin/env python3
"""
anju_ai.llm.trace — persist LLMResponse to memory.reasoning_traces.

Per ADR-004 (append-only memory) and AGENT_PROTOCOL §6, every LLM call
gets one row in reasoning_traces — the foundation of auditability.

Usage:
    from anju_ai.llm.gemini import GeminiClient
    from anju_ai.llm.trace import log_trace

    client = GeminiClient()
    response = client.complete(prompt, MySchema, prompt_name="x", prompt_version=1)
    trace_id = log_trace(con, "morning_scan", response,
                         input_payload={"symbol": "RELIANCE", ...},
                         linked_signal_id=42)
"""

from __future__ import annotations

import json
from typing import Any

from anju_ai.llm.base import LLMResponse


def log_trace(con,
              loop: str,
              response: LLMResponse,
              input_payload: dict | None = None,
              tool_calls: list | None = None,
              linked_signal_id: int | None = None,
              linked_outcome_id: int | None = None,
              linked_revision_id: int | None = None) -> int:
    """Insert one reasoning_traces row and return its id."""
    input_json = json.dumps(input_payload or {}, default=str)

    if response.parsed is not None:
        try:
            output_json = response.parsed.model_dump_json()
        except Exception:
            output_json = None
    else:
        output_json = None

    tool_calls_json = (json.dumps(tool_calls, default=str)
                       if tool_calls is not None else None)

    cur = con.execute("""
        INSERT INTO reasoning_traces (
            loop, prompt_name, prompt_version, model,
            input_tokens, output_tokens, latency_ms,
            input_payload_json, output_payload_json, raw_llm_output,
            tool_calls_json, status, error_message,
            linked_signal_id, linked_outcome_id, linked_revision_id,
            cost_inr
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        loop, response.prompt_name, response.prompt_version, response.model,
        response.tokens_in, response.tokens_out, response.latency_ms,
        input_json, output_json, response.raw_text,
        tool_calls_json, response.status, response.error_message or None,
        linked_signal_id, linked_outcome_id, linked_revision_id,
        response.cost_inr,
    ))
    return cur.lastrowid or 0


def recent_traces(con, loop: str | None = None, limit: int = 20) -> list[dict]:
    """Read recent traces — used by the anomaly_qa agent + audit UI."""
    if loop:
        rows = con.execute("""
            SELECT id, loop, prompt_name, model, status, latency_ms,
                   cost_inr, created_at, error_message
              FROM reasoning_traces
             WHERE loop = ?
             ORDER BY id DESC LIMIT ?
        """, (loop, limit)).fetchall()
    else:
        rows = con.execute("""
            SELECT id, loop, prompt_name, model, status, latency_ms,
                   cost_inr, created_at, error_message
              FROM reasoning_traces
             ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def trace_health(con) -> dict:
    """Aggregate stats — used by anomaly_qa to detect LLM-side problems
    (rising parse-error rate, rate-limit spikes, etc)."""
    rows = con.execute("""
        SELECT status, COUNT(*) AS n, AVG(latency_ms) AS avg_lat
          FROM reasoning_traces
         WHERE created_at >= datetime('now', '-24 hours')
         GROUP BY status
    """).fetchall()

    by_status = {r["status"]: {"count": r["n"], "avg_latency_ms": int(r["avg_lat"] or 0)}
                 for r in rows}
    total = sum(v["count"] for v in by_status.values())
    ok    = by_status.get("OK", {}).get("count", 0)
    return {
        "total_24h": total,
        "ok_rate":   round(ok / total * 100, 1) if total else 0.0,
        "by_status": by_status,
    }
