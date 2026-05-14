#!/usr/bin/env python3
"""
anju_ai.memory.db — SQLite connection + migrations runner.

The agent's brain state lives in <project_root>/data/memory.db. Migrations
live as numbered .sql files in anju_ai/memory/migrations/. Each is applied
exactly once; the schema_versions table tracks which have run.

Append-only invariant: signals, outcomes, reasoning_traces, audit, lessons,
revisions are NEVER updated. Corrections insert new rows with `supersedes`
FK to the prior row.

Override the path with $ANJU_MEMORY_DB (useful for tests).

Usage:
    from anju_ai.memory.db import connect, apply_migrations, MEMORY_DB_PATH

    con = connect()
    apply_migrations(con)
    rows = con.execute("SELECT * FROM signals_current").fetchall()
    con.close()
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


# ── Path resolution ───────────────────────────────────────────────────────────

def _resolve_db_path() -> Path:
    """Default: <project_root>/data/memory.db. Env override: $ANJU_MEMORY_DB."""
    if env := os.getenv("ANJU_MEMORY_DB"):
        return Path(env)
    project_root = Path(__file__).resolve().parents[2]
    db = project_root / "data" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


MEMORY_DB_PATH       = _resolve_db_path()
MIGRATIONS_DIR       = Path(__file__).resolve().parent / "migrations"


# ── Connection ────────────────────────────────────────────────────────────────

def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a memory.db connection with WAL + sensible defaults.
    Always run `apply_migrations(con)` once after opening for the first time."""
    path = Path(db_path) if db_path else MEMORY_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


# ── Migrations ────────────────────────────────────────────────────────────────

def _ensure_versions_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),
            notes      TEXT
        )
    """)


def _applied_versions(con: sqlite3.Connection) -> set[int]:
    _ensure_versions_table(con)
    return {r[0] for r in con.execute("SELECT version FROM schema_versions").fetchall()}


def _discover_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[tuple[int, str, Path]]:
    """Find all NNN_name.sql files. Returns sorted [(version, name, path)]."""
    if not migrations_dir.exists():
        return []
    out = []
    for p in sorted(migrations_dir.iterdir()):
        if not p.is_file() or p.suffix != ".sql":
            continue
        # Filename pattern: NNN_name.sql  →  parse leading int
        try:
            version = int(p.stem.split("_", 1)[0])
            name = p.stem.split("_", 1)[1] if "_" in p.stem else p.stem
        except (ValueError, IndexError):
            continue
        out.append((version, name, p))
    return sorted(out, key=lambda x: x[0])


def apply_migrations(con: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR,
                     verbose: bool = False) -> int:
    """Apply all pending migrations in order. Returns number of migrations run."""
    _ensure_versions_table(con)
    applied = _applied_versions(con)
    all_migs = _discover_migrations(migrations_dir)
    pending = [m for m in all_migs if m[0] not in applied]

    if verbose:
        print(f"  Schema migrations: {len(applied)} applied, {len(pending)} pending")

    for version, name, path in pending:
        sql = path.read_text()
        if verbose:
            print(f"    Applying {version:03d}_{name}...")
        try:
            con.executescript(sql)
            con.execute(
                "INSERT INTO schema_versions(version, notes) VALUES (?, ?)",
                (version, name),
            )
        except Exception as e:
            raise RuntimeError(f"Migration {version}_{name} failed: {e}") from e

    return len(pending)


# ── Helpers ───────────────────────────────────────────────────────────────────

def init_if_needed(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open + apply migrations in one call. Returns ready-to-use connection."""
    con = connect(db_path)
    apply_migrations(con)
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def audit_log(con: sqlite3.Connection, event_type: str, summary: str,
              severity: str = "INFO", payload_json: str | None = None,
              linked_id: int | None = None, linked_table: str | None = None) -> int:
    """Append a row to the audit ledger. Returns the new audit.id."""
    cur = con.execute(
        """INSERT INTO audit (event_type, severity, summary, payload_json,
                              linked_id, linked_table)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (event_type, severity, summary, payload_json, linked_id, linked_table),
    )
    return cur.lastrowid or 0
