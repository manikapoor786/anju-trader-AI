#!/usr/bin/env python3
"""
anju_ai.loops.audit_report — on-demand reasoning-trace audit UI.

Per AGENT_PROTOCOL §6: every decision in anju-AI is logged. This loop
produces a human-readable summary of recent activity, sliced by:
  - Signals generated (with verdict mix)
  - Outcomes closed (with WIN/LOSS breakdown + P&L)
  - LLM trace health by loop (success rate, latency, cost)
  - Lessons written + flagged-for-revision count
  - Revisions: proposed / awaiting approval / applied
  - Anomalies flagged (WARN/CRITICAL)

Sends the report to Telegram. Phase 4 will add an HTML version
downloadable via gh-pages — for now Telegram is the UI per ADR-006.

Usage:
    python -m anju_ai.loops.audit_report           # last 7 days
    python -m anju_ai.loops.audit_report --days 30 # custom window
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from anju_ai.memory.db import init_if_needed
from anju_ai.llm.trace import trace_health


# ── Telegram ─────────────────────────────────────────────────────────────────

def tg_send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print(text)
        return False
    try:
        for chunk in _split_text(text, 4000):
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": chunk,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=20,
            )
            if not r.ok:
                return False
        return True
    except Exception:
        return False


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            out.append(cur); cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        out.append(cur)
    return out


# ── Aggregators ──────────────────────────────────────────────────────────────

def signals_summary(con, since: str) -> dict:
    rows = con.execute("""
        SELECT verdict, COUNT(*) AS n
          FROM signals_current
         WHERE signal_date >= ? AND backtest_run_id IS NULL
         GROUP BY verdict
    """, (since,)).fetchall()
    total = sum(r["n"] for r in rows)
    return {
        "total": total,
        "by_verdict": {r["verdict"]: r["n"] for r in rows},
    }


def outcomes_summary(con, since: str) -> dict:
    rows = con.execute("""
        SELECT o.outcome_kind, COUNT(*) AS n,
               AVG(o.net_pnl_pct) AS avg_pnl
          FROM outcomes o
          JOIN fills f ON o.fill_id = f.id
          JOIN signals_current s ON f.signal_id = s.id
         WHERE o.outcome_date >= ?
           AND s.backtest_run_id IS NULL
         GROUP BY o.outcome_kind
    """, (since,)).fetchall()
    total = sum(r["n"] for r in rows)
    wins = sum(r["n"] for r in rows if r["outcome_kind"].startswith("WIN"))
    losses = sum(r["n"] for r in rows if r["outcome_kind"] == "LOSS_STOP")
    avg_overall = con.execute("""
        SELECT AVG(o.net_pnl_pct) FROM outcomes o
          JOIN fills f ON o.fill_id = f.id
          JOIN signals_current s ON f.signal_id = s.id
         WHERE o.outcome_date >= ?
           AND s.backtest_run_id IS NULL
    """, (since,)).fetchone()
    return {
        "total": total, "wins": wins, "losses": losses,
        "win_rate_pct": round(wins / total * 100, 1) if total else 0.0,
        "avg_pnl_pct": round((avg_overall[0] or 0.0), 3),
        "by_kind": {r["outcome_kind"]: {
            "count": r["n"], "avg_pnl_pct": round((r["avg_pnl"] or 0.0), 3)
        } for r in rows},
    }


def llm_summary(con, since: str) -> dict:
    rows = con.execute("""
        SELECT loop, status, COUNT(*) AS n,
               AVG(latency_ms) AS avg_latency,
               SUM(cost_inr) AS total_cost
          FROM reasoning_traces
         WHERE created_at >= ?
         GROUP BY loop, status
    """, (since,)).fetchall()
    by_loop: dict[str, dict] = {}
    total_cost = 0.0
    for r in rows:
        d = by_loop.setdefault(r["loop"], {
            "ok": 0, "errors": 0, "avg_latency_ms": 0, "cost_inr": 0.0,
        })
        if r["status"] == "OK":
            d["ok"] += r["n"]
        else:
            d["errors"] += r["n"]
        d["avg_latency_ms"] = max(d["avg_latency_ms"], int(r["avg_latency"] or 0))
        d["cost_inr"] += float(r["total_cost"] or 0)
        total_cost += float(r["total_cost"] or 0)
    return {"by_loop": by_loop, "total_cost_inr": round(total_cost, 2)}


def lessons_summary(con, since: str) -> dict:
    rows = con.execute("""
        SELECT classification, suggests_revision, COUNT(*) AS n
          FROM lessons
         WHERE created_at >= ?
         GROUP BY classification, suggests_revision
    """, (since,)).fetchall()
    total = sum(r["n"] for r in rows)
    flagged = sum(r["n"] for r in rows if r["suggests_revision"])
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r["classification"]] = by_class.get(r["classification"], 0) + r["n"]
    return {"total": total, "flagged_for_revision": flagged,
            "by_classification": by_class}


def revisions_summary(con, since: str) -> dict:
    rows = con.execute("""
        SELECT status, COUNT(*) AS n
          FROM revisions
         WHERE proposed_at >= ?
         GROUP BY status
    """, (since,)).fetchall()
    return {r["status"]: r["n"] for r in rows}


def anomaly_summary(con, since: str) -> dict:
    rows = con.execute("""
        SELECT severity, COUNT(*) AS n
          FROM audit
         WHERE event_at >= ?
           AND event_type LIKE '%ANOMALY%'
         GROUP BY severity
    """, (since,)).fetchall()
    return {r["severity"]: r["n"] for r in rows}


# ── Render ───────────────────────────────────────────────────────────────────

def build_report(con, days: int = 7) -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    since_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    sigs = signals_summary(con, since)
    outs = outcomes_summary(con, since)
    llm  = llm_summary(con, since_iso)
    lessons = lessons_summary(con, since_iso)
    revisions = revisions_summary(con, since_iso)
    anomalies = anomaly_summary(con, since_iso)
    health = trace_health(con)

    lines = [
        f"📒 <b>anju-AI · Audit Report</b>  "
        f"({days}d window since {since})",
        "",
        f"<b>━━━ Signals ━━━</b>",
        f"  Total generated: <b>{sigs['total']}</b>",
    ]
    for v, n in sigs["by_verdict"].items():
        lines.append(f"  {v}: <b>{n}</b>")

    lines += ["",
              f"<b>━━━ Outcomes ━━━</b>",
              f"  Closed: <b>{outs['total']}</b>  "
              f"(🟢 {outs['wins']} · 🔴 {outs['losses']})",
              f"  Win rate: <b>{outs['win_rate_pct']}%</b>",
              f"  Avg net P&L: <b>{outs['avg_pnl_pct']:+.3f}%</b>/trade"]

    if outs["by_kind"]:
        for k, d in outs["by_kind"].items():
            lines.append(f"  {k}: <b>{d['count']}</b>  avg {d['avg_pnl_pct']:+.2f}%")

    if llm["by_loop"]:
        lines += ["", f"<b>━━━ LLM Activity ━━━</b>"]
        for loop, d in llm["by_loop"].items():
            lines.append(
                f"  <b>{loop}</b>: {d['ok']} ok · {d['errors']} errors"
                f" · p95~{d['avg_latency_ms']}ms"
                f" · ₹{d['cost_inr']:.2f}"
            )
        lines.append(f"  <b>Total LLM cost ({days}d)</b>: "
                     f"<b>₹{llm['total_cost_inr']:.2f}</b>")
        ok_rate = health.get("ok_rate", 0)
        lines.append(f"  Last-24h ok rate: <b>{ok_rate}%</b>")

    lines += ["", f"<b>━━━ Lessons (Phase 3.1 LLM) ━━━</b>",
              f"  Total: <b>{lessons['total']}</b>  ·  "
              f"Flagged for revision: <b>{lessons['flagged_for_revision']}</b>"]
    for c, n in lessons["by_classification"].items():
        lines.append(f"  {c}: <b>{n}</b>")

    lines += ["", f"<b>━━━ Revisions (Phase 3.2 critic) ━━━</b>"]
    if revisions:
        for status, n in revisions.items():
            lines.append(f"  {status}: <b>{n}</b>")
    else:
        lines.append("  <i>None proposed this window</i>")

    lines += ["", f"<b>━━━ Anomalies ━━━</b>"]
    if anomalies:
        for sev, n in anomalies.items():
            emoji = "🔴" if sev == "CRITICAL" else "🟡" if sev == "WARN" else "🟢"
            lines.append(f"  {emoji} {sev}: <b>{n}</b>")
    else:
        lines.append("  ✅ <i>No anomalies in window</i>")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def run(days: int = 7, telegram: bool = True) -> int:
    print(f"\n📒 anju-AI audit_report  ({days}d)\n")
    con = init_if_needed()
    try:
        report = build_report(con, days=days)
        if telegram:
            tg_send(report)
        else:
            print(report)
    finally:
        con.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--no-telegram", action="store_true")
    args = p.parse_args()
    return run(days=args.days, telegram=not args.no_telegram)


if __name__ == "__main__":
    sys.exit(main())
