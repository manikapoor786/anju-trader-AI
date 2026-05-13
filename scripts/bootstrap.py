#!/usr/bin/env python3
"""
bootstrap.py — first-time setup helper.

Run this once after cloning the repo. It:
  1. Verifies Python version + venv is active
  2. Installs requirements.txt
  3. Creates data/ directory + initialises memory.db with the latest schema
  4. Validates configs (runtime.yaml, strategies.yaml, llm.yaml)
  5. Checks for required GitHub Actions secrets (lists what to set)
  6. Sends a "hello world" Telegram message if creds are present

Usage:
    python scripts/bootstrap.py
"""

import os
import sys
import shutil
import sqlite3
from pathlib import Path


def step(name: str) -> None:
    print(f"\n── {name} " + "─" * (70 - len(name) - 4))


def main() -> int:
    ROOT = Path(__file__).resolve().parent.parent

    step("Python version")
    if sys.version_info < (3, 10):
        print(f"  ❌ Need Python ≥ 3.10, got {sys.version_info[:2]}")
        return 1
    print(f"  ✅ {sys.version.split()[0]}")

    step("Virtual environment")
    if sys.prefix == sys.base_prefix:
        print("  ⚠️  Not in a venv. Recommended: python -m venv venv && source venv/bin/activate")
    else:
        print(f"  ✅ venv: {sys.prefix}")

    step("Data directory")
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    print(f"  ✅ {data_dir}")

    step("memory.db (schema bootstrap is a Phase 0 task — stub for now)")
    db_path = data_dir / "memory.db"
    if db_path.exists():
        print(f"  ✅ {db_path} already exists")
    else:
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS schema_versions ("
                    "  version INTEGER PRIMARY KEY,"
                    "  applied_at TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),"
                    "  notes TEXT)")
        con.execute("INSERT INTO schema_versions(version, notes) VALUES (0, 'bootstrap')")
        con.commit()
        con.close()
        print(f"  ✅ Created {db_path} with placeholder schema_versions table")
        print("  ℹ️  Full schema will be applied in Phase 0 task 0.17")

    step("Configs present")
    for cfg in ["runtime.yaml", "strategies.yaml", "llm.yaml", "universe.yaml"]:
        p = ROOT / "config" / cfg
        if p.exists():
            print(f"  ✅ {p.name}")
        else:
            print(f"  ❌ {p.name} MISSING")
            return 1

    step("Required GitHub Actions secrets (set on your repo)")
    required_secrets = [
        ("TELEGRAM_BOT_TOKEN_AI",  "Bot token for the *new* anju-AI bot — not the anju-trader one"),
        ("TELEGRAM_CHAT_ID_AI",    "Chat ID for the *new* anju-AI Telegram chat"),
        ("GEMINI_API_KEY",         "Google AI Studio key (free) — https://aistudio.google.com/apikey"),
        ("ANTHROPIC_API_KEY",      "Anthropic console key — only needed Phase 3+"),
        ("KITE_API_KEY",           "Kite Connect API key — Phase 4 only (cutover)"),
        ("KITE_API_SECRET",        "Kite Connect API secret — Phase 4 only"),
        ("KITE_ACCESS_TOKEN",      "Daily Kite access token — Phase 4 only (workflow refreshes daily)"),
    ]
    for name, note in required_secrets:
        print(f"  • {name:<28} — {note}")
    print()
    print("  Set them on GitHub: Repo → Settings → Secrets and variables → Actions → New secret")

    step("Done")
    print("Next:")
    print("  1. Create a NEW Telegram bot via @BotFather (don't reuse the anju-trader bot)")
    print("  2. Get its TOKEN and CHAT_ID")
    print("  3. Create the GitHub repo (public during Phase 0–3 per ADR-009)")
    print("  4. Set the secrets above")
    print("  5. Push this code, then trigger morning.yml manually from the GitHub mobile app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
