#!/usr/bin/env python3
"""
get_telegram_chat_id.py — find your new bot's chat ID.

Steps:
    1. Send any message to your new bot OR add it to a group and send a message there
    2. Run this script with the bot token:
         BOT_TOKEN=<your_token> python scripts/get_telegram_chat_id.py
    3. Copy the chat ID it prints.

If you don't see a chat, you forgot to message the bot first. Message it,
then run again.
"""

import os
import sys
import json
import urllib.request


def main() -> int:
    token = os.getenv("BOT_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not token:
        print("Usage: BOT_TOKEN=<your_token> python scripts/get_telegram_chat_id.py")
        print("   or: python scripts/get_telegram_chat_id.py <your_token>")
        return 2

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"❌ Failed to call Telegram API: {e}")
        return 1

    if not data.get("ok"):
        print(f"❌ Telegram API error: {data}")
        return 1

    results = data.get("result", [])
    if not results:
        print("⚠️  No updates yet. Send any message to your bot, then re-run this.")
        print("    (Open Telegram → find your new bot → send 'hi' → run again)")
        return 0

    print(f"\n✅ Found {len(results)} update(s). Chat IDs:\n")
    seen = set()
    for upd in results:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat", {})
        cid  = chat.get("id")
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        kind  = chat.get("type", "?")
        title = chat.get("title") or chat.get("username") or chat.get("first_name", "?")
        print(f"   chat_id = {cid}   ({kind})   {title}")

    print(f"\nUse one of these as TELEGRAM_CHAT_ID_AI in GitHub secrets.")
    print("(Group chat IDs are negative numbers like -987654321 — that's correct.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
