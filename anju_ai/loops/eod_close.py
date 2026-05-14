#!/usr/bin/env python3
"""
anju_ai.loops.eod_close — daily 4 PM IST outcome closure.

After market close, walk every open fill forward through the new day's
bhavcopy data. First-touch detection of stop / T1 / T2 marks WIN_* /
LOSS_STOP. Time exit after max_hold_days. Outcomes persist to memory.db.

Output: structured Telegram summary of what closed today, with P&L.

Usage:
    python -m anju_ai.loops.eod_close                 # close + telegram
    python -m anju_ai.loops.eod_close --no-telegram   # silent
    python -m anju_ai.loops.eod_close --max-hold 90   # tune hold ceiling
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from anju_core import get_ohlcv, refresh_daily
from anju_ai.memory.db import audit_log, init_if_needed
from anju_ai.tools.outcome_tracker import close_open_outcomes


def tg_send(text: str) -> bool:
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


def render_report(stats: dict, just_closed: list) -> str:
    now = datetime.now().strftime("%d %b %Y %H:%M")
    if not just_closed:
        return (
            f"📊 <b>anju-AI · EOD Close</b>\n"
            f"{now} IST · Scanned: <b>{stats['scanned']}</b> open · "
            f"Closed: <b>{stats['closed']}</b> · "
            f"Still open: <b>{stats['still_open']}</b>\n\n"
            f"<i>No fills hit stop/target/time-exit today.</i>"
        )

    wins   = [c for c in just_closed if c["kind"].startswith("WIN")]
    losses = [c for c in just_closed if c["kind"] == "LOSS_STOP"]
    times  = [c for c in just_closed if c["kind"] == "TIME_EXIT"]

    lines = [
        f"📊 <b>anju-AI · EOD Close</b>",
        f"{now} IST · Scanned: <b>{stats['scanned']}</b> · "
        f"Closed: <b>{stats['closed']}</b>  "
        f"(🟢 {len(wins)} · 🔴 {len(losses)} · ⏳ {len(times)})",
    ]
    for c in just_closed[:15]:
        emoji = "🟢" if c["kind"].startswith("WIN") else ("🔴" if c["kind"] == "LOSS_STOP" else "⏳")
        pnl_str = f"{c['pnl_pct']:+.1f}%"
        lines.append(
            f"\n{emoji} <b>{c['symbol']}</b>  {c['kind']}  "
            f"₹{c['entry']:.2f}→₹{c['exit']:.2f}  {pnl_str}  ({c['days']}d)"
        )
    if len(just_closed) > 15:
        lines.append(f"\n<i>+ {len(just_closed) - 15} more — see memory.db</i>")
    return "\n".join(lines)


def collect_just_closed(con, before_count: int) -> list:
    """Read the rows we just inserted to render a friendly report."""
    rows = con.execute("""
        SELECT o.outcome_kind, o.exit_price, o.days_held, o.net_pnl_pct,
               f.fill_price, s.symbol
        FROM outcomes o
        JOIN fills f ON o.fill_id = f.id
        JOIN signals_current s ON f.signal_id = s.id
        ORDER BY o.id DESC
        LIMIT ?
    """, (max(0, con.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] - before_count),)).fetchall()
    return [
        {
            "symbol": r["symbol"], "kind": r["outcome_kind"],
            "entry": r["fill_price"], "exit": r["exit_price"],
            "days": r["days_held"], "pnl_pct": r["net_pnl_pct"],
        }
        for r in rows
    ]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-hold", type=int, default=90,
                   help="Force TIME_EXIT after this many trading days")
    p.add_argument("--no-telegram", action="store_true")
    p.add_argument("--refresh-data", action="store_true",
                   help="Refresh bhavcopy before closing")
    args = p.parse_args()

    print(f"\n📊 anju-AI eod_close  {datetime.now().strftime('%H:%M IST')}\n")

    if args.refresh_data:
        print("Refreshing historical data...")
        try:
            refresh_daily(days_back=5, verbose=False)
        except Exception as e:
            print(f"  ⚠️  Refresh failed (continuing): {e}")

    con = init_if_needed()
    try:
        before = con.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
        stats = close_open_outcomes(con, get_ohlcv,
                                    max_hold_days=args.max_hold)
        just_closed = collect_just_closed(con, before)
        audit_log(con, "EOD_CLOSE",
                  f"Closed {stats['closed']}/{stats['scanned']} open fills "
                  f"({len(just_closed)} new outcomes)")
        print(f"  Scanned {stats['scanned']} · Closed {stats['closed']} · "
              f"Still open {stats['still_open']}")
    finally:
        con.close()

    if not args.no_telegram:
        tg_send(render_report(stats, just_closed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
