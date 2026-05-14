-- ============================================================================
-- 002_backtest_runs.sql — namespace backtest signals separately from live paper
-- ============================================================================
--
-- Why: a backtest run generates thousands of signals/fills/outcomes that share
-- the same memory.db as live-paper morning_scan output. Without a run_id,
-- the morning_scan digest would show backtest signals as "today's signals"
-- and the eod_close would try to close them. Add an FK to backtest_runs so:
--
--   - live paper:   backtest_run_id IS NULL
--   - backtest run: backtest_run_id = <id of that run>
--
-- Queries like "today's signals" then filter `WHERE backtest_run_id IS NULL`.

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    universe        TEXT NOT NULL,
    mode            TEXT NOT NULL,
    capital_inr     REAL NOT NULL,
    config_json     TEXT NOT NULL,       -- full input config snapshot
    summary_json    TEXT,                -- computed metrics after run completes
    status          TEXT NOT NULL DEFAULT 'RUNNING',  -- RUNNING | COMPLETED | FAILED
    started_at      TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),
    completed_at    TEXT,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_btruns_status ON backtest_runs(status);

-- Add backtest_run_id to signals, fills, outcomes.
-- SQLite ALTER TABLE ADD COLUMN can't add an FK directly, but we declare
-- the column NULLable and use it as a soft FK (enforced in app code).

ALTER TABLE signals  ADD COLUMN backtest_run_id INTEGER DEFAULT NULL;
ALTER TABLE fills    ADD COLUMN backtest_run_id INTEGER DEFAULT NULL;
ALTER TABLE outcomes ADD COLUMN backtest_run_id INTEGER DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_signals_btrun  ON signals(backtest_run_id);
CREATE INDEX IF NOT EXISTS idx_fills_btrun    ON fills(backtest_run_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_btrun ON outcomes(backtest_run_id);

-- Update the signals_current view to keep returning the same rows. Old rows
-- have backtest_run_id = NULL so live morning_scan continues working.
DROP VIEW IF EXISTS signals_current;
CREATE VIEW signals_current AS
SELECT s.* FROM signals s
WHERE NOT EXISTS (SELECT 1 FROM signals s2 WHERE s2.supersedes = s.id);

-- Convenience view: only live (non-backtest) signals
CREATE VIEW IF NOT EXISTS signals_live AS
SELECT * FROM signals_current WHERE backtest_run_id IS NULL;
