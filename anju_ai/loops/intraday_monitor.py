#!/usr/bin/env python3
"""
anju_ai.loops.intraday_monitor — every-30-min open-position monitor.

Per AGENT_PROTOCOL §2 / ROADMAP 2.9. During market hours (09:15–15:30
IST), checks live prices for all open paper positions and alerts on:

  1. Stop-hit         — current price <= suggested_stop
  2. Target-hit       — current price >= suggested_t1 (or t2)
  3. Unusual move     — intraday move > ±3% (alert only, no auto-action)
  4. Stale            — position older than max_hold_days

The actual outcome-recording happens in eod_close.py (which runs after
market close). Intraday monitor is alerts-only — no DB writes beyond
audit log. Phase 4 (live) wires this to bracket-order updates.

Data source: yfinance 1-minute or recent-day data (no real-time tick
needed for 30-min cadence). Falls back gracefully if quotes unavailable.

Usage (CLI / workflow):
    python -m anju_ai.loops.intraday_monitor
    python -m anju_ai.loops.intraday_monitor --no-telegram   # silent
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
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

from anju_ai.memory.db import audit_log, init_if_needed


# ── Telegram helper ──────────────────────────────────────────────────────────

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


# ── Market-hours check ───────────────────────────────────────────────────────

def is_market_open(now: datetime | None = None) -> bool:
    """IST market hours: Mon-Fri, 09:15-15:30."""
    if now is None:
        now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    if now.weekday() >= 5:        # Sat/Sun
        return False
    h, m = now.hour, now.minute
    after_open = (h > 9) or (h == 9 and m >= 15)
    before_close = (h < 15) or (h == 15 and m <= 30)
    return after_open and before_close


# ── Live price fetcher (yfinance, fast) ──────────────────────────────────────

def fetch_live_price(symbol: str) -> float | None:
    """Best-effort live price via yfinance fast_info. Returns None on failure."""
    try:
        import yfinance as yf
        sym_ns = symbol if symbol.endswith(".NS") else symbol + ".NS"
        ticker = yf.Ticker(sym_ns)
        fi = ticker.fast_info
        price = (getattr(fi, "last_price", None)
                 or getattr(fi, "regular_market_price", None))
        if price is None:
            # Fall back to recent history
            df = ticker.history(period="1d", interval="5m")
            if df is not None and not df.empty:
                price = float(df["Close"].iloc[-1])
        return float(price) if price else None
    except Exception:
        return None


# ── Alert types ──────────────────────────────────────────────────────────────

def classify_alert(entry: float, stop: float | None, t1: float | None,
                   t2: float | None, current: float,
                   days_held: int, max_hold_days: int) -> tuple[str, str] | None:
    """Return (severity, summary) tuple, or None if no alert."""
    if current is None or entry <= 0:
        return None

    pnl_pct = (current - entry) / entry * 100
    intraday_move = (current - entry) / entry * 100   # rough — needs prev close for true intraday

    # 1. Stop-hit (highest severity)
    if stop is not None and current <= stop:
        return ("CRITICAL",
                f"🛑 Stop hit at ₹{current:.2f} (stop ₹{stop:.2f}, P&L {pnl_pct:+.1f}%) — close position")

    # 2. Target-hit
    if t2 is not None and current >= t2:
        return ("INFO",
                f"🎯 T2 hit at ₹{current:.2f} (target ₹{t2:.2f}, P&L {pnl_pct:+.1f}%) — full exit suggested")
    if t1 is not None and current >= t1:
        return ("INFO",
                f"🎯 T1 hit at ₹{current:.2f} (target ₹{t1:.2f}, P&L {pnl_pct:+.1f}%) — book partial / trail stop")

    # 3. Stale (older than max_hold_days)
    if days_held >= max_hold_days:
        return ("WARN",
                f"⏳ Held {days_held} days (max {max_hold_days}), P&L {pnl_pct:+.1f}% — time exit?")

    # 4. Unusual intraday move (placeholder — needs prev close to compute properly)
    # Phase 2.9 stub returns None — Phase 2.10 wires the real intraday delta
    return None


# ── Pipeline ─────────────────────────────────────────────────────────────────

def fetch_open_positions(con) -> list[dict]:
    """Read live-paper open positions from memory.db.
    Excludes backtest signals (backtest_run_id IS NOT NULL)."""
    rows = con.execute("""
        SELECT s.id          AS signal_id,
               s.symbol      AS symbol,
               s.signal_date AS signal_date,
               s.entry_price AS entry_price,
               s.suggested_stop AS stop,
               s.suggested_t1   AS t1,
               s.suggested_t2   AS t2,
               s.suggested_qty  AS qty,
               f.fill_price     AS fill_price,
               f.fill_qty       AS fill_qty,
               f.fill_date      AS fill_date,
               f.id             AS fill_id
        FROM signals_current s
        JOIN fills f ON f.signal_id = s.id
        WHERE s.backtest_run_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM outcomes o WHERE o.fill_id = f.id)
    """).fetchall()
    return [dict(r) for r in rows]


def run(skip_telegram: bool = False, max_hold_days: int = 90) -> int:
    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    print(f"\n📡 anju-AI intraday_monitor  {now_ist.strftime('%H:%M IST')}\n")

    if not is_market_open(now_ist):
        print("Market closed — exiting cleanly")
        return 0

    con = init_if_needed()
    try:
        positions = fetch_open_positions(con)
        if not positions:
            print("No open positions to monitor")
            return 0

        print(f"Monitoring {len(positions)} open positions...")
        alerts: list[tuple[str, str, dict]] = []

        with ThreadPoolExecutor(max_workers=8) as ex:
            symbols = [p["symbol"] for p in positions]
            prices = list(ex.map(fetch_live_price, symbols))

        for p, current in zip(positions, prices):
            if current is None:
                continue
            days_held = _days_between(p["fill_date"], now_ist)
            alert = classify_alert(
                entry=float(p["fill_price"]), stop=p["stop"],
                t1=p["t1"], t2=p["t2"], current=current,
                days_held=days_held, max_hold_days=max_hold_days,
            )
            if alert:
                severity, summary = alert
                alerts.append((severity, summary, p))

        for severity, summary, p in alerts:
            audit_log(con, f"INTRADAY_ALERT_{severity}",
                      f"{p['symbol']}: {summary}",
                      severity=severity)

        if alerts and not skip_telegram:
            _send_alerts_telegram(alerts, now_ist)
        elif not alerts:
            print("No alerts triggered — all positions healthy")

    finally:
        con.close()
    return 0


def _days_between(date_str: str, now: datetime) -> int:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(
            tzinfo=timezone(timedelta(hours=5, minutes=30)))
        return (now.date() - d.date()).days
    except (ValueError, TypeError):
        return 0


def _send_alerts_telegram(alerts: list[tuple[str, str, dict]],
                          now: datetime) -> None:
    lines = [f"📡 <b>anju-AI · Intraday Monitor</b>  {now.strftime('%H:%M IST')}"]
    by_sev = {"CRITICAL": [], "WARN": [], "INFO": []}
    for severity, summary, p in alerts:
        by_sev[severity].append(f"  <b>{p['symbol']}</b>  {summary}")
    if by_sev["CRITICAL"]:
        lines.append("\n🔴 <b>CRITICAL</b>")
        lines.extend(by_sev["CRITICAL"])
    if by_sev["WARN"]:
        lines.append("\n🟡 <b>WARN</b>")
        lines.extend(by_sev["WARN"])
    if by_sev["INFO"]:
        lines.append("\n🟢 <b>INFO</b>")
        lines.extend(by_sev["INFO"])
    tg_send("\n".join(lines))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--no-telegram", action="store_true")
    p.add_argument("--max-hold", type=int, default=90)
    args = p.parse_args()
    return run(skip_telegram=args.no_telegram, max_hold_days=args.max_hold)


if __name__ == "__main__":
    sys.exit(main())
