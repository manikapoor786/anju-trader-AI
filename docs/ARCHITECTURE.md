# Architecture — anju-trader-AI

> Last updated: 2026-05-13
> Status: Phase 0 (design only — no executable code yet)

This document describes the **complete system** before any line of executable code is written. If the design is sound on paper, the implementation is mechanical. If the design has holes, no amount of coding fixes them.

---

## 1. Design principles (non-negotiable)

These are the lenses through which every implementation decision gets evaluated.

| # | Principle | What it means in practice |
|---|---|---|
| 1 | **Memory is the database, not the code.** | Every signal, fill, outcome, observation, lesson is a structured row in `memory.db`. The agent reasons over this DB, not over scraped state. |
| 2 | **Tools are pure functions.** | The LLM agent calls tools the same way it uses tool-use: `score_signal(sym, df) → ScoreResult`. No globals, no side effects beyond logging. |
| 3 | **Reasoning is structured.** | Every LLM call has a typed input schema and a typed output schema. No free-form prose that we then parse fragilely. |
| 4 | **Every decision is auditable.** | Every signal, every position-sizing call, every exit has a `reasoning_id` linking to the inputs, the rules fired, the LLM tokens, and the final action. |
| 5 | **Cost is a first-class metric.** | All expectancy numbers are post-cost. Pre-cost numbers are not shown anywhere — they lie. |
| 6 | **The agent never trades without human approval in Phase 0–2.** | Paper portfolio only. Real money only after the success criteria in ROADMAP are met. |
| 7 | **Self-modification requires human approval forever.** | The weekly critic can propose code/parameter changes. Manish approves via Telegram. No silent self-mutation. |
| 8 | **Free until proven.** | LLM tier = Gemini Flash (free) by default. Claude only for weekly critic when daily usage stays in free tier. No paid LLM call without a corresponding revenue justification. |
| 9 | **Reproducibility over speed.** | Every signal generation can be replayed bit-for-bit from `memory.db`. Caching is encouraged; non-determinism is forbidden in scoring. |
| 10 | **Phone-first operability.** | Every workflow has `workflow_dispatch`. Every alert has actionable Telegram replies. You can run the entire system from a phone. |

---

## 2. The layered model

```
┌──────────────────────────────────────────────────────────────────┐
│                       Layer 6 — TELEGRAM I/O                      │
│  Daily digest · Intraday alerts · /approve_<id> · /reject_<id>    │
├──────────────────────────────────────────────────────────────────┤
│                       Layer 5 — REASONING LOOPS                   │
│  morning · intraday · eod_postmortem · weekly_critic · anomaly_qa │
├──────────────────────────────────────────────────────────────────┤
│                       Layer 4 — AGENT ORCHESTRATOR                │
│  Prompt assembly · Tool selection · Reasoning trace · Audit log   │
├──────────────────────────────────────────────────────────────────┤
│                       Layer 3 — TOOLS (pure functions)            │
│  score · size · cost · outcome · flows · catalyst · options · bt  │
├──────────────────────────────────────────────────────────────────┤
│                       Layer 2 — MEMORY                            │
│  signals · fills · outcomes · lessons · revisions · audit         │
├──────────────────────────────────────────────────────────────────┤
│                       Layer 1 — DATA                              │
│  bhavcopy · yfinance · NSE flows · news/RSS · Kite ticks (opt)    │
└──────────────────────────────────────────────────────────────────┘
```

A clean layering rule: **lower layers never import higher layers.** Layer 3 tools never call the LLM. Only Layer 4 (agent) talks to the LLM. This keeps tools deterministic and testable.

---

## 3. Layer-by-layer detail

### Layer 1 — Data sources (all free in Phase 0–2)

| Source | What we get | Cadence | Cost |
|---|---|---|---|
| NSE bhavcopy | EOD OHLCV + delivery % for all NSE stocks | Daily 6 PM IST | Free |
| NSE FII/DII | Daily cash + derivatives net activity | Daily 6 PM IST | Free |
| NSE bulk/block deals | Large trades with counterparties | Daily | Free |
| BSE/NSE announcements | Corporate filings, results, regulatory | Real-time | Free |
| BSE/NSE SAST | Insider + promoter transactions | T+1 | Free |
| yfinance | Backup OHLCV + fundamentals + earnings calendar | On demand | Free |
| RSS news (MoneyControl, Economic Times, Mint) | Stock + sector + macro news | Hourly | Free |
| Kite Connect ticks | Intraday tick data | Real-time | Free if Kite-subscribed |

All data lands in `data/historical.db` (OHLCV) and `data/flows.db` (everything else). One file each — easy to inspect, version, and reset.

### Layer 2 — Memory (the agent's brain state)

Single SQLite file: `data/memory.db`. Schemas in [MEMORY_SCHEMA.md](MEMORY_SCHEMA.md).

```
memory.db
├── signals          One row per signal generated, with full inputs + score breakdown
├── fills            Hypothetical (paper) or real fills with timestamps + costs
├── outcomes         Closed-trade outcomes (WIN/LOSS/EXIT) — event-driven
├── lessons          LLM-generated post-mortems linked to outcomes
├── revisions        Proposed changes from weekly critic + approval status
├── reasoning_traces One row per LLM call with input prompt, output, tokens, latency
└── audit            Every meaningful action — append-only ledger
```

Key invariant: **never UPDATE rows in `signals`, `outcomes`, `reasoning_traces`, or `audit`.** Append-only. Corrections go in as new rows with a `supersedes` foreign key. This makes the system fully replayable.

### Layer 3 — Tools (pure functions, no LLM)

Located in `anju_ai/tools/`. Each tool has:
- A typed input dataclass
- A typed output dataclass
- A unit test (mandatory)
- A docstring telling the LLM agent when to use it

```python
# Example: anju_ai/tools/scoring.py
@dataclass
class ScoreInput:
    symbol: str
    df: pd.DataFrame
    regime: Regime
    flows: FlowsSnapshot

@dataclass
class ScoreResult:
    score: float           # 0–100
    breakdown: dict        # which features contributed how much
    verdict: Verdict       # BUY / WATCH / AVOID
    reasoning: str         # human-readable explanation
    confidence: float      # 0–1, the model's own confidence

def score_signal(inp: ScoreInput) -> ScoreResult: ...
```

The set of tools (Phase 1–3):

| Tool | Purpose |
|---|---|
| `score_signal` | Compute composite score for one symbol |
| `compute_position_size` | Kelly-fraction × score × regime × correlation |
| `apply_costs` | Subtract round-trip cost from any P&L number |
| `track_outcome` | Event-driven first-touch detection (stop or target) |
| `fetch_flows` | FII/DII + bulk/block + promoter for a symbol or date |
| `score_catalyst` | LLM-graded news catalyst (bullish/bearish + confidence) |
| `evaluate_options_leverage` | Should this signal use ATM call instead of cash? |
| `run_backtest` | Replay scoring on historical data with full cost model |
| `detect_regime` | Trending / Sideways / Volatile / Bear |
| `correlation_check` | Is this new signal correlated with open positions? |
| `paper_fill` | Simulate a fill with slippage modelled per universe segment |
| `tax_lot_status` | LTCG/STCG status of open positions |

**Forbidden in tools**: any LLM call, any Telegram send, any GitHub Actions trigger. Tools are pure compute + DB I/O.

### Layer 4 — Agent orchestrator

This is where the LLM lives. See [AGENT_PROTOCOL.md](AGENT_PROTOCOL.md) for the full protocol.

In short: the agent gets a **task**, a **toolbox**, and access to **memory**. It plans, calls tools, observes results, writes its reasoning to `reasoning_traces`, and produces a typed output. It does not have side effects beyond logging — actions (sending Telegram, writing signals) are taken by the calling loop based on the agent's typed output.

### Layer 5 — Reasoning loops

The cadenced jobs that drive the system. Each one is a GitHub Actions workflow.

```
morning.yml          6:30 AM IST    Daily scan + signal generation
intraday.yml         every 30 min   Open-position monitor (mkt hours only)
eod.yml              4:00 PM IST    Outcome closure + post-mortem LLM
weekly_critic.yml    Sun 9 AM IST   Strategy critic + revision proposals
anomaly_qa.yml       every 3 hours  Data freshness, regime sanity, error detection
```

Plus manual:

```
manual_scan.yml          On-demand scan with custom universe/threshold
manual_review.yml        Deep LLM review of one symbol
manual_backtest.yml      Backtest a strategy + parameters
manual_paper_book.yml    Paper portfolio snapshot to Telegram
manual_compare.yml       A/B vs anju-trader for any date range
kite_login.yml           Daily Kite auth refresh
```

### Layer 6 — Telegram I/O

Two-way. The system pushes (digests, alerts, reasoning summaries). You pull (replies that trigger workflows).

Outbound:
- Morning digest (signals + reasoning)
- Intraday alerts (SL/target hit, regime change, anomaly)
- EOD report (today's outcomes, P&L, lessons learned)
- Weekly critic (proposed revisions with `/approve_<id>` `/reject_<id>`)
- A/B comparison (every Sunday: anju-trader vs anju-trader-AI)

Inbound (via a slim webhook handler that triggers GH Actions workflows):
- `/scan <universe>` → manual_scan.yml
- `/review <symbol>` → manual_review.yml
- `/book` → manual_paper_book.yml
- `/approve_<revision_id>` → applies a critic proposal
- `/reject_<revision_id>` → rejects it (with optional reason)
- `/compare <days>` → manual_compare.yml

---

## 4. The data flow for one signal (concrete example)

What happens at 6:30 AM IST when `morning.yml` fires?

```
1. ACTIONS RUNNER STARTS
   └─→ checkout, install deps, load secrets

2. DATA REFRESH                                          [Layer 1]
   ├─→ data_layer.refresh_bhavcopy()         (1 NSE ZIP)
   ├─→ flows.refresh_fii_dii()                (1 NSE API)
   ├─→ flows.refresh_bulk_block_deals()       (1 NSE API)
   ├─→ news.refresh_rss_feeds()               (~10 RSS pulls)
   └─→ all writes to data/historical.db + data/flows.db

3. REGIME DETECTION                                       [Layer 3]
   └─→ tools.detect_regime() → Regime(state="Trending", min_score=6)

4. UNIVERSE SCAN                                          [Layer 3]
   ├─→ for each of ~500 symbols:
   │     ├─→ tools.score_signal(sym, df, regime, flows) → ScoreResult
   │     └─→ if score ≥ regime.min_score: candidates.append(...)
   └─→ candidates = ~30–50 symbols

5. CATALYST AUGMENT (LLM, agentic)                        [Layer 4]
   For each candidate:
   ├─→ agent.review_catalyst(symbol, news_24h, filings_7d)
   │     • LLM input: structured news + filings + scoring breakdown
   │     • LLM output: CatalystScore(bullish/neutral/bearish, confidence, reasoning)
   ├─→ adjust candidate.score by catalyst_score × catalyst_weight
   └─→ write reasoning_trace row

6. POSITION SIZING                                        [Layer 3]
   For top 15 by adjusted score:
   ├─→ tools.compute_position_size(score, regime, open_positions, capital)
   └─→ tools.evaluate_options_leverage(symbol, iv_percentile, conviction)
       → returns ('cash', qty) or ('atm_call', lots, expiry)

7. SIGNAL ASSEMBLY                                        [Layer 2]
   ├─→ memory.signals.insert(signal_id, symbol, score, sizing,
   │                          reasoning_trace_id, regime, flows_snapshot_id)
   └─→ memory.audit.insert(event="signal_generated", ...)

8. PAPER FILL                                             [Layer 3]
   ├─→ tools.paper_fill(signal_id) at next-day open + modelled slippage
   └─→ memory.fills.insert(fill_id, signal_id, price, qty, cost)

9. TELEGRAM DIGEST                                        [Layer 6]
   └─→ tg.digest.send(today_signals, regime, reasoning_summary)

10. WORKFLOW COMMIT                                       [Layer 1]
    └─→ git add data/memory.db data/flows.db && git commit && git push
        (state persists across runs via repo, same as anju-trader)
```

Every step is logged. If the digest looks wrong at 7 AM, you can `SELECT * FROM reasoning_traces WHERE created_at >= '06:30'` and see exactly what the agent was thinking.

---

## 5. The cost-of-being-wrong analysis

For each design decision, we asked: *what's the cost if we're wrong?*

| Decision | If wrong, cost is | Mitigation |
|---|---|---|
| Outcome tracking = event-driven | Late or wrong WIN/LOSS labels | Unit tests + replay on anju-trader history |
| Scoring weights from backtest | Overfit to past, fails forward | Walk-forward CV, out-of-sample holdout |
| Free LLM (Gemini) | Quality below Claude | Fallback adapter, switch when usage justifies |
| GitHub Actions only | Workflow downtime | All workflows idempotent, can replay missed |
| Append-only memory | DB grows large | Compaction script monthly; SQLite handles 10s of GB fine |
| Human-approved revisions | Slow improvement velocity | Acceptable — never silent mutation |
| Paper-only Phase 0–2 | Slow capital growth | Acceptable — proves system first |

---

## 6. The cost-of-being-right analysis

For each design decision, the upside if it works:

| Decision | If right, value is |
|---|---|
| Memory-as-database | Replayable backtest of past decisions → continuous improvement |
| Agentic LLM in the loop | Catches catalysts and patterns rules miss |
| Walk-forward optimised weights | Survives regime change |
| F&O leverage layer | Adds 3–5x leverage cleanly when conditions are right |
| Phone-controllable | You can run a hedge fund from Goa |
| LLM post-mortems | System learns from every loss instead of repeating it |
| Auditable reasoning | You can trust it because you can verify it |

---

## 7. What this architecture explicitly does NOT do

Boundaries matter. The system will not:

- **Predict the market**. It identifies setups with positive expectancy after costs.
- **Trade futures or options outright**. Only as cash-equivalent leverage on stock signals.
- **Short stocks via SLB**. Only F&O shorts in bear regimes.
- **Trade pre-market or post-market sessions**. Liquidity too thin.
- **Trade SME/B-group/penny names**. Liquidity + manipulation risk too high.
- **Auto-execute in Phase 0–2**. Paper only. You manually place trades from the digest.
- **Mutate its own code without approval**. Every change is a PR you click approve on.
- **Use leverage beyond 3x effective exposure**. Hard cap regardless of conviction.
- **Hold more than 15 positions** or fewer than 5. Concentration band enforced.

These boundaries are encoded as runtime asserts. The system refuses to violate them.

---

## 8. Open design questions (for Manish to weigh in on)

These need your input before we implement:

1. **Maximum allowable drawdown**: above what % monthly DD does the agent automatically reduce position sizes? My default: 8%.
2. **Cash position cap**: what % of capital can be in cash during a Bear regime? My default: 70% (i.e. min 30% allocated to defensives).
3. **F&O leverage cap**: what's the max effective leverage? My default: 3x notional exposure.
4. **Approval mode**: do you want to approve **every** signal in Phase 0–2, or only weekly-critic-proposed code changes? My default: only critic changes; signals auto-paper-fill.
5. **A/B exit criterion**: when do we kill anju-trader? My default: 60 days where anju-trader-AI's cost-adjusted expectancy > 1.3× anju-trader's, with ≥40 closed signals.
6. **GitHub repo visibility**: public or private? Public = unlimited Actions minutes free, but exposes strategy. My recommendation: **public** until live capital is deployed, then private.

---

## 9. What's next

Once you've read this doc, [AGENT_PROTOCOL.md](AGENT_PROTOCOL.md), [ROADMAP.md](ROADMAP.md), and [DECISIONS.md](DECISIONS.md) and signed off, the implementation order is:

1. **Phase 0 (week 1)**: scaffold + forked primitives + paper portfolio + first end-to-end signal
2. **Phase 1 (weeks 2–4)**: outcome tracker + cost model + backtest validation
3. **Phase 2 (weeks 5–10)**: flows + catalyst LLM + F&O layer + concentration
4. **Phase 3 (weeks 11–16)**: agentic loops (post-mortem, critic, anomaly QA)
5. **Phase 4 (week 17+)**: cutover

Approximate effort: **4 months to live cutover** under realistic effort.
