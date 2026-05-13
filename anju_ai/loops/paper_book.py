#!/usr/bin/env python3
"""
anju_ai.loops.paper_book — Phase 0 connectivity stub.

Real implementation lands in Phase 1 alongside the outcome tracker.
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--detail", default="normal",
                   choices=["brief", "normal", "full_with_reasoning"])
    args = p.parse_args()

    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    now   = datetime.now().strftime("%d %b %Y %H:%M")

    msg = (
        f"📒 <b>anju-AI · Paper Book Snapshot</b>\n"
        f"Detail: <code>{args.detail}</code> | {now} IST\n\n"
        f"<i>Phase 0 stub — paper portfolio not yet populated. "
        f"Real paper book ships in Phase 1 (task 1.4).</i>\n\n"
        f"Capital configured: ₹1.75 cr (from config/runtime.yaml)\n"
        f"Open positions: 0 (none until Phase 0 task 0.18 fills the first signal)\n"
    )

    if not token or not chat:
        print(msg)
        return 0

    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
        timeout=15,
    )
    print(f"  Telegram: {r.status_code} {r.json().get('ok')}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
