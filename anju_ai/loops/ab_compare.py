#!/usr/bin/env python3
"""
anju_ai.loops.ab_compare — anju-trader vs anju-trader-AI head-to-head report.

Reads recent signal-outcome data from memory.db (anju-trader-AI) and from a
read-only mount of anju-trader's signals.db (when wired up in Phase 1) and
sends a Telegram comparison report:

  - signal counts per system in the period
  - win rate per system
  - expectancy per trade (cost-adjusted in Phase 1)
  - the symbols each system flagged that the other missed

Phase 0 is a STUB — outputs a placeholder Telegram message so the workflow
is discoverable on the phone. Real implementation lands after Phase 1 when:
  - outcomes are event-driven (so wins/losses are real, not 10-day-late)
  - costs are subtracted (so expectancy is honest)

Usage:
    python -m anju_ai.loops.ab_compare --days 30
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
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


def _tg_send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print(text)
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    return r.ok


def count_signals_last_n_days(days: int) -> dict:
    """Read anju-trader-AI's memory.db for signals in the last N days."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    con = init_if_needed()
    try:
        total = con.execute(
            "SELECT COUNT(*) FROM signals_current WHERE signal_date >= ?",
            (cutoff,),
        ).fetchone()[0]
        by_verdict = {
            row["verdict"]: row["cnt"] for row in con.execute(
                "SELECT verdict, COUNT(*) as cnt FROM signals_current "
                "WHERE signal_date >= ? GROUP BY verdict", (cutoff,)
            ).fetchall()
        }
        outcomes_total = con.execute(
            """SELECT COUNT(*) FROM outcomes o
                  JOIN fills f ON o.fill_id = f.id
                  JOIN signals_current s ON f.signal_id = s.id
                 WHERE s.signal_date >= ?""", (cutoff,)
        ).fetchone()[0]
        wins = con.execute(
            """SELECT COUNT(*) FROM outcomes o
                  JOIN fills f ON o.fill_id = f.id
                  JOIN signals_current s ON f.signal_id = s.id
                 WHERE s.signal_date >= ?
                   AND o.outcome_kind LIKE 'WIN%'""", (cutoff,)
        ).fetchone()[0]
        win_rate = (wins / outcomes_total * 100) if outcomes_total > 0 else None
    finally:
        con.close()
    return {
        "days": days, "since": cutoff, "total_signals": total,
        "by_verdict": by_verdict, "outcomes": outcomes_total,
        "wins": wins, "win_rate": win_rate,
    }


def render_report(stats: dict) -> str:
    bv = stats["by_verdict"]
    bv_str = "  ".join(f"{k}: <b>{v}</b>" for k, v in bv.items()) or "—"
    win_rate_str = (f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None
                    else "<i>no closed outcomes yet</i>")

    return (
        f"📊 <b>A/B Report · anju-AI vs anju-trader</b>\n"
        f"Period: last <b>{stats['days']}</b> days (since {stats['since']})\n\n"
        f"<b>anju-AI (paper)</b>\n"
        f"  Signals: <b>{stats['total_signals']}</b>\n"
        f"  Breakdown: {bv_str}\n"
        f"  Closed outcomes: <b>{stats['outcomes']}</b>  ·  "
        f"Wins: <b>{stats['wins']}</b>  ·  Win rate: {win_rate_str}\n\n"
        f"<b>anju-trader (live)</b>\n"
        f"  <i>Phase 1 stub — will read anju-trader/signals.db via "
        f"a shared volume or sync job in task 1.8.</i>\n\n"
        f"<i>Phase 0 v0 report. Real cost-adjusted expectancy lands once "
        f"outcomes are event-driven (task 1.1) and costs are modelled "
        f"(task 1.2). Decision rule for cutover: see ROADMAP §Phase 4.</i>"
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    args = p.parse_args()

    stats = count_signals_last_n_days(args.days)
    text  = render_report(stats)
    ok = _tg_send(text)
    if not ok:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
