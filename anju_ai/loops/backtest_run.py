#!/usr/bin/env python3
"""
anju_ai.loops.backtest_run — CLI / workflow entrypoint for the backtest engine.

Runs anju_ai.tools.backtest.run_backtest against historical.db, persists
the run + every signal/fill/outcome to memory.db namespaced by run_id,
and sends a structured Telegram report.

Usage (CLI):
    python -m anju_ai.loops.backtest_run \
        --start 2024-05-01 --end 2026-05-01 \
        --universe nifty100 --mode strict --min-score 6

Usage (workflow): triggered from backtest.yml manual dispatch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
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

from anju_core import get_index, get_ohlcv
from anju_core.universe import get_universe
from anju_ai.memory.db import audit_log, init_if_needed
from anju_ai.tools.backtest import BacktestInput, BacktestReport, run_backtest


# ── Telegram ──────────────────────────────────────────────────────────────────

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")


def tg_send(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        print(text)
        return False
    try:
        for chunk in _chunks(text, 4000):
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": chunk,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=20,
            )
            if not r.ok:
                print(f"TG error: {r.status_code} {r.text[:200]}")
                return False
        return True
    except Exception as e:
        print(f"TG send failed: {e}")
        return False


def _chunks(text: str, limit: int) -> list[str]:
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


# ── Persistence ───────────────────────────────────────────────────────────────

def _create_run(con, inp: BacktestInput) -> int:
    cur = con.execute("""
        INSERT INTO backtest_runs (name, start_date, end_date, universe,
                                   mode, capital_inr, config_json, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING')
    """, (
        inp.name, inp.start_date, inp.end_date,
        f"custom({len(inp.universe_symbols)})", inp.mode, inp.capital_inr,
        json.dumps(inp.model_dump()),
    ))
    return cur.lastrowid


def _finalize_run(con, run_id: int, report: BacktestReport) -> None:
    con.execute("""
        UPDATE backtest_runs
        SET status='COMPLETED', completed_at=datetime('now', '+05:30'),
            summary_json=?
        WHERE id=?
    """, (json.dumps(report.model_dump()), run_id))


def _fail_run(con, run_id: int, error: str) -> None:
    con.execute("""
        UPDATE backtest_runs
        SET status='FAILED', completed_at=datetime('now', '+05:30'),
            error_message=?
        WHERE id=?
    """, (error[:2000], run_id))


# ── Report rendering ──────────────────────────────────────────────────────────

def render_report(report: BacktestReport) -> str:
    """Build the HTML Telegram report."""
    cfg = report.config
    verdict_line = _verdict(report)

    lines = [
        f"📊 <b>anju-AI · Backtest Report</b>",
        f"<b>{report.name}</b>  ·  {report.start_date} → {report.end_date}",
        f"Universe: <b>{report.universe_size}</b> stocks  ·  "
        f"Days: <b>{report.days_scanned}</b>  ·  "
        f"Mode: <code>{cfg.get('mode')}</code>  ·  "
        f"Min score: <b>{cfg.get('min_score')}</b>",
        "",
        f"<b>━━━ Headline ━━━</b>",
        f"  Total closed trades: <b>{report.total_closed}</b>  "
        f"(still open: {report.total_open})",
        f"  Win rate: <b>{report.win_rate_pct}%</b>",
        f"  Avg winner: <b>+{report.avg_winner_pct:.2f}%</b>  ·  "
        f"Avg loser: <b>{report.avg_loser_pct:.2f}%</b>",
        f"  Realised R:R: <b>{report.rr_realized}x</b>",
        f"  Gross expectancy: <b>{report.gross_expectancy_pct:+.3f}%</b>/trade",
        f"  <b>NET expectancy: {report.net_expectancy_pct:+.3f}%/trade</b>",
        f"  Net ₹/trade @ max position: <b>₹{int(report.expectancy_inr_per_trade):,}</b>",
        "",
        f"<b>━━━ Equity ━━━</b>",
        f"  Cumulative return (compounded): <b>{report.final_equity_pct:+.1f}%</b>",
        f"  Max drawdown: <b>{report.max_drawdown_pct:.1f}%</b>",
        "",
        f"<b>━━━ Outcome mix ━━━</b>",
    ]
    for kind, n in sorted(report.by_outcome_kind.items()):
        emoji = "🟢" if kind.startswith("WIN") else ("🔴" if kind == "LOSS_STOP" else "⏳")
        pct = round(n / report.total_closed * 100, 1) if report.total_closed else 0
        lines.append(f"  {emoji} {kind}: <b>{n}</b> ({pct}%)")

    if report.by_score_bucket:
        lines += ["", f"<b>━━━ By score bucket ━━━</b>"]
        for b, v in report.by_score_bucket.items():
            lines.append(
                f"  <code>{b}</code>  trades=<b>{v['trades']}</b>  "
                f"win=<b>{v['win_rate_pct']}%</b>  "
                f"net=<b>{v['net_expectancy_pct']:+.3f}%</b>"
            )

    if report.by_entry_model:
        lines += ["", f"<b>━━━ By entry model ━━━</b>"]
        for em, v in sorted(report.by_entry_model.items(),
                            key=lambda x: -x[1]["net_expectancy_pct"]):
            lines.append(
                f"  {em or '—'}  trades=<b>{v['trades']}</b>  "
                f"win=<b>{v['win_rate_pct']}%</b>  "
                f"net=<b>{v['net_expectancy_pct']:+.3f}%</b>"
            )

    if report.best_trades:
        lines += ["", f"<b>━━━ Best 3 trades ━━━</b>"]
        for t in report.best_trades[:3]:
            lines.append(
                f"  🟢 <b>{t.symbol}</b>  +{t.net_pnl_pct:.1f}%  "
                f"score {t.score:.0f}  {t.days_held}d  "
                f"<i>{t.entry_model or '—'}</i>"
            )

    if report.worst_trades:
        lines += [f"<b>━━━ Worst 3 trades ━━━</b>"]
        for t in report.worst_trades[:3]:
            lines.append(
                f"  🔴 <b>{t.symbol}</b>  {t.net_pnl_pct:.1f}%  "
                f"score {t.score:.0f}  {t.days_held}d  "
                f"<i>{t.entry_model or '—'}</i>"
            )

    lines += ["", verdict_line]
    return "\n".join(lines)


def _verdict(r: BacktestReport) -> str:
    """One-liner honest verdict based on the numbers."""
    if r.total_closed < 30:
        return ("<i>⚠️ Verdict: too few trades to conclude. Need ≥30 closed trades "
                "for statistical relevance.</i>")
    exp = r.net_expectancy_pct
    dd = r.max_drawdown_pct
    if exp <= 0:
        return (f"<i>❌ Verdict: edge does NOT survive costs. "
                f"Net expectancy {exp:+.3f}%/trade is negative — "
                f"current scoring loses money after brokerage + slippage. "
                f"DO NOT deploy live capital. Phase 1 task 1.5 must cut "
                f"the negative score buckets.</i>")
    if exp < 0.3:
        return (f"<i>⚠️ Verdict: marginal edge ({exp:+.3f}%/trade). "
                f"Likely not robust enough for live deployment at size. "
                f"Investigate which score buckets are negative-expectancy "
                f"and trim.</i>")
    if dd < -25:
        return (f"<i>⚠️ Verdict: positive edge ({exp:+.3f}%/trade) but "
                f"drawdown {dd:.1f}% is severe. Position sizing needs "
                f"to scale down in high-vol regimes.</i>")
    return (f"<i>✅ Verdict: edge survives costs ({exp:+.3f}%/trade) with "
            f"manageable drawdown ({dd:.1f}%). Promising baseline. "
            f"Phase 2+ adds flows + catalyst LLM + F&O leverage to "
            f"push this higher.</i>")


# ── Progress reporter ─────────────────────────────────────────────────────────

class TelegramProgress:
    """Sends ping updates to Telegram every N days during long backtests."""

    def __init__(self, every_n_days: int = 50):
        self.every = every_n_days
        self.last_ping = 0

    def __call__(self, current: int, total: int, message: str) -> None:
        if current == 0 or (current - self.last_ping >= self.every) or current >= total - 1:
            pct = round(current / max(total, 1) * 100, 0)
            tg_send(f"⏱ <b>Backtest progress</b> {pct:.0f}%  ·  {message}")
            self.last_ping = current


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--universe", default="nifty100",
                   help="Named universe (nifty50/nifty100/nifty180/...)")
    p.add_argument("--mode", default="strict", choices=["strict", "aggressive"])
    p.add_argument("--min-score", type=float, default=6.0)
    p.add_argument("--max-open", type=int, default=15)
    p.add_argument("--max-hold", type=int, default=90)
    p.add_argument("--capital", type=float, default=17_500_000)
    p.add_argument("--name", default=None, help="Run name (defaults to timestamp)")
    p.add_argument("--no-telegram", action="store_true")
    p.add_argument("--no-progress", action="store_true")
    args = p.parse_args()

    name = args.name or f"bt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.universe}_{args.mode}"

    symbols = get_universe(args.universe)
    inp = BacktestInput(
        name=name, start_date=args.start, end_date=args.end,
        universe_symbols=symbols, mode=args.mode, min_score=args.min_score,
        max_open_positions=args.max_open, capital_inr=args.capital,
        max_hold_days=args.max_hold,
    )

    print(f"\n📊 anju-AI backtest  {args.start} → {args.end}")
    print(f"   universe={args.universe} ({len(symbols)} symbols)  "
          f"mode={args.mode}  min_score={args.min_score}\n")

    con = init_if_needed()
    run_id = _create_run(con, inp)
    audit_log(con, "BACKTEST_STARTED",
              f"#{run_id} {name} {args.start}→{args.end} "
              f"univ={args.universe} mode={args.mode}")

    tg_send(f"🟢 <b>Backtest started</b>  #{run_id}\n"
            f"<b>{name}</b>\n"
            f"{args.start} → {args.end}  ·  {len(symbols)} symbols  ·  "
            f"mode {args.mode}\n<i>Expect a final report in 5-30 min.</i>")

    progress = None if args.no_progress else TelegramProgress(every_n_days=100)

    try:
        report, trades = run_backtest(
            inp,
            ohlcv_loader=get_ohlcv,
            nifty_loader=lambda: get_index("^NSEI", days=2000)["Close"],
            progress_cb=progress,
        )
    except Exception as e:
        traceback.print_exc()
        _fail_run(con, run_id, str(e))
        audit_log(con, "BACKTEST_FAILED", f"#{run_id}: {e}", severity="CRITICAL")
        tg_send(f"❌ <b>Backtest failed</b> #{run_id}\n<code>{e}</code>")
        con.close()
        return 1

    _finalize_run(con, run_id, report)
    audit_log(con, "BACKTEST_COMPLETED",
              f"#{run_id} closed={report.total_closed} "
              f"win_rate={report.win_rate_pct}% "
              f"net_exp={report.net_expectancy_pct:+.3f}%/trade")
    con.close()

    print(f"\n✅ Backtest #{run_id} complete: {report.total_closed} closed trades, "
          f"net expectancy {report.net_expectancy_pct:+.3f}%/trade")

    if not args.no_telegram:
        tg_send(render_report(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
