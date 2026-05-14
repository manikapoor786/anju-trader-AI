#!/usr/bin/env python3
"""
anju_ai.loops.anomaly_qa — every-3h system self-monitor.

Per AGENT_PROTOCOL §2.4 / ROADMAP 3.4. Reads:
  - reasoning_traces health (error rates, latency, rate-limits)
  - audit ledger (workflow run history, recent errors)
  - regime_snapshots last 30 days (detect classifier flipping)
  - signal counts per day vs trailing average
  - data freshness (latest bhavcopy_log, latest flows_snapshots)

Sends the structured snapshot to Gemini Flash. Returns a list of
anomalies with severity. Telegram-alerts only WARN/CRITICAL.

Usage:
    python -m anju_ai.loops.anomaly_qa
    python -m anju_ai.loops.anomaly_qa --dry-run        # don't call LLM
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from anju_ai.memory.db import audit_log, init_if_needed
from anju_ai.llm.gemini import GeminiClient
from anju_ai.llm.base import LLMResponse, OK
from anju_ai.llm.trace import log_trace, trace_health


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class Anomaly(BaseModel):
    severity: Literal["INFO", "WARN", "CRITICAL"]
    category: str
    description: str
    suggested_fix: str
    auto_remediable: bool = False
    workflow_to_trigger: str | None = None


class AnomalyQAInput(BaseModel):
    snapshot_time: str
    workflow_health: dict          # {name: {ok_count, fail_count, last_run}}
    data_freshness: dict           # {source: last_date_str}
    regime_history: list[dict]     # [{date, state, min_score}]
    signal_count_history: list[dict]   # [{date, n}]
    llm_trace_health: dict         # {total, ok_rate, by_status}
    open_position_count: int
    recent_errors: list[dict]      # tail of audit where severity in (WARN, CRITICAL)


class AnomalyQAOutput(BaseModel):
    anomalies: list[Anomaly] = Field(default_factory=list)


# ── Telegram ─────────────────────────────────────────────────────────────────

def tg_send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print(text)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        return r.ok
    except Exception:
        return False


# ── Snapshot collectors ──────────────────────────────────────────────────────

def collect_workflow_health(con) -> dict:
    rows = con.execute("""
        SELECT event_type AS name,
               SUM(CASE WHEN severity='INFO' THEN 1 ELSE 0 END) AS ok,
               SUM(CASE WHEN severity IN ('WARN','CRITICAL') THEN 1 ELSE 0 END) AS bad,
               MAX(event_at) AS last_run
          FROM audit
         WHERE event_at >= datetime('now', '-48 hours')
         GROUP BY event_type
    """).fetchall()
    return {r["name"]: {"ok": r["ok"], "bad": r["bad"], "last_run": r["last_run"]}
            for r in rows}


def collect_data_freshness(con) -> dict:
    """Latest known date per data source."""
    out: dict[str, str] = {}
    try:
        r = con.execute(
            "SELECT MAX(snapshot_date) FROM regime_snapshots").fetchone()
        if r and r[0]:
            out["regime"] = r[0]
    except Exception:
        pass
    try:
        r = con.execute(
            "SELECT MAX(snapshot_date) FROM flows_snapshots").fetchone()
        if r and r[0]:
            out["flows"] = r[0]
    except Exception:
        pass
    # historical.db is a separate file — only check if locally available
    try:
        from anju_core.data_layer import _resolve_db_path
        import sqlite3
        p = _resolve_db_path()
        if p.exists():
            hcon = sqlite3.connect(p)
            r = hcon.execute("SELECT MAX(date) FROM bhavcopy_log").fetchone()
            hcon.close()
            if r and r[0]:
                out["bhavcopy"] = r[0]
    except Exception:
        pass
    return out


def collect_regime_history(con, days: int = 30) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = con.execute("""
        SELECT snapshot_date, state, min_score
          FROM regime_snapshots
         WHERE snapshot_date >= ?
         ORDER BY snapshot_date
    """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def collect_signal_history(con, days: int = 14) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = con.execute("""
        SELECT signal_date AS date, COUNT(*) AS n
          FROM signals_current
         WHERE signal_date >= ? AND backtest_run_id IS NULL
         GROUP BY signal_date
         ORDER BY signal_date
    """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def collect_open_position_count(con) -> int:
    r = con.execute("""
        SELECT COUNT(*) FROM signals_current s
        JOIN fills f ON f.signal_id = s.id
        WHERE s.backtest_run_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM outcomes o WHERE o.fill_id = f.id)
    """).fetchone()
    return int(r[0]) if r else 0


def collect_recent_errors(con, limit: int = 20) -> list[dict]:
    rows = con.execute("""
        SELECT event_at, event_type, severity, summary
          FROM audit
         WHERE severity IN ('WARN','CRITICAL')
           AND event_at >= datetime('now', '-48 hours')
         ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def build_snapshot(con) -> AnomalyQAInput:
    return AnomalyQAInput(
        snapshot_time=datetime.now(timezone.utc).isoformat(),
        workflow_health=collect_workflow_health(con),
        data_freshness=collect_data_freshness(con),
        regime_history=collect_regime_history(con),
        signal_count_history=collect_signal_history(con),
        llm_trace_health=trace_health(con),
        open_position_count=collect_open_position_count(con),
        recent_errors=collect_recent_errors(con),
    )


# ── Prompt loader + render ───────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"


def _load_prompt(name: str, version: int) -> str:
    text = (_PROMPTS_DIR / f"{name}.v{version}.md").read_text()
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    return text.strip()


def _render(inp: AnomalyQAInput) -> str:
    return ("## SYSTEM SNAPSHOT\n"
            f"Time: {inp.snapshot_time}\n"
            f"Open positions: {inp.open_position_count}\n\n"
            f"### Workflow health (last 48h)\n"
            + json.dumps(inp.workflow_health, indent=2, default=str)
            + "\n\n### Data freshness\n"
            + json.dumps(inp.data_freshness, indent=2)
            + "\n\n### Regime history (last 30d)\n"
            + json.dumps(inp.regime_history, indent=2)
            + "\n\n### Signal count history (last 14d)\n"
            + json.dumps(inp.signal_count_history, indent=2)
            + "\n\n### LLM trace health (last 24h)\n"
            + json.dumps(inp.llm_trace_health, indent=2)
            + "\n\n### Recent errors (last 48h)\n"
            + json.dumps(inp.recent_errors, indent=2, default=str))


def review_anomalies(inp: AnomalyQAInput, client=None,
                     prompt_version: int = 1) -> LLMResponse:
    if client is None:
        client = GeminiClient()
    prompt = _load_prompt("anomaly_qa", prompt_version) + "\n\n" + _render(inp)
    return client.complete(
        prompt=prompt, schema=AnomalyQAOutput,
        model="gemini-1.5-flash",
        prompt_name="anomaly_qa", prompt_version=prompt_version,
        max_tokens_in=3000, max_tokens_out=800,
        temperature=0.2, timeout_s=30.0,
    )


# ── Main loop ────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> int:
    print(f"\n🔍 anju-AI anomaly_qa  {datetime.now().strftime('%H:%M IST')}\n")
    con = init_if_needed()
    try:
        snap = build_snapshot(con)
        if dry_run:
            print("Dry run — snapshot collected:")
            print(_render(snap)[:1500])
            return 0

        resp = review_anomalies(snap)
        log_trace(con, "anomaly_qa", resp, input_payload=snap.model_dump())

        if resp.status != OK or resp.parsed is None:
            audit_log(con, "ANOMALY_QA_FAILED",
                      f"LLM returned {resp.status}: {resp.error_message}",
                      severity="WARN")
            return 1

        anomalies = resp.parsed.anomalies
        critical = [a for a in anomalies if a.severity == "CRITICAL"]
        warn     = [a for a in anomalies if a.severity == "WARN"]
        info     = [a for a in anomalies if a.severity == "INFO"]

        audit_log(con, "ANOMALY_QA_RUN",
                  f"{len(critical)} critical · {len(warn)} warn · {len(info)} info")

        if critical or warn:
            _send_alert(critical, warn, info)
        else:
            print("✅ No anomalies — system healthy")
    finally:
        con.close()
    return 0


def _send_alert(critical: list[Anomaly], warn: list[Anomaly],
                info: list[Anomaly]) -> None:
    now = datetime.now().strftime("%d %b %Y %H:%M")
    lines = [f"🔍 <b>anju-AI · Anomaly QA</b>  ·  {now} IST"]
    if critical:
        lines.append("\n🔴 <b>CRITICAL</b>")
        for a in critical:
            lines.append(f"  <b>{a.category}</b>: {a.description}")
            lines.append(f"    → <i>{a.suggested_fix}</i>")
    if warn:
        lines.append("\n🟡 <b>WARN</b>")
        for a in warn:
            lines.append(f"  <b>{a.category}</b>: {a.description}")
            lines.append(f"    → <i>{a.suggested_fix}</i>")
    if info and len(critical) + len(warn) < 5:   # don't bury alerts
        lines.append("\n🟢 <b>INFO</b>")
        for a in info[:3]:
            lines.append(f"  <b>{a.category}</b>: {a.description}")
    tg_send("\n".join(lines))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
