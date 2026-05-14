-- ============================================================================
-- 001_initial.sql — initial memory.db schema
-- Matches docs/MEMORY_SCHEMA.md. Append-only tables; corrections via supersedes.
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 30000;
PRAGMA foreign_keys = ON;

-- ── Regime snapshots ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS regime_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL UNIQUE,
    state           TEXT NOT NULL,
    min_score       INTEGER NOT NULL,
    nifty_close     REAL NOT NULL,
    nifty_ma20      REAL,
    nifty_ma50      REAL,
    nifty_ma200     REAL,
    vix_close       REAL,
    breadth_pct     REAL,
    vol_10d_pct     REAL,
    payload_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);

-- ── Flow snapshots ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS flows_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL UNIQUE,
    fii_cash_cr     REAL,
    dii_cash_cr     REAL,
    fii_futures_cr  REAL,
    fii_options_cr  REAL,
    bulk_deals_json TEXT NOT NULL DEFAULT '[]',
    block_deals_json TEXT NOT NULL DEFAULT '[]',
    promoter_json   TEXT NOT NULL DEFAULT '[]',
    insider_json    TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);

-- ── Reasoning traces (every LLM call) ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS reasoning_traces (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    loop                 TEXT NOT NULL,
    prompt_name          TEXT NOT NULL,
    prompt_version       INTEGER NOT NULL,
    model                TEXT NOT NULL,
    input_tokens         INTEGER NOT NULL DEFAULT 0,
    output_tokens        INTEGER NOT NULL DEFAULT 0,
    latency_ms           INTEGER NOT NULL DEFAULT 0,
    input_payload_json   TEXT NOT NULL,
    output_payload_json  TEXT,
    raw_llm_output       TEXT NOT NULL DEFAULT '',
    tool_calls_json      TEXT,
    status               TEXT NOT NULL DEFAULT 'OK',
    error_message        TEXT,
    linked_signal_id     INTEGER,
    linked_outcome_id    INTEGER,
    linked_revision_id   INTEGER,
    cost_inr             REAL NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);

CREATE INDEX IF NOT EXISTS idx_traces_loop_date ON reasoning_traces(loop, created_at);
CREATE INDEX IF NOT EXISTS idx_traces_status    ON reasoning_traces(status);

-- ── Signals ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signals (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date             TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    horizon                 TEXT NOT NULL,
    regime_id               INTEGER NOT NULL,
    rule_score              REAL NOT NULL,
    catalyst_score          REAL,
    final_score             REAL NOT NULL,
    verdict                 TEXT NOT NULL,
    entry_price             REAL NOT NULL,
    suggested_stop          REAL NOT NULL,
    suggested_t1            REAL,
    suggested_t2            REAL,
    suggested_qty           INTEGER NOT NULL,
    suggested_instrument    TEXT NOT NULL DEFAULT 'CASH',
    suggested_option_lots   INTEGER,
    suggested_option_expiry TEXT,
    reasoning_trace_id      INTEGER,
    breakdown_json          TEXT NOT NULL,
    flows_snapshot_id       INTEGER,
    supersedes              INTEGER,
    created_at              TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),
    FOREIGN KEY (regime_id)          REFERENCES regime_snapshots(id),
    FOREIGN KEY (flows_snapshot_id)  REFERENCES flows_snapshots(id),
    FOREIGN KEY (reasoning_trace_id) REFERENCES reasoning_traces(id),
    FOREIGN KEY (supersedes)         REFERENCES signals(id)
);

CREATE INDEX IF NOT EXISTS idx_signals_date_symbol ON signals(signal_date, symbol);
CREATE INDEX IF NOT EXISTS idx_signals_supersedes  ON signals(supersedes);

CREATE VIEW IF NOT EXISTS signals_current AS
SELECT s.* FROM signals s
WHERE NOT EXISTS (SELECT 1 FROM signals s2 WHERE s2.supersedes = s.id);

-- ── Fills ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER NOT NULL,
    fill_date       TEXT NOT NULL,
    fill_price      REAL NOT NULL,
    fill_qty        INTEGER NOT NULL,
    instrument      TEXT NOT NULL DEFAULT 'CASH',
    option_strike   REAL,
    option_expiry   TEXT,
    gross_value     REAL NOT NULL,
    cost_brokerage  REAL NOT NULL DEFAULT 0,
    cost_stt        REAL NOT NULL DEFAULT 0,
    cost_exchange   REAL NOT NULL DEFAULT 0,
    cost_gst        REAL NOT NULL DEFAULT 0,
    cost_slippage   REAL NOT NULL DEFAULT 0,
    cost_total      REAL NOT NULL DEFAULT 0,
    is_paper        INTEGER NOT NULL DEFAULT 1,
    kite_order_id   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),
    FOREIGN KEY (signal_id) REFERENCES signals(id)
);

CREATE INDEX IF NOT EXISTS idx_fills_signal ON fills(signal_id);

-- ── Outcomes ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS outcomes (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id                  INTEGER NOT NULL,
    outcome_date             TEXT NOT NULL,
    outcome_kind             TEXT NOT NULL,
    exit_price               REAL NOT NULL,
    days_held                INTEGER NOT NULL,
    gross_pnl_paise          INTEGER NOT NULL,
    costs_total_paise        INTEGER NOT NULL DEFAULT 0,
    net_pnl_paise            INTEGER NOT NULL,
    net_pnl_pct              REAL NOT NULL,
    max_favourable_excursion REAL,
    max_adverse_excursion    REAL,
    supersedes               INTEGER,
    created_at               TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),
    FOREIGN KEY (fill_id)    REFERENCES fills(id),
    FOREIGN KEY (supersedes) REFERENCES outcomes(id)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_fill ON outcomes(fill_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_date ON outcomes(outcome_date);

-- ── Lessons (LLM post-mortems) ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lessons (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    outcome_id          INTEGER NOT NULL,
    classification      TEXT NOT NULL,
    primary_factor      TEXT NOT NULL,
    lesson              TEXT NOT NULL,
    similar_pattern_id  INTEGER,
    suggests_revision   INTEGER NOT NULL DEFAULT 0,
    revision_hint       TEXT,
    reasoning_trace_id  INTEGER NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),
    FOREIGN KEY (outcome_id)          REFERENCES outcomes(id),
    FOREIGN KEY (similar_pattern_id)  REFERENCES lessons(id),
    FOREIGN KEY (reasoning_trace_id)  REFERENCES reasoning_traces(id)
);

CREATE INDEX IF NOT EXISTS idx_lessons_outcome ON lessons(outcome_id);
CREATE INDEX IF NOT EXISTS idx_lessons_pattern ON lessons(similar_pattern_id);

-- ── Revisions (weekly critic proposals) ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS revisions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    proposed_at          TEXT NOT NULL,
    week                 TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    target               TEXT NOT NULL,
    current_value        TEXT NOT NULL,
    proposed_value       TEXT NOT NULL,
    rationale            TEXT NOT NULL,
    expected_impact      TEXT NOT NULL,
    confidence           REAL NOT NULL,
    backtest_required    INTEGER NOT NULL DEFAULT 0,
    backtest_result      TEXT,
    status               TEXT NOT NULL DEFAULT 'PROPOSED',
    decided_by           TEXT,
    decided_at           TEXT,
    decision_reason      TEXT,
    applied_pr_url       TEXT,
    rolled_back_at       TEXT,
    reasoning_trace_id   INTEGER NOT NULL,
    created_at           TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),
    FOREIGN KEY (reasoning_trace_id) REFERENCES reasoning_traces(id)
);

CREATE INDEX IF NOT EXISTS idx_revisions_status ON revisions(status);
CREATE INDEX IF NOT EXISTS idx_revisions_week   ON revisions(week);

-- ── News + filings (cache) ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS news_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at    TEXT NOT NULL,
    published_at  TEXT,
    symbol        TEXT,
    sector        TEXT,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    snippet       TEXT,
    raw_html      TEXT,
    hash          TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_news_symbol_date ON news_items(symbol, published_at);

CREATE TABLE IF NOT EXISTS filings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at        TEXT NOT NULL,
    filed_at          TEXT,
    symbol            TEXT NOT NULL,
    exchange          TEXT NOT NULL,
    kind              TEXT NOT NULL,
    headline          TEXT NOT NULL,
    url               TEXT NOT NULL,
    extracted_summary TEXT
);

-- ── Audit ledger (append-only catch-all) ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at     TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),
    event_type   TEXT NOT NULL,
    severity     TEXT NOT NULL DEFAULT 'INFO',
    summary      TEXT NOT NULL,
    payload_json TEXT,
    linked_id    INTEGER,
    linked_table TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_type_date ON audit(event_type, event_at);
CREATE INDEX IF NOT EXISTS idx_audit_severity  ON audit(severity, event_at);
