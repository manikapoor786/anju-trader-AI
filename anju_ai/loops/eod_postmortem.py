#!/usr/bin/env python3
"""
anju_ai.loops.eod_postmortem — LLM writes a structured lesson after each
closed trade.

Per AGENT_PROTOCOL §2.2 / ROADMAP 3.1. After eod_close.py marks trades
as outcomes today, this loop reviews each new closed trade with
Gemini Flash and persists a lesson to memory.lessons.

The lessons get used by:
  - Phase 3.2 weekly_critic to identify recurring patterns
  - Phase 3.5 deep_review to inject "similar past trades" context
  - Future-you reading the audit log to understand what was learned

Runs after eod_close.py in the same workflow (or independently triggered).

Usage:
    python -m anju_ai.loops.eod_postmortem           # run on today's closes
    python -m anju_ai.loops.eod_postmortem --limit 5 # process only 5 most recent
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

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
from anju_ai.llm.trace import log_trace


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


# ── Lesson agent — uses anju_ai.tools.post_mortem ────────────────────────────

# Inline schemas because the loop is the entry point
from typing import Literal
from pydantic import BaseModel, Field


class PostMortemSignalContext(BaseModel):
    symbol:      str
    score:       float
    verdict:     str
    entry_model: str
    rule_score:  float = 0.0
    regime:      str = ""
    tags:        list[str] = Field(default_factory=list)


class PostMortemFillContext(BaseModel):
    fill_date:   str
    fill_price:  float
    qty:         int


class PostMortemOutcomeContext(BaseModel):
    outcome_kind:  str       # WIN_T1 | WIN_T2 | LOSS_STOP | TIME_EXIT
    exit_date:     str
    exit_price:    float
    days_held:     int
    net_pnl_pct:   float
    mfe_pct:       float
    mae_pct:       float


class SimilarTrade(BaseModel):
    lesson_id:      int
    classification: str
    primary_factor: str
    lesson:         str


class PostMortemInput(BaseModel):
    signal:  PostMortemSignalContext
    fill:    PostMortemFillContext
    outcome: PostMortemOutcomeContext
    similar_past_trades: list[SimilarTrade] = Field(default_factory=list)


class PostMortemOutput(BaseModel):
    classification:    Literal[
        "EDGE_WORKING", "EDGE_BROKEN", "BAD_LUCK",
        "BAD_EXECUTION", "WRONG_REGIME", "BLACK_SWAN",
    ]
    primary_factor:    str
    lesson:            str
    similar_pattern_id: int | None = None
    suggests_revision:  bool
    revision_hint:     str | None = None


# ── Prompt loader (mirrors catalyst.py) ──────────────────────────────────────

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"


def _load_prompt(name: str, version: int) -> str:
    path = _PROMPTS_DIR / f"{name}.v{version}.md"
    text = path.read_text()
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    return text.strip()


def _render(inp: PostMortemInput) -> str:
    s, f, o = inp.signal, inp.fill, inp.outcome
    lines = [
        f"## SIGNAL CONTEXT",
        f"Symbol: {s.symbol}  ·  Score: {s.score:.1f}  ·  Verdict: {s.verdict}",
        f"Entry model: {s.entry_model or '—'}  ·  Regime: {s.regime or '—'}",
        f"Tags: {', '.join(s.tags[:6]) if s.tags else '—'}",
        f"\n## FILL",
        f"Filled at ₹{f.fill_price:.2f}  ·  qty {f.qty}  ·  date {f.fill_date}",
        f"\n## OUTCOME",
        f"Kind: {o.outcome_kind}  ·  Exit ₹{o.exit_price:.2f}  on {o.exit_date}",
        f"Days held: {o.days_held}  ·  Net P&L: {o.net_pnl_pct:+.2f}%",
        f"MFE: {o.mfe_pct:+.2f}%  ·  MAE: {o.mae_pct:+.2f}%",
    ]
    if inp.similar_past_trades:
        lines.append(f"\n## SIMILAR PAST TRADES ({len(inp.similar_past_trades)})")
        for t in inp.similar_past_trades[:5]:
            lines.append(f"  [{t.lesson_id}] {t.classification}: "
                         f"{t.primary_factor} — {t.lesson}")
    return "\n".join(lines)


def review_outcome(inp: PostMortemInput, client=None,
                   prompt_version: int = 1) -> LLMResponse:
    if client is None:
        client = GeminiClient()
    prompt = _load_prompt("post_mortem", prompt_version) + "\n\n" + _render(inp)
    return client.complete(
        prompt=prompt, schema=PostMortemOutput,
        model="gemini-1.5-flash",
        prompt_name="post_mortem", prompt_version=prompt_version,
        max_tokens_in=2500, max_tokens_out=500,
        temperature=0.3, timeout_s=30.0,
    )


# ── DB helpers ───────────────────────────────────────────────────────────────

def find_recent_closed_outcomes(con, limit: int = 20,
                                  since: str | None = None) -> list[dict]:
    """Find outcomes without a lesson row yet."""
    if since is None:
        since = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = con.execute("""
        SELECT o.id AS outcome_id, o.outcome_kind, o.exit_price,
               o.outcome_date AS exit_date,
               o.days_held, o.net_pnl_pct,
               o.max_favourable_excursion AS mfe_pct,
               o.max_adverse_excursion    AS mae_pct,
               f.id AS fill_id, f.fill_price, f.fill_qty, f.fill_date,
               s.symbol, s.final_score AS score, s.rule_score, s.verdict,
               s.breakdown_json, r.state AS regime
        FROM outcomes o
        JOIN fills f ON o.fill_id = f.id
        JOIN signals_current s ON f.signal_id = s.id
        LEFT JOIN regime_snapshots r ON s.regime_id = r.id
        WHERE NOT EXISTS (SELECT 1 FROM lessons l WHERE l.outcome_id = o.id)
          AND o.outcome_date >= ?
        ORDER BY o.id DESC
        LIMIT ?
    """, (since, limit)).fetchall()
    return [dict(r) for r in rows]


def find_similar_past_lessons(con, signal_score: float, outcome_kind: str,
                              limit: int = 5) -> list[SimilarTrade]:
    """Heuristic: pull past lessons with similar score band AND same kind."""
    score_lo = signal_score - 5
    score_hi = signal_score + 5
    rows = con.execute("""
        SELECT l.id, l.classification, l.primary_factor, l.lesson
          FROM lessons l
          JOIN outcomes o ON l.outcome_id = o.id
          JOIN fills f    ON o.fill_id = f.id
          JOIN signals_current s ON f.signal_id = s.id
         WHERE s.final_score BETWEEN ? AND ?
           AND o.outcome_kind = ?
         ORDER BY l.id DESC
         LIMIT ?
    """, (score_lo, score_hi, outcome_kind, limit)).fetchall()
    return [SimilarTrade(lesson_id=r["id"], classification=r["classification"],
                          primary_factor=r["primary_factor"], lesson=r["lesson"])
            for r in rows]


def save_lesson(con, outcome_id: int, output: PostMortemOutput,
                reasoning_trace_id: int) -> int:
    """Insert lesson row + return its id."""
    cur = con.execute("""
        INSERT INTO lessons
            (outcome_id, classification, primary_factor, lesson,
             similar_pattern_id, suggests_revision, revision_hint,
             reasoning_trace_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        outcome_id, output.classification, output.primary_factor,
        output.lesson, output.similar_pattern_id,
        1 if output.suggests_revision else 0,
        output.revision_hint,
        reasoning_trace_id,
    ))
    return cur.lastrowid or 0


# ── Main loop ────────────────────────────────────────────────────────────────

def run(limit: int = 20, dry_run: bool = False) -> int:
    print(f"\n📝 anju-AI eod_postmortem  {datetime.now().strftime('%H:%M IST')}\n")
    con = init_if_needed()
    try:
        rows = find_recent_closed_outcomes(con, limit=limit)
        if not rows:
            print("No new closed outcomes to post-mortem")
            return 0
        print(f"Reviewing {len(rows)} closed trades...")

        client = GeminiClient()
        new_lessons = 0
        revisions_suggested = 0

        for r in rows:
            inp = PostMortemInput(
                signal=PostMortemSignalContext(
                    symbol=r["symbol"], score=r["score"] or 0.0,
                    verdict=r["verdict"] or "",
                    entry_model="", rule_score=r["rule_score"] or 0.0,
                    regime=r["regime"] or "",
                    tags=list(json.loads(r["breakdown_json"] or "{}").keys())[:6],
                ),
                fill=PostMortemFillContext(
                    fill_date=r["fill_date"] or "",
                    fill_price=r["fill_price"] or 0.0,
                    qty=r["fill_qty"] or 0,
                ),
                outcome=PostMortemOutcomeContext(
                    outcome_kind=r["outcome_kind"],
                    exit_date=r["exit_date"] or "",
                    exit_price=r["exit_price"] or 0.0,
                    days_held=r["days_held"] or 0,
                    net_pnl_pct=r["net_pnl_pct"] or 0.0,
                    mfe_pct=r["mfe_pct"] or 0.0,
                    mae_pct=r["mae_pct"] or 0.0,
                ),
                similar_past_trades=find_similar_past_lessons(
                    con, r["score"] or 0.0, r["outcome_kind"]),
            )

            response = review_outcome(inp, client=client) if not dry_run else None
            if response is None or response.status != OK or response.parsed is None:
                if response:
                    print(f"  ❌ {r['symbol']}: {response.status} — {response.error_message}")
                    log_trace(con, "post_mortem", response,
                              input_payload=inp.model_dump())
                continue

            tid = log_trace(con, "post_mortem", response,
                            input_payload=inp.model_dump(),
                            linked_outcome_id=r["outcome_id"])
            lid = save_lesson(con, r["outcome_id"], response.parsed, tid)
            new_lessons += 1
            if response.parsed.suggests_revision:
                revisions_suggested += 1
            print(f"  ✅ {r['symbol']} [{response.parsed.classification}] "
                  f"#{lid}: {response.parsed.primary_factor}")

        audit_log(con, "POST_MORTEM_BATCH",
                  f"reviewed {len(rows)}, wrote {new_lessons} lessons, "
                  f"{revisions_suggested} flagged for weekly critic")

        if new_lessons:
            tg_send(
                f"📝 <b>anju-AI · Post-Mortem</b>\n"
                f"Reviewed <b>{len(rows)}</b> closed trades · "
                f"wrote <b>{new_lessons}</b> lessons · "
                f"<b>{revisions_suggested}</b> flagged for weekly critic"
            )
    finally:
        con.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    return run(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
