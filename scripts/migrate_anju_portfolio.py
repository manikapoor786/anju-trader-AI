#!/usr/bin/env python3
"""
scripts/migrate_anju_portfolio.py — port anju-trader's portfolio.json
into anju-AI's signals + fills tables.

PHASE 4 PREP (ROADMAP 4.1). Built ahead of time so the cutover ceremony
is mechanical — no last-minute SQL editing.

What it does:
  1. Reads <anju-trader-repo>/portfolio.json (44 positions)
  2. For each position, creates:
     - A regime_snapshot for the entry date (best-effort, uses MAX known)
     - A signals row with verdict='BUY', breakdown_json={'migrated': true}
     - A fills row with fill_price=entry_price, qty=qty, is_paper=0
  3. Tags each row with note='migrated_from_anju_trader'
  4. Generates a migration report (counts + symbol list + warnings)

USAGE:
    python scripts/migrate_anju_portfolio.py \\
        --source ~/anju-trader/portfolio.json \\
        --dry-run               # preview without writes
    python scripts/migrate_anju_portfolio.py \\
        --source ~/anju-trader/portfolio.json \\
        --commit                # actual migration

Safety:
  - Refuses to run if anju-AI memory.db has live signals already
  - Marks all migrated fills with is_paper=0 (i.e. real positions)
  - Idempotent: re-run won't duplicate (checks symbol+entry_date+qty)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap project root so anju_ai imports work when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_anju_portfolio(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data.get("positions", []) if isinstance(data, dict) else (
        data if isinstance(data, list) else []
    )


def already_migrated(con, symbol: str, entry_date: str, qty: int) -> bool:
    """Idempotent guard: same (symbol, entry_date, qty) already in fills?"""
    row = con.execute("""
        SELECT 1
          FROM fills f
          JOIN signals_current s ON f.signal_id = s.id
         WHERE s.symbol = ?
           AND f.fill_date = ?
           AND f.fill_qty = ?
         LIMIT 1
    """, (symbol.replace(".NS", ""), entry_date, qty)).fetchone()
    return row is not None


def get_or_create_regime(con, date_str: str) -> int:
    """Best-effort regime row for a historical entry date. If we don't
    have one for that date, create a placeholder."""
    row = con.execute(
        "SELECT id FROM regime_snapshots WHERE snapshot_date = ?",
        (date_str,)
    ).fetchone()
    if row:
        return row["id"]
    cur = con.execute("""
        INSERT INTO regime_snapshots
            (snapshot_date, state, min_score, nifty_close, payload_json)
        VALUES (?, 'Unknown', 0, 0, '{"note": "migration placeholder"}')
    """, (date_str,))
    return cur.lastrowid or 0


def migrate_one(con, pos: dict) -> dict:
    """Migrate one position. Returns {status, symbol, reason}."""
    symbol = (pos.get("symbol") or "").replace(".NS", "").replace(".BSE", "")
    if not symbol:
        return {"status": "skipped", "reason": "no symbol"}

    qty = int(pos.get("qty", 0) or 0)
    entry_price = float(pos.get("entry", 0) or 0)
    if qty <= 0 or entry_price <= 0:
        return {"status": "skipped", "symbol": symbol, "reason": "qty or entry zero"}

    entry_date = pos.get("entry_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stop = float(pos.get("stop", 0) or entry_price * 0.95)
    t1   = float(pos.get("target1", 0) or 0) or None
    t2   = float(pos.get("target2", 0) or 0) or None

    if already_migrated(con, symbol, entry_date, qty):
        return {"status": "skipped", "symbol": symbol, "reason": "already migrated"}

    regime_id = get_or_create_regime(con, entry_date)

    # Insert signal
    cur = con.execute("""
        INSERT INTO signals
            (signal_date, symbol, horizon, regime_id, rule_score, final_score,
             verdict, entry_price, suggested_stop, suggested_t1, suggested_t2,
             suggested_qty, suggested_instrument, breakdown_json)
        VALUES (?, ?, 'POSITIONAL', ?, 0, 0, 'BUY', ?, ?, ?, ?, ?, 'CASH', ?)
    """, (entry_date, symbol, regime_id, entry_price, stop, t1, t2, qty,
          json.dumps({"migrated_from": "anju-trader", "source_pos": pos},
                     default=str)))
    signal_id = cur.lastrowid

    # Insert fill (is_paper=0 → live, not paper)
    gross = entry_price * qty
    con.execute("""
        INSERT INTO fills
            (signal_id, fill_date, fill_price, fill_qty, instrument,
             gross_value, cost_slippage, cost_total, is_paper)
        VALUES (?, ?, ?, ?, 'CASH', ?, 0, 0, 0)
    """, (signal_id, entry_date, entry_price, qty, gross))

    return {"status": "migrated", "symbol": symbol, "qty": qty,
            "entry_price": entry_price}


def has_existing_live_signals(con) -> int:
    """Count of signals not from a backtest. If >0 we should be careful."""
    r = con.execute(
        "SELECT COUNT(*) FROM signals_current WHERE backtest_run_id IS NULL"
    ).fetchone()
    return int(r[0]) if r else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True,
                   help="Path to anju-trader's portfolio.json")
    p.add_argument("--commit", action="store_true",
                   help="Actually write changes (default is dry-run preview)")
    p.add_argument("--force", action="store_true",
                   help="Migrate even if anju-AI has existing live signals")
    args = p.parse_args()

    src = Path(args.source).expanduser()
    positions = load_anju_portfolio(src)
    if not positions:
        print(f"❌ No positions found in {src}")
        return 1

    print(f"📦 anju-AI portfolio migration  ({len(positions)} positions)\n")

    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    try:
        existing = has_existing_live_signals(con)
        if existing > 0 and not args.force:
            print(f"❌ anju-AI memory.db has {existing} existing live signals.")
            print("   Add --force to migrate alongside them, or empty memory.db first.")
            return 2

        results = []
        for pos in positions:
            res = migrate_one(con, pos)
            results.append(res)
            sym = res.get("symbol", "?")
            status = res["status"]
            print(f"  [{status:10s}] {sym:15s} {res.get('reason', '')}")

        if args.commit:
            con.commit()
            print(f"\n✅ Committed.")
        else:
            con.rollback()
            print(f"\n🔍 DRY RUN — no changes written. Re-run with --commit to apply.")

        # Summary
        migrated = sum(1 for r in results if r["status"] == "migrated")
        skipped  = sum(1 for r in results if r["status"] == "skipped")
        print(f"\nSummary: {migrated} migrated, {skipped} skipped, "
              f"{len(positions)} total")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
