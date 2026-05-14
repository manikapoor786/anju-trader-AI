# Roadmap — anju-trader-AI

> Last updated: 2026-05-13
> Current phase: **Phase 0 (Scaffolding)**

A living document. Updated at the end of every phase. Each phase has a
**Definition of Done** that must pass before moving on.

---

## Phase 0 — Scaffolding (Week 1)

**Goal**: project structure exists, docs are written, first end-to-end signal flows through a paper portfolio. No intelligence yet — proving the plumbing works.

### Tasks

| # | Task | Status | Notes |
|---|---|---|---|
| 0.1 | Project directory + git init | ✅ Done | 2026-05-13 |
| 0.2 | README.md | ✅ Done | 2026-05-13 |
| 0.3 | docs/ARCHITECTURE.md | ✅ Done | 2026-05-13 |
| 0.4 | docs/AGENT_PROTOCOL.md | ✅ Done | 2026-05-13 |
| 0.5 | docs/ROADMAP.md (this file) | ✅ Done | 2026-05-13 |
| 0.6 | docs/DECISIONS.md (ADR 001–005) | ✅ Done | 2026-05-13 |
| 0.7 | docs/MEMORY_SCHEMA.md | ✅ Done | 2026-05-13 |
| 0.8 | requirements.txt + .gitignore | ✅ Done | 2026-05-13 |
| 0.9 | config/runtime.yaml + config/strategies.yaml + config/llm.yaml + config/universe.yaml | ✅ Done | 2026-05-13 |
| 0.10 | Skeleton package structure (`anju_core/`, `anju_ai/` + `__init__.py`) | ✅ Done | 2026-05-13 |
| 0.11 | GitHub workflow stubs (morning.yml, manual_scan.yml, kite_login.yml) | ✅ Done | 2026-05-13 |
| 0.12 | First commit + push to GitHub | ⏳ Pending Manish creates repo |
| 0.13 | Fork `data_layer.py` from anju-trader into `anju_core/data_layer.py` | ✅ Done | 2026-05-13 · 12 unit tests, CI workflow added |
| 0.14 | Fork `regime_detector.py` into `anju_core/regime.py` | ✅ Done | 2026-05-14 · 12 unit tests on _classify logic |
| 0.15a | Fork stockview indicators → `anju_core/indicators.py` | ✅ Done | 2026-05-14 · 13 unit tests on synthetic data |
| 0.15b | Port scoring engine with Pydantic types → `anju_ai/tools/scoring.py` | ✅ Done | 2026-05-14 · 8 unit tests · audit-invariant breakdown sums to score |
| 0.16 | `anju_ai/tools/paper_fill.py` with slippage model | ✅ Done | 2026-05-14 · 6 unit tests · segment + size-impact scaling |
| 0.17 | `anju_ai/memory/db.py` + schema migrations | ✅ Done | 2026-05-14 · 7 unit tests · 12 tables + signals_current view + audit ledger |
| 0.18 | Wire `anju_ai/loops/morning_scan.py` end-to-end (rule-based only, no LLM) | ✅ Done | 2026-05-14 · 7-step pipeline · parallel scan · regime/signals/fills persisted · digest |
| 0.19 | First Telegram digest from anju-trader-AI to a *new* Telegram chat | ✅ Done | 2026-05-14 · part of 0.18 — header + top N with reasoning |
| 0.20 | A/B comparison workflow stub | ✅ Done | 2026-05-14 · Sunday cron + manual dispatch · reads memory.db for AI side |

### Definition of Done

- Project pushes to GitHub
- `manual_scan.yml` workflow can be triggered from the GitHub mobile app
- A signal is produced, paper-filled, written to `data/memory.db`, and a Telegram digest arrives
- All written content (signals + outcomes + reasoning_traces with empty LLM payloads) is replayable from `memory.db`
- Zero LLM calls yet — rule-based throughput proven first

**Estimated effort**: 5 working days
**Cost**: ₹0

---

## Phase 1 — Tier 1 fixes (Weeks 2–4)

**Goal**: fix the three things the v2 audit identified as broken. Validate that the rule-based system actually has positive cost-adjusted expectancy.

### Tasks

| # | Task | Status | Notes |
|---|---|---|---|
| 1.1 | `anju_ai/tools/outcome_tracker.py` — event-driven (first-touch detection on daily H/L) | ✅ Done | 2026-05-14 · 16 unit tests · gap handling · MFE/MAE · idempotent loop · eod_close.yml + backfill_history.yml |
| 1.2 | `anju_ai/tools/costs.py` — full Indian retail cost model | ✅ Done | 2026-05-14 · 16 unit tests · wired into outcome closure · brokerage+STT+slippage+GST+stamp+SEBI+exchange |
| 1.3 | `anju_ai/tools/backtest.py` — walks daily through historical.db, applies scoring + costs + outcome rules | ✅ Done | 2026-05-14 · 10 unit tests · walk-forward · run namespacing via backtest_runs table |
| 1.4 | Backtest report: win-rate, expectancy, max DD, Sharpe by score bucket × regime × universe segment | ✅ Done | 2026-05-14 · part of 1.3 — render_report + verdict + Telegram HTML |
| 1.5 | Cut any score bucket with cost-adjusted expectancy ≤ 0 | ⏳ | |
| 1.6 | Survivorship-bias-clean universe loader (delisted stocks included) | ✅ Done | 2026-05-14 · 4 unit tests · get_universe_at_date filters via bhavcopy availability · memoised cache helper for backtest |
| 1.7 | Unit tests for outcome_tracker, costs, backtest | ⏳ | Mandatory |
| 1.8 | Replay anju-trader's last 30 days through anju-trader-AI rule engine → comparison report | ⏳ | |

### Definition of Done

- A backtest of the rule-based scoring on 2 years of survivorship-clean data shows:
  - Cost-adjusted expectancy > 0.5% per trade for the kept score buckets
  - Win rate > 50% on score-30+ signals
  - Max DD < 15% on the in-sample period
  - Out-of-sample (last 6 months held out) doesn't degrade by more than 20%
- Outcome tracker correctly classifies WIN/LOSS for ≥95% of test trades when replayed
- Cost model error vs Zerodha actual statements within ±10% on 20 sample trades

If backtest fails to clear these bars, **we stop and rethink the scoring before adding any LLM intelligence**. There's no point putting an LLM on top of a negative-expectancy base.

**Estimated effort**: 12 working days
**Cost**: ₹0

---

## Phase 2 — Edge layer (Weeks 5–10)

**Goal**: add the inputs that institutional traders have and you don't — flows, catalysts, options. Concentrate the portfolio.

### Tasks

| # | Task | Status | Notes |
|---|---|---|---|
| 2.1 | `anju_ai/tools/flows.py` — FII/DII daily ingest from NSE | ✅ Done | 2026-05-14 · 15 unit tests · UPSERT into flows_snapshots · wired into morning_scan refresh · passive collection (not yet scoring input) |
| 2.2 | `anju_ai/tools/deals.py` — bulk + block deals daily ingest | ✅ Done | 2026-05-14 · 20 unit tests · JSON merge with dedupe · wired into morning_scan · passive collection |
| 2.3 | `anju_ai/tools/insider.py` — promoter + insider (SAST) ingest | ✅ Done | 2026-05-14 · 19 unit tests · grouped-by-date persistence · insider_signal_for_symbol aggregator · wired into morning_scan |
| 2.4 | Backtest flows as scoring features → measure expectancy lift | ⏳ | |
| 2.5 | `anju_ai/tools/catalyst.py` + LLM client (Gemini Flash) — daily news/filings scan per candidate | ✅ Done | 2026-05-14 · 15 unit tests · prompt v1 · apply_catalyst_to_score with weight=0 calibration default |
| 2.6 | Wire `agent.catalyst_review` loop (see AGENT_PROTOCOL §2.1) | ⏳ | First real LLM use |
| 2.7 | `anju_ai/tools/options.py` — fetch IV percentile, recommend ATM calls when conviction high + IV low | ✅ Done | 2026-05-14 · 24 unit tests · iv_history migration 003 · evaluate_leverage gated by fno_enabled flag |
| 2.8 | Concentration enforcer: max 15, min 5 positions; pyramiding on HERO | ✅ Done | 2026-05-14 · 13 unit tests · enforce_concentration with NEW_OPEN/PYRAMID/SKIP actions · config-driven via ConcentrationConfig |
| 2.9 | `anju_ai/loops/intraday_monitor.py` — every 30 min mkt hours | ✅ Done | 2026-05-14 · 13 unit tests · classify_alert routing (CRITICAL/WARN/INFO) · is_market_open + intraday.yml cron 15,45 4-9 UTC |
| 2.10 | `anju_ai/tools/correlation.py` — penalise sizing when new signal correlates with open positions | ✅ Done | 2026-05-14 · 18 unit tests · pairwise corr matrix · portfolio_concentration_score · penalty_strength=0 default until backtest validates |

### Definition of Done

- Flows scored as features show ≥0.1% expectancy lift on midcap signals
- Catalyst LLM agent achieves: malformed-output rate <2%, latency p95 <3s, free-tier usage <80% of daily quota
- Backtest of full Phase 2 system vs Phase 1 system: ≥20% improvement in cost-adjusted expectancy OR ≥30% reduction in max DD
- Paper portfolio runs for 14 trading days with all loops green

**Estimated effort**: 30 working days
**Cost**: ₹0–500 (Gemini stays free; possibly ₹500 if we want NSE bulk-deal historical archive)

---

## Phase 3 — Agentic loops (Weeks 11–16)

**Goal**: the LLM doesn't just augment scoring — it reviews the system itself and proposes improvements.

### Tasks

| # | Task | Status | Notes |
|---|---|---|---|
| 3.1 | `anju_ai/loops/eod_postmortem.py` + `agent.post_mortem` (Gemini) | ✅ Done | 2026-05-14 · 10 unit tests · prompt v1 · finds unprocessed outcomes · injects similar past lessons · postmortem.yml 4:30PM IST cron |
| 3.2 | `anju_ai/loops/weekly_critic.py` + `agent.weekly_critic` (Claude) | ⏳ | Proposes revisions |
| 3.3 | Telegram interactive: `/approve_<id>` `/reject_<id>` → applies revision via PR | ⏳ | |
| 3.4 | `anju_ai/loops/anomaly_qa.py` + `agent.anomaly_qa` (Gemini, every 3h) | ✅ Done | 2026-05-14 · 9 unit tests · prompt v1 · snapshot collectors for workflow/data/regime/signals/traces/positions · anomaly_qa.yml every-3h cron |
| 3.5 | `anju_ai/loops/deep_review_agent.py` + `agent.deep_review` (Gemini Pro) | ✅ Done | 2026-05-14 · 12 unit tests · prompt v1 · bull/bear/base/levels/options/confidence · wired into manual_review.yml |
| 3.6 | Reasoning-trace audit UI (simple HTML report from memory.db) | ⏳ | |
| 3.7 | A/B comparison vs anju-trader: cost-adjusted expectancy over rolling 60 days | ⏳ | |
| 3.8 | Bear-regime defensive playbook + short F&O setups | ⏳ | Audit finding 3.7 |
| 3.9 | Tax-aware exit logic (LTCG deferral when within 30 days of 365-day mark) | ⏳ | |

### Definition of Done

- All 5 agent loops live; reasoning_traces populated for every loop run
- Weekly critic proposes ≥1 specific, evidence-cited revision per week on average
- Anomaly QA precision ≥90% (low false-positive rate)
- A/B comparison shows anju-trader-AI ≥1.2× anju-trader cost-adjusted expectancy on the same signal universe
- Manish can run a full week without opening a laptop

**Estimated effort**: 30 working days
**Cost**: ~₹300–800/month (Claude weekly critic + small Gemini overage if any)

---

## Phase 4 — Cutover (Week 17+)

**Goal**: kill anju-trader. Run anju-trader-AI on live capital.

### Pre-cutover gates (all must pass)

- [ ] Cost-adjusted expectancy ≥ 1.3× anju-trader's over a rolling 60-day window
- [ ] ≥40 closed signals in the comparison window
- [ ] Max drawdown ≤ anju-trader's
- [ ] No CRITICAL anomalies in last 14 days
- [ ] At least 4 approved weekly-critic revisions applied (proves the learning loop works)
- [ ] Manish has manually verified 10 random reasoning traces and agrees with the logic

### Tasks

| # | Task | Status | Notes |
|---|---|---|---|
| 4.1 | Migration of `portfolio.json` from anju-trader → anju-trader-AI | ⏳ | |
| 4.2 | Switch live capital flag: `config/runtime.yaml: live=true` | ⏳ | |
| 4.3 | Disable anju-trader workflows (don't delete — archive for 90 days) | ⏳ | |
| 4.4 | First week of live: paper + live mirror, daily comparison | ⏳ | Belt + suspenders |
| 4.5 | Public retrospective in README (what worked, what didn't) | ⏳ | |

**Cost (steady state)**: ~₹500–2000/month total (LLM + optional Kite + optional premium news).

---

## Success metrics (continuous after cutover)

Tracked in `data/memory.db` and reviewed in a weekly Telegram report:

| Metric | Target | Definition |
|---|---|---|
| **CAGR (annualised)** | ≥30% Y1, ≥50% rolling 24-mo | Net of all costs |
| **Max drawdown** | ≤12% monthly, ≤20% peak-to-trough | From equity curve |
| **Sharpe ratio** | ≥1.5 | After-cost returns, daily |
| **Win rate** | ≥55% | All closed signals |
| **R:R (avg winner / avg loser)** | ≥1.5× | All closed signals |
| **Expectancy / trade** | ≥0.8% | After all costs |
| **Closed signals / month** | 8–20 | Concentration band |
| **Reasoning-trace coverage** | 100% | Every decision logged |
| **Workflow uptime** | ≥99% | GH Actions success rate |
| **Anomaly precision** | ≥90% | True-positive WARN/CRITICAL rate |

If two consecutive months miss any **bold** metric, the system is paused for review.

---

## What we explicitly are NOT building (anti-roadmap)

- HFT or scalping (latency-bound, not our edge)
- Crypto (different microstructure, different intelligence)
- US equities (different timezone, different data sources, distraction)
- Mutual fund advisory (different regulatory regime)
- Auto-execution before Phase 4 (you confirm trades from digest)
- Mobile app / web dashboard (Telegram is the UI)
- Multi-user / SaaS in Phase 0–4 (the user is Manish, period)

If we get to year 2 and the system is provably working, **then** we revisit monetisation as a separate project. Not before.
