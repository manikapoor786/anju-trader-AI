"""Memory layer — the agent's persistent brain state.

Phase 0 deliverable:
    db.py         SQLite connection + migrations + transactional helpers
    schema.sql    Initial schema (matches docs/MEMORY_SCHEMA.md)

Phase 1+ adds typed accessors:
    signals.py    Read/insert signals with the supersedes pattern
    outcomes.py   Read/insert outcomes
    lessons.py    Read/insert lessons
    revisions.py  Propose/approve/apply revisions
    audit.py      Append-only audit log
    traces.py     Reasoning trace insert/query
"""
