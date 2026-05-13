#!/usr/bin/env python3
"""
anju_ai.loops.deep_review — Phase 0 connectivity stub.

Real implementation lands in Phase 3 (task 3.5) when the LLM agent layer
is operational. Until then this just confirms the workflow can be triggered.
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
    p.add_argument("--symbol", required=True)
    p.add_argument("--horizon", default="BOTH")
    p.add_argument("--question", default="")
    p.add_argument("--model", default="auto")
    args = p.parse_args()

    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    now   = datetime.now().strftime("%d %b %Y %H:%M")

    sym = args.symbol.upper().replace(".NS", "").replace(".BSE", "")

    msg = (
        f"🔬 <b>anju-AI · Deep Review request received</b>\n"
        f"Symbol: <code>{sym}</code>\n"
        f"Horizon: <code>{args.horizon}</code>\n"
        f"Model: <code>{args.model}</code>\n"
        f"Time: {now} IST\n\n"
        + (f"Your question: <i>{args.question}</i>\n\n" if args.question else "")
        + f"<i>Phase 0 stub — deep LLM review ships in Phase 3 (task 3.5). "
          f"Until then this workflow only confirms it can be triggered from "
          f"your phone.</i>"
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
