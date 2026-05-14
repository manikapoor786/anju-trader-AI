#!/usr/bin/env python3
"""
anju_ai.loops.weekly_critic — Sunday-morning strategy review (Claude).

Per AGENT_PROTOCOL §2.3 / ROADMAP 3.2. Reviews the past week's signals
+ outcomes + lessons and proposes specific revisions for Manish to
approve via Telegram (`/approve_<id>` / `/reject_<id>`).

ONLY this loop uses Claude (paid) — every other loop uses Gemini free
tier. Cost: ~₹5-10/week at projected volume.

Usage:
    python -m anju_ai.loops.weekly_critic         # send Telegram proposals
    python -m anju_ai.loops.weekly_critic --dry-run   # print, don't apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import statistics
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
from anju_ai.llm.claude import ClaudeClient
from anju_ai.llm.base import LLMResponse, OK
from anju_ai.llm.trace import log_trace


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class ExpectancyStats(BaseModel):
    trades: int
    win_rate: float
    avg_winner_pct: float
    avg_loser_pct: float
    net_expectancy_pct: float


class LessonSummary(BaseModel):
    lesson_id: int
    classification: str
    primary_factor: str
    lesson: str


class WeeklyCriticInput(BaseModel):
    week:               str
    signals_count:      int
    outcomes_count:     int
    headline_stats:     ExpectancyStats
    expectancy_by_score_bucket: dict[str, ExpectancyStats]
    expectancy_by_regime:       dict[str, ExpectancyStats]
    expectancy_by_entry_model:  dict[str, ExpectancyStats]
    recent_lessons:     list[LessonSummary]
    flagged_lessons:    list[LessonSummary]   # suggests_revision=true
    recent_approved_revisions: list[dict]


class RevisionProposal(BaseModel):
    kind:              Literal["PARAMETER", "WEIGHT", "FILTER", "NEW_RULE"]
    target:            str
    current_value:     str
    proposed_value:    str
    rationale:         str
    expected_impact:   str
    confidence:        float = Field(ge=0.0, le=1.0)
    backtest_required: bool


class WeeklyCriticOutput(BaseModel):
    summary:   str
    flags:     list[str] = Field(default_factory=list)
    proposals: list[RevisionProposal] = Field(default_factory=list,
                                               max_length=5)


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


# ── Snapshot collectors ──────────────────────────────────────────────────────

def _bucket(score: float) -> str:
    lo = int(score // 5) * 5
    return f"{lo:02d}-{lo+4:02d}"


def collect_input(con, week: str | None = None) -> WeeklyCriticInput:
    """Build the input from memory.db. Default window: last 7 days."""
    if week is None:
        week = datetime.now().strftime("%Y-W%W")
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    # Headline counts
    signals_count = con.execute(
        "SELECT COUNT(*) FROM signals_current "
        "WHERE signal_date >= ? AND backtest_run_id IS NULL", (since,)
    ).fetchone()[0]

    outcomes = con.execute("""
        SELECT s.final_score AS score, r.state AS regime,
               o.outcome_kind, o.net_pnl_pct, s.verdict, s.backtest_run_id
          FROM outcomes o
          JOIN fills f ON o.fill_id = f.id
          JOIN signals_current s ON f.signal_id = s.id
          LEFT JOIN regime_snapshots r ON s.regime_id = r.id
         WHERE o.outcome_date >= ?
           AND s.backtest_run_id IS NULL
    """, (since,)).fetchall()
    closed = [dict(r) for r in outcomes]

    headline = _stats_for_trades(closed)
    by_bucket = _group_stats(closed, lambda t: _bucket(t["score"] or 0))
    by_regime = _group_stats(closed, lambda t: t["regime"] or "Unknown")
    # Entry model isn't on signals_current schema yet — use score band as proxy
    by_model = _group_stats(closed, lambda t: "score_" + _bucket(t["score"] or 0))

    # Lessons
    lesson_rows = con.execute("""
        SELECT id, classification, primary_factor, lesson, suggests_revision
          FROM lessons
         WHERE created_at >= datetime('now', '-7 days')
         ORDER BY id DESC LIMIT 30
    """).fetchall()
    recent = [LessonSummary(lesson_id=r["id"], classification=r["classification"],
                             primary_factor=r["primary_factor"],
                             lesson=r["lesson"])
              for r in lesson_rows]
    flagged = [l for l, r in zip(recent, lesson_rows) if r["suggests_revision"]]

    # Recent revisions (last 30 days)
    rev_rows = con.execute("""
        SELECT kind, target, current_value, proposed_value, status,
               decided_at, decided_by
          FROM revisions
         WHERE proposed_at >= datetime('now', '-30 days')
         ORDER BY id DESC LIMIT 20
    """).fetchall()

    return WeeklyCriticInput(
        week=week,
        signals_count=signals_count,
        outcomes_count=len(closed),
        headline_stats=headline,
        expectancy_by_score_bucket=by_bucket,
        expectancy_by_regime=by_regime,
        expectancy_by_entry_model=by_model,
        recent_lessons=recent[:15],
        flagged_lessons=flagged[:10],
        recent_approved_revisions=[dict(r) for r in rev_rows],
    )


def _stats_for_trades(trades: list[dict]) -> ExpectancyStats:
    if not trades:
        return ExpectancyStats(trades=0, win_rate=0, avg_winner_pct=0,
                                avg_loser_pct=0, net_expectancy_pct=0)
    wins = [t for t in trades if t["outcome_kind"].startswith("WIN")]
    losses = [t for t in trades if t["outcome_kind"] == "LOSS_STOP"]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_w = statistics.mean(t["net_pnl_pct"] for t in wins) if wins else 0.0
    avg_l = statistics.mean(t["net_pnl_pct"] for t in losses) if losses else 0.0
    exp = statistics.mean(t["net_pnl_pct"] for t in trades)
    return ExpectancyStats(
        trades=len(trades), win_rate=round(win_rate, 1),
        avg_winner_pct=round(avg_w, 3), avg_loser_pct=round(avg_l, 3),
        net_expectancy_pct=round(exp, 3),
    )


def _group_stats(trades: list[dict], key_fn) -> dict[str, ExpectancyStats]:
    grouped: dict[str, list[dict]] = {}
    for t in trades:
        k = key_fn(t)
        grouped.setdefault(k, []).append(t)
    return {k: _stats_for_trades(v) for k, v in grouped.items()}


# ── Prompt + render ──────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"


def _load_prompt(name: str, version: int) -> str:
    text = (_PROMPTS_DIR / f"{name}.v{version}.md").read_text()
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    return text.strip()


def _render(inp: WeeklyCriticInput) -> str:
    lines = [f"## WEEK {inp.week}",
             f"Signals: {inp.signals_count}  ·  Outcomes: {inp.outcomes_count}"]

    lines.append(f"\n## HEADLINE")
    lines.append(json.dumps(inp.headline_stats.model_dump(), indent=2))

    if inp.expectancy_by_score_bucket:
        lines.append(f"\n## BY SCORE BUCKET")
        lines.append(json.dumps(
            {k: v.model_dump() for k, v in inp.expectancy_by_score_bucket.items()},
            indent=2))

    if inp.expectancy_by_regime:
        lines.append(f"\n## BY REGIME")
        lines.append(json.dumps(
            {k: v.model_dump() for k, v in inp.expectancy_by_regime.items()},
            indent=2))

    if inp.recent_lessons:
        lines.append(f"\n## LESSONS THIS WEEK ({len(inp.recent_lessons)})")
        for l in inp.recent_lessons:
            lines.append(f"- [{l.lesson_id}] [{l.classification}] "
                         f"{l.primary_factor}: {l.lesson}")

    if inp.flagged_lessons:
        lines.append(f"\n## LESSONS FLAGGED FOR REVISION ({len(inp.flagged_lessons)})")
        for l in inp.flagged_lessons:
            lines.append(f"- [{l.lesson_id}] {l.primary_factor}: {l.lesson}")

    if inp.recent_approved_revisions:
        lines.append(f"\n## RECENT REVISIONS (last 30d)")
        for r in inp.recent_approved_revisions[:10]:
            lines.append(
                f"- [{r.get('status', '?')}] {r.get('kind', '?')} "
                f"{r.get('target', '')}: "
                f"{r.get('current_value', '')} → {r.get('proposed_value', '')}"
            )

    return "\n".join(lines)


def review_week(inp: WeeklyCriticInput, client=None,
                prompt_version: int = 1) -> LLMResponse:
    if client is None:
        client = ClaudeClient()
    prompt = _load_prompt("weekly_critic", prompt_version) + "\n\n" + _render(inp)
    return client.complete(
        prompt=prompt, schema=WeeklyCriticOutput,
        model="claude-sonnet-4-6",
        prompt_name="weekly_critic", prompt_version=prompt_version,
        max_tokens_in=8000, max_tokens_out=2000,
        temperature=0.2, timeout_s=60.0,
    )


# ── Persist revisions ────────────────────────────────────────────────────────

def save_revisions(con, output: WeeklyCriticOutput, week: str,
                   reasoning_trace_id: int) -> list[int]:
    ids: list[int] = []
    for p in output.proposals:
        cur = con.execute("""
            INSERT INTO revisions
                (proposed_at, week, kind, target, current_value, proposed_value,
                 rationale, expected_impact, confidence, backtest_required,
                 status, reasoning_trace_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(), week,
            p.kind, p.target, p.current_value, p.proposed_value,
            p.rationale, p.expected_impact, p.confidence,
            1 if p.backtest_required else 0,
            "AWAITING_APPROVAL" if not p.backtest_required else "BACKTESTING",
            reasoning_trace_id,
        ))
        ids.append(cur.lastrowid or 0)
    return ids


def render_telegram(output: WeeklyCriticOutput, proposal_ids: list[int]) -> str:
    lines = [f"📋 <b>anju-AI · Weekly Critic</b>"]
    lines.append(f"\n<i>{output.summary}</i>")

    if output.flags:
        lines.append(f"\n<b>Flags</b>: {' · '.join(output.flags)}")

    if not output.proposals:
        lines.append("\n<i>No revisions proposed this week.</i>")
        return "\n".join(lines)

    for pid, p in zip(proposal_ids, output.proposals):
        lines.append(f"\n━━━ <b>Proposal #{pid}</b> ({p.kind}, conf {p.confidence:.0%}) ━━━")
        lines.append(f"<b>Target</b>: <code>{p.target}</code>")
        lines.append(f"  <code>{p.current_value}</code> → <code>{p.proposed_value}</code>")
        lines.append(f"<b>Rationale</b>: <i>{p.rationale}</i>")
        lines.append(f"<b>Expected impact</b>: {p.expected_impact}")
        if p.backtest_required:
            lines.append(f"  ⏳ <i>Backtest required before applying</i>")
        lines.append(f"\n  Reply <code>/approve_{pid}</code> or <code>/reject_{pid}</code>")
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> int:
    print(f"\n📋 anju-AI weekly_critic  {datetime.now().strftime('%a %d %b %Y')}\n")
    con = init_if_needed()
    try:
        inp = collect_input(con)
        if inp.outcomes_count < 5:
            tg_send(f"📋 <b>Weekly Critic</b>\n"
                    f"<i>Insufficient data ({inp.outcomes_count} closed trades). "
                    f"Need ≥5 to propose meaningful revisions. Skipping.</i>")
            return 0

        if dry_run:
            print("Dry run — input rendered:")
            print(_render(inp)[:2000])
            return 0

        resp = review_week(inp)
        tid = log_trace(con, "weekly_critic", resp,
                        input_payload=inp.model_dump())

        if resp.status != OK or resp.parsed is None:
            tg_send(f"❌ <b>Weekly Critic failed</b>: {resp.status} — "
                    f"{resp.error_message[:200]}")
            audit_log(con, "WEEKLY_CRITIC_FAILED",
                      f"{resp.status}: {resp.error_message}", severity="WARN")
            return 1

        ids = save_revisions(con, resp.parsed, inp.week, tid)
        audit_log(con, "WEEKLY_CRITIC",
                  f"week={inp.week} proposals={len(ids)}")
        tg_send(render_telegram(resp.parsed, ids))
    finally:
        con.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
