-- ============================================================================
-- 003_options_iv_history.sql — F&O ATM IV history per symbol
-- ============================================================================
--
-- Populated by daily option-chain ingest. Used by iv_percentile() to
-- determine when ATM calls are cheap (IVP < 30) vs expensive (IVP > 70).
-- One row per (symbol, date). REPLACE on conflict so re-fetches the same
-- day overwrite without duplicates.

CREATE TABLE IF NOT EXISTS iv_history (
    symbol   TEXT NOT NULL,
    date     TEXT NOT NULL,    -- 'YYYY-MM-DD'
    atm_iv   REAL NOT NULL,
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_iv_symbol_date ON iv_history(symbol, date);
