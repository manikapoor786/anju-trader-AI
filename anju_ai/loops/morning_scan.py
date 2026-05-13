#!/usr/bin/env python3
"""
anju_ai.loops.morning_scan — Phase 0 connectivity stub.

This is intentionally a stub. It accepts the CLI arguments declared in
morning.yml and manual_scan.yml, validates that secrets/env are wired up
correctly, and sends a Telegram message confirming the plumbing works.

The actual scan logic lands in Phase 0 task 0.18 (see docs/ROADMAP.md).

Usage:
    python -m anju_ai.loops.morning_scan --step refresh
    python -m anju_ai.loops.morning_scan --step full --universe nifty500 ...
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Load .env from project root
ROOT = Path(__file__).resolve().parent.parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def tg_send(text: str) -> bool:
    """Send a Telegram message. Returns True on success, False otherwise."""
    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print(f"  ⚠️  Telegram creds missing — would have sent:\n{text}")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        ok = r.ok and r.json().get("ok", False)
        if not ok:
            print(f"  ⚠️  Telegram error: {r.status_code} {r.text[:200]}")
        return ok
    except Exception as e:
        print(f"  ⚠️  Telegram send failed: {e}")
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--step", default="full",
                   choices=["refresh", "regime", "scan", "catalyst",
                            "paper_fill", "digest", "full"])
    p.add_argument("--universe", default="nifty500")
    p.add_argument("--mode", default="auto")
    p.add_argument("--min-score", default="")
    p.add_argument("--replay-date", default="")
    p.add_argument("--paper-only", default="true")
    p.add_argument("--catalyst-llm", default="true")
    args = p.parse_args()

    now = datetime.now().strftime("%d %b %Y %H:%M")

    print(f"[anju-AI] morning_scan step={args.step} universe={args.universe} "
          f"mode={args.mode} @ {now} IST")

    # Phase 0: only the 'digest' / 'full' step actually does anything visible.
    # All other steps just print and return — no-op stubs until Phase 0 task 0.18.
    if args.step not in ("digest", "full"):
        print(f"  (Phase 0 stub — step '{args.step}' is a no-op until 0.18)")
        return 0

    # Check what creds we have
    has_telegram = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
    has_gemini   = bool(os.getenv("GEMINI_API_KEY"))
    has_claude   = bool(os.getenv("ANTHROPIC_API_KEY"))
    has_kite     = bool(os.getenv("KITE_API_KEY"))

    msg = (
        f"🌱 <b>anju-trader-AI · Phase 0 connectivity test</b>\n"
        f"Run: <code>{args.step}</code> | Universe: <code>{args.universe}</code> | "
        f"Mode: <code>{args.mode}</code>\n"
        f"Time: {now} IST\n\n"
        f"<b>Creds detected:</b>\n"
        f"  Telegram: {'✅' if has_telegram else '❌'}\n"
        f"  Gemini:   {'✅' if has_gemini else '❌ (set GEMINI_API_KEY)'}\n"
        f"  Claude:   {'✅' if has_claude else '⚪ (Phase 3 only — optional now)'}\n"
        f"  Kite:     {'✅' if has_kite else '⚪ (Phase 4 only — optional now)'}\n\n"
        f"<i>This is the Phase 0 plumbing test. Actual scan logic ships in "
        f"task 0.18 (see docs/ROADMAP.md). Until then this workflow just "
        f"confirms the GitHub Actions → Telegram pipe works.</i>"
    )

    ok = tg_send(msg)
    if not ok:
        print("  ❌ Telegram send failed — check secrets")
        return 1
    print("  ✅ Telegram digest sent — connectivity verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
