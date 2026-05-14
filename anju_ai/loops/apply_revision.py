#!/usr/bin/env python3
"""
anju_ai.loops.apply_revision — approve/reject a weekly_critic proposal.

ADR-005 demands human approval for every self-modification. The full
Telegram-bot polling version needs a hosted webhook (deferred). For
now this CLI does the same job:

  $ python -m anju_ai.loops.apply_revision --id 42 --action approve
  $ python -m anju_ai.loops.apply_revision --id 42 --action reject \
        --reason "data too noisy"

Triggered from manual_revision.yml workflow_dispatch — user types
the proposal id + action from GitHub mobile. Workflow then:
  - Marks revision status APPROVED or REJECTED
  - If APPROVED + concrete param change: writes the parameter into
    config/model_params.json (committed back to repo)
  - Telegram confirmation either way

For WEIGHT or FILTER changes that require backtest first, the
revision sits in BACKTESTING until the backtest run completes;
this CLI rejects an approval attempt and points to the backtest.
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


def load_revision(con, revision_id: int) -> dict | None:
    row = con.execute(
        "SELECT * FROM revisions WHERE id=?", (revision_id,)
    ).fetchone()
    return dict(row) if row else None


def apply_parameter_change(target: str, value: str) -> bool:
    """Persist a config/model_params.json override.

    Example: target='tools.scoring.MIN_BASE_SCORE', value='5'
    Writes: { 'tools.scoring.MIN_BASE_SCORE': '5' } into model_params.json
    """
    params_file = ROOT / "config" / "model_params.json"
    try:
        if params_file.exists():
            params = json.loads(params_file.read_text())
        else:
            params = {}
    except Exception:
        params = {}
    params[target] = value
    params_file.parent.mkdir(parents=True, exist_ok=True)
    params_file.write_text(json.dumps(params, indent=2, sort_keys=True))
    return True


def approve(con, revision_id: int) -> tuple[bool, str]:
    rev = load_revision(con, revision_id)
    if not rev:
        return False, f"Revision #{revision_id} not found"
    if rev["status"] == "APPROVED" or rev["status"] == "APPLIED":
        return False, f"Revision #{revision_id} already approved"
    if rev["status"] == "REJECTED":
        return False, f"Revision #{revision_id} was rejected — cannot un-reject"
    if rev["status"] == "BACKTESTING" and rev["backtest_required"]:
        return False, (
            f"Revision #{revision_id} still requires a backtest. "
            f"Run the Backtest workflow with these params first, then "
            f"come back to approve."
        )

    # Only PARAMETER changes are auto-applied; WEIGHT / FILTER changes that
    # involve scoring weights require a code change + PR.
    apply_now = (rev["kind"] == "PARAMETER")
    if apply_now:
        apply_parameter_change(rev["target"], rev["proposed_value"])

    new_status = "APPLIED" if apply_now else "APPROVED"
    con.execute("""
        UPDATE revisions
           SET status=?, decided_by='MANISH', decided_at=?
         WHERE id=?
    """, (new_status, datetime.now(timezone.utc).isoformat(), revision_id))

    audit_log(con, "REVISION_APPROVED",
              f"#{revision_id} {rev['kind']} {rev['target']}: "
              f"{rev['current_value']} → {rev['proposed_value']}")
    return True, (
        f"Revision #{revision_id} APPROVED"
        + (" + applied to config/model_params.json" if apply_now else
           " — manual code change still needed for kind=" + rev["kind"])
    )


def reject(con, revision_id: int, reason: str = "") -> tuple[bool, str]:
    rev = load_revision(con, revision_id)
    if not rev:
        return False, f"Revision #{revision_id} not found"
    if rev["status"] in ("APPROVED", "APPLIED"):
        return False, f"Revision #{revision_id} already approved — cannot reject"
    if rev["status"] == "REJECTED":
        return False, f"Revision #{revision_id} already rejected"

    con.execute("""
        UPDATE revisions
           SET status='REJECTED', decided_by='MANISH', decided_at=?,
               decision_reason=?
         WHERE id=?
    """, (datetime.now(timezone.utc).isoformat(), reason or None, revision_id))

    audit_log(con, "REVISION_REJECTED",
              f"#{revision_id} {rev['target']}: {reason or 'no reason'}")
    return True, f"Revision #{revision_id} REJECTED" + (f": {reason}" if reason else "")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--action", required=True, choices=["approve", "reject"])
    p.add_argument("--reason", default="")
    args = p.parse_args()

    con = init_if_needed()
    try:
        if args.action == "approve":
            ok, msg = approve(con, args.id)
        else:
            ok, msg = reject(con, args.id, args.reason)
    finally:
        con.close()

    emoji = "✅" if ok else "❌"
    tg_send(f"{emoji} <b>Revision #{args.id}</b>\n<i>{msg}</i>")
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
