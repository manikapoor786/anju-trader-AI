# Memory Schema — data/memory.db

> Last updated: 2026-05-13
> Status: Phase 0 design, not yet created

Single SQLite file. Append-only tables. The agent's brain state lives here.

---

## Design rules

1. **Append-only on all reasoning tables.** No UPDATEs on `signals`, `outcomes`, `reasoning_traces`, `audit`, `lessons`, `revisions`. Corrections insert a new row with `supersedes` FK to the prior row.
2. **One DB file**. Easy to commit to git for state persistence (matches anju-trader pattern).
3. **WAL mode** — `PRAGMA journal_mode=WAL` for concurrent reads while loops are writing.
4. **All timestamps in IST**, stored as ISO 8601 strings with `+05:30` offset. UTC conversion happens at query time if needed.
5. **All money in paise (int)** for `outcomes` P&L; rupees (float) elsewhere. Avoids float-precision bugs.
6. **No FK CASCADE deletes** — append-only means we never delete.

---

## Tables

### `signals`

Every candidate the morning scan promotes to "signal" gets one row.

```sql
CREATE TABLE signals (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date          TEXT NOT NULL,           -- 'YYYY-MM-DD'
    symbol               TEXT NOT NULL,
    horizon              TEXT NOT NULL,           -- 'SWING' | 'POSITIONAL'
    regime_id            INTEGER NOT NULL,        -- FK to regime_snapshots.id
    rule_score           REAL NOT NULL,           -- 0..100, from tools.scoring
    catalyst_score       REAL,                    -- -1..+1, from agent.catalyst_review (NULL if LLM step skipped)
    final_score          REAL NOT NULL,           -- combined score after all adjustments
    verdict              TEXT NOT NULL,           -- 'BUY' | 'WATCH' | 'AVOID'
    entry_price          REAL NOT NULL,           -- signal-time close
    suggested_stop       REAL NOT NULL,
    suggested_t1         REAL,
    suggested_t2         REAL,
    suggested_qty        INTEGER NOT NULL,        -- from position sizing
    suggested_instrument TEXT NOT NULL,           -- 'CASH' | 'ATM_CALL'
    suggested_option_lots INTEGER,                -- if ATM_CALL
    suggested_option_expiry TEXT,                 -- 'YYYY-MM-DD' if ATM_CALL
    reasoning_trace_id   INTEGER,                 -- FK to reasoning_traces.id (NULL if rule-only)
    breakdown_json       TEXT NOT NULL,           -- full scoring breakdown
    flows_snapshot_id    INTEGER,                 -- FK to flows_snapshots.id
    supersedes           INTEGER,                 -- FK to signals.id (this row corrects an earlier one)
    created_at           TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);

CREATE INDEX idx_signals_date_symbol ON signals(signal_date, symbol);
CREATE INDEX idx_signals_supersedes  ON signals(supersedes);
```

Convenience view of the "current truth" for each signal:
```sql
CREATE VIEW signals_current AS
SELECT s.* FROM signals s
WHERE NOT EXISTS (
    SELECT 1 FROM signals s2 WHERE s2.supersedes = s.id
);
```

---

### `fills`

When a signal gets paper-filled (or live-filled in Phase 4+).

```sql
CREATE TABLE fills (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id         INTEGER NOT NULL,           -- FK to signals.id
    fill_date         TEXT NOT NULL,              -- when the fill happened (usually signal_date + 1 open)
    fill_price        REAL NOT NULL,              -- actual fill price (with modelled slippage)
    fill_qty          INTEGER NOT NULL,
    instrument        TEXT NOT NULL,              -- 'CASH' | 'ATM_CALL'
    option_strike     REAL,                       -- if option
    option_expiry     TEXT,                       -- if option
    gross_value       REAL NOT NULL,              -- price * qty
    cost_brokerage    REAL NOT NULL,              -- ₹
    cost_stt          REAL NOT NULL,
    cost_exchange     REAL NOT NULL,
    cost_gst          REAL NOT NULL,
    cost_slippage     REAL NOT NULL,              -- modelled
    cost_total        REAL NOT NULL,
    is_paper          INTEGER NOT NULL,           -- 1 = paper, 0 = live
    kite_order_id     TEXT,                       -- NULL if paper
    created_at        TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);

CREATE INDEX idx_fills_signal ON fills(signal_id);
```

---

### `outcomes`

When a position closes (event-driven: first day H ≥ T1, or L ≤ stop, or LLM-suggested EXIT).

```sql
CREATE TABLE outcomes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id             INTEGER NOT NULL,         -- FK to fills.id
    outcome_date        TEXT NOT NULL,
    outcome_kind        TEXT NOT NULL,            -- 'WIN_T1' | 'WIN_T2' | 'LOSS_STOP' | 'TIME_EXIT' | 'AGENT_EXIT'
    exit_price          REAL NOT NULL,
    days_held           INTEGER NOT NULL,         -- trading days
    gross_pnl_paise     INTEGER NOT NULL,         -- (exit - entry) * qty in paise
    costs_total_paise   INTEGER NOT NULL,
    net_pnl_paise       INTEGER NOT NULL,         -- gross - costs
    net_pnl_pct         REAL NOT NULL,
    max_favourable_excursion REAL,                -- biggest unrealised gain during hold
    max_adverse_excursion    REAL,                -- biggest unrealised loss during hold
    supersedes          INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);

CREATE INDEX idx_outcomes_fill ON outcomes(fill_id);
CREATE INDEX idx_outcomes_date ON outcomes(outcome_date);
```

---

### `lessons`

LLM-generated post-mortems linked to outcomes.

```sql
CREATE TABLE lessons (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    outcome_id          INTEGER NOT NULL,         -- FK to outcomes.id
    classification      TEXT NOT NULL,            -- see AGENT_PROTOCOL §2.2
    primary_factor      TEXT NOT NULL,
    lesson              TEXT NOT NULL,            -- the 1–2 sentence lesson
    similar_pattern_id  INTEGER,                  -- FK to lessons.id (this echoes an earlier lesson)
    suggests_revision   INTEGER NOT NULL,         -- 0 | 1
    revision_hint       TEXT,
    reasoning_trace_id  INTEGER NOT NULL,         -- FK to reasoning_traces.id
    created_at          TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);

CREATE INDEX idx_lessons_outcome ON lessons(outcome_id);
CREATE INDEX idx_lessons_pattern ON lessons(similar_pattern_id);
```

---

### `revisions`

Proposed changes from the weekly critic. The audit trail of how the system evolved.

```sql
CREATE TABLE revisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    proposed_at         TEXT NOT NULL,
    week                TEXT NOT NULL,            -- 'YYYY-WW'
    kind                TEXT NOT NULL,            -- 'PARAMETER' | 'WEIGHT' | 'FILTER' | 'NEW_RULE'
    target              TEXT NOT NULL,            -- dotted path e.g. 'tools.scoring.MIN_BASE_SCORE'
    current_value       TEXT NOT NULL,
    proposed_value      TEXT NOT NULL,
    rationale           TEXT NOT NULL,
    expected_impact     TEXT NOT NULL,
    confidence          REAL NOT NULL,
    backtest_required   INTEGER NOT NULL,
    backtest_result     TEXT,                     -- JSON, populated after backtest if required
    status              TEXT NOT NULL,            -- 'PROPOSED' | 'BACKTESTING' | 'AWAITING_APPROVAL' | 'APPROVED' | 'REJECTED' | 'APPLIED' | 'ROLLED_BACK'
    decided_by          TEXT,                     -- 'MANISH' | 'AUTO_REJECTED'
    decided_at          TEXT,
    decision_reason     TEXT,
    applied_pr_url      TEXT,                     -- GitHub PR URL once applied
    rolled_back_at      TEXT,
    reasoning_trace_id  INTEGER NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);

CREATE INDEX idx_revisions_status ON revisions(status);
CREATE INDEX idx_revisions_week   ON revisions(week);
```

---

### `reasoning_traces`

Every LLM call gets one row. The audit foundation.

```sql
CREATE TABLE reasoning_traces (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    loop                TEXT NOT NULL,            -- 'catalyst_review' | 'post_mortem' | ...
    prompt_name         TEXT NOT NULL,
    prompt_version      INTEGER NOT NULL,
    model               TEXT NOT NULL,            -- 'gemini-1.5-flash' | 'claude-sonnet-4-6' | ...
    input_tokens        INTEGER NOT NULL,
    output_tokens       INTEGER NOT NULL,
    latency_ms          INTEGER NOT NULL,
    input_payload_json  TEXT NOT NULL,            -- the validated input (Pydantic JSON)
    output_payload_json TEXT,                     -- the parsed output (NULL if parse_error)
    raw_llm_output      TEXT NOT NULL,            -- model text before parsing
    tool_calls_json     TEXT,                     -- list of tool calls + results
    status              TEXT NOT NULL,            -- 'OK' | 'PARSE_ERROR' | 'TIMEOUT' | 'RATE_LIMITED' | 'BUDGET_EXCEEDED'
    error_message       TEXT,
    linked_signal_id    INTEGER,
    linked_outcome_id   INTEGER,
    linked_revision_id  INTEGER,
    cost_inr            REAL NOT NULL DEFAULT 0,  -- estimated ₹ cost of this call
    created_at          TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);

CREATE INDEX idx_traces_loop_date ON reasoning_traces(loop, created_at);
CREATE INDEX idx_traces_status    ON reasoning_traces(status);
```

---

### `audit`

The catch-all ledger. Every meaningful action writes here.

```sql
CREATE TABLE audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at        TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),
    event_type      TEXT NOT NULL,                -- 'SIGNAL_GENERATED' | 'FILL' | 'OUTCOME' | 'LESSON' | 'REVISION_PROPOSED' | 'REVISION_APPROVED' | 'REVISION_APPLIED' | 'ANOMALY' | 'WORKFLOW_RUN' | 'CONFIG_CHANGE' | ...
    severity        TEXT NOT NULL,                -- 'INFO' | 'WARN' | 'CRITICAL'
    summary         TEXT NOT NULL,
    payload_json    TEXT,                         -- structured details
    linked_id       INTEGER,                      -- generic FK based on event_type
    linked_table    TEXT
);

CREATE INDEX idx_audit_type_date ON audit(event_type, event_at);
CREATE INDEX idx_audit_severity  ON audit(severity, event_at);
```

---

### `regime_snapshots`

Daily classification — referenced by every signal so we know the regime when the signal fired.

```sql
CREATE TABLE regime_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL UNIQUE,
    state           TEXT NOT NULL,                -- 'Trending' | 'Sideways' | 'Volatile' | 'Bear'
    min_score       INTEGER NOT NULL,
    nifty_close     REAL NOT NULL,
    nifty_ma20      REAL,
    nifty_ma50      REAL,
    nifty_ma200     REAL,
    vix_close       REAL,
    breadth_pct     REAL,                         -- % of Nifty 50 above MA50
    vol_10d_pct     REAL,
    payload_json    TEXT NOT NULL,                -- full regime detector output
    created_at      TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);
```

---

### `flows_snapshots`

Daily institutional flow snapshot. Each signal references one.

```sql
CREATE TABLE flows_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL UNIQUE,
    fii_cash_cr     REAL,                         -- ₹ crore, net buy positive
    dii_cash_cr     REAL,
    fii_futures_cr  REAL,
    fii_options_cr  REAL,
    bulk_deals_json TEXT NOT NULL,                -- list of bulk deals today
    block_deals_json TEXT NOT NULL,
    promoter_json   TEXT NOT NULL,                -- SAST disclosures today
    insider_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', '+05:30'))
);
```

Per-symbol flow features (computed on demand from this table) are joined at scoring time.

---

### `news_items` and `filings`

Cached news/filings so the catalyst LLM doesn't re-fetch.

```sql
CREATE TABLE news_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at    TEXT NOT NULL,
    published_at  TEXT,
    symbol        TEXT,                           -- NULL if sector/macro
    sector        TEXT,
    source        TEXT NOT NULL,                  -- 'MoneyControl' | 'ET' | 'Mint' | ...
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    snippet       TEXT,
    raw_html      TEXT,                           -- archived for replay
    hash          TEXT UNIQUE                     -- dedupe key
);

CREATE INDEX idx_news_symbol_date ON news_items(symbol, published_at);

CREATE TABLE filings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at    TEXT NOT NULL,
    filed_at      TEXT,
    symbol        TEXT NOT NULL,
    exchange      TEXT NOT NULL,                  -- 'NSE' | 'BSE'
    kind          TEXT NOT NULL,                  -- 'RESULTS' | 'BOARD_MEETING' | 'CORPORATE_ACTION' | 'REGULATORY' | ...
    headline      TEXT NOT NULL,
    url           TEXT NOT NULL,
    extracted_summary TEXT                        -- LLM-extracted summary if processed
);
```

---

## Schema versioning

The DB has one extra table for migrations:

```sql
CREATE TABLE schema_versions (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now', '+05:30')),
    notes      TEXT
);
```

Migrations live as ordered SQL files under `anju_ai/memory/migrations/`:
```
001_initial.sql
002_add_options_columns.sql
003_add_news_filings.sql
...
```

`anju_ai/memory/db.py:apply_migrations()` runs missing ones at startup.

---

## Storage estimates (Year 1 of operation)

Rough projections:

| Table | Rows/year | Avg row size | Total |
|---|---|---|---|
| `signals` | ~3,500 | 2 KB | 7 MB |
| `fills` | ~1,000 | 1 KB | 1 MB |
| `outcomes` | ~1,000 | 1 KB | 1 MB |
| `lessons` | ~1,000 | 2 KB | 2 MB |
| `revisions` | ~50 | 4 KB | 200 KB |
| `reasoning_traces` | ~10,000 | 8 KB | 80 MB |
| `audit` | ~50,000 | 0.5 KB | 25 MB |
| `regime_snapshots` | 250 | 2 KB | 500 KB |
| `flows_snapshots` | 250 | 50 KB | 12 MB |
| `news_items` | ~50,000 | 5 KB | 250 MB |
| `filings` | ~10,000 | 3 KB | 30 MB |

**Total Year 1: ~410 MB.** SQLite handles this comfortably. Git pushes of `memory.db` will work but get slow — Phase 3 will likely move `news_items` and `filings` (the bulk) to a separate `data/news.db` that doesn't get committed.

---

## Reading the DB from the phone

Phase 3 plan: a `scripts/memory_summary.py` workflow that, on `workflow_dispatch`, sends a Telegram message with key counts and recent rows:

```
📊 memory.db status (as of 13 May 2026 22:00 IST)
  Signals: 3,247 total  (12 today, 47 this week)
  Open positions: 9
  Closed outcomes: 218  (62% wins, +0.94% expectancy/trade)
  Lessons: 218  (8 patterns appearing >5x)
  Revisions: PROPOSED 1, APPROVED 11, APPLIED 11
  Last reasoning trace: 14 sec ago (catalyst_review on AXISBANK)
```

You read this once a day. If anything looks off, you trigger `manual_audit.yml` for details.
