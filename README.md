# 🌸 anju-trader-AI

**The agentic successor to anju-trader.**
Named after my mother. Built to convert ₹1.75 cr into ₹4–5 cr over 24 months
with a system that thinks, learns, and can be trusted blindly because every
decision is auditable.

---

## What this project is

A **fully autonomous, agentic Indian-equity trading intelligence** that:

- Scans the NSE universe daily and produces both **swing** (1–4 wk) and **positional** (1–3 mo) signals
- Recommends both **cash** entries and **F&O leverage** when the setup warrants it
- Tracks every signal to a real outcome (WIN/LOSS/EXIT) — event-driven, not calendar-based
- Reviews itself: an LLM agent writes a post-mortem after each closed trade and proposes parameter / code improvements weekly
- Runs entirely on **GitHub Actions** (free tier), controllable from your phone
- Logs every reasoning step so you can audit "why did the system buy X on date Y?" months later

It is the project anju-trader was always trying to become but couldn't, because anju-trader is rule-based and this one is reasoning-based from the architecture up.

---

## Why a separate project (not an upgrade)

anju-trader works. It produces signals every morning and you trust the format.
Invasively rewriting it — the outcome tracker, the scoring weights, the sizing,
the cost model — would break the live system you rely on.

This project lets us:

- **Greenfield the architecture** for agentic reasoning (memory + LLM + tools + loops)
- **Run both in parallel** for 60–90 days and prove which one is actually better
- **Cut over only when evidence demands it** — not because we fell in love with the new shiny system

When `anju-trader-AI` beats `anju-trader` on cost-adjusted expectancy over a rolling 60-day window with ≥40 closed signals, we cut over. Until then, the old one stays live.

---

## Honest expectations

**What this system can deliver:**

| Promise | Truth |
|---|---|
| 99% uptime | ✅ GitHub Actions reliability |
| 99% process discipline | ✅ No emotional overrides, no skipped rules |
| 99% data freshness | ✅ Daily bhavcopy + live tick where needed |
| Auditable reasoning for every decision | ✅ Built into the memory schema |
| 60–70% win rate at best | ✅ Realistic ceiling with positive expectancy |
| 1.5–2.5x R:R per winner | ✅ Engineered into the scoring |
| **Top 1% of Indian self-directed retail by 2-yr risk-adjusted return** | ✅ The realistic ambition |
| 99% prediction accuracy | ❌ No system does this — claims of it are lies |
| "Beats all AI and humans" | ❌ Unverifiable, not the right target |
| Money-printing machine | ❌ Edge gets arbitraged the moment it exists |

The target that matches the math: **₹4–5 cr in 24 months (50–70% CAGR)**.
If the market cooperates and we add F&O leverage discipline, we overshoot toward ₹6–7 cr.
If it doesn't, we still survive.

---

## Status

**Last update**: 2026-05-14
**Branch**: `main`
**Tests**: **380 / 380 passing** across all modules
**Live capital deployed**: ₹0 (paper-only until backtest validates edge)
**Blocker**: 🧪 Backtest needs re-run with diagnostic logging to debug the 0-trades issue

### Phase progress

| Phase | Status | Notes |
|---|---|---|
| **Phase 0 — Scaffolding** | ✅ **8/8 complete** | Project skeleton, forked primitives, end-to-end paper-fill pipeline |
| **Phase 1 — Tier 1 fixes** | ✅ **6/8 complete** | 1.5 + 1.8 blocked on backtest output |
| **Phase 2 — Edge layer** | ✅ **9/10 complete** | 2.4 blocked on backtest output |
| **Phase 3 — Agentic loops** | ✅ **8/9 complete** | 3.7 blocked on anju-trader DB access (Phase 4 prep) |
| **Phase 4 — Cutover** | ⏳ Pending | Gated on validation criteria — all defined in ROADMAP |

### What's built

**All five LLM agents are live** (Gemini Flash for high-volume, Claude Sonnet for the weekly critic):
- 🧠 **catalyst_review** — daily news/filings scoring per candidate (calibration mode, weight=0)
- 📝 **post_mortem** — after each closed trade, writes structured lesson to `lessons.db`
- 📋 **weekly_critic** — Sunday 9 AM, proposes specific revisions with /approve workflow
- 🔍 **anomaly_qa** — every 3h, system self-monitor (Telegram only on WARN/CRITICAL)
- 🔬 **deep_review** — on-demand multi-angle symbol analysis from your phone

**Plus the full Phase 2 edge stack**:
- 💰 FII/DII + bulk/block + promoter/SAST daily ingestion (passive, scoring-weight pending backtest)
- 📊 F&O option chain + IV percentile + leverage gate
- 🎯 Concentration enforcer (5–15 positions, HERO pyramiding)
- 📡 Intraday position monitor every 30 min
- 🔗 Correlation-aware sizing penalty
- 🐻 Bear-regime defensive playbook + short F&O setups
- 💼 Tax-aware LTCG deferral logic

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full breakdown including
what's pending and why.

---

## Quick map

```
anju-trader-AI/
├── README.md                # This file — start here
├── docs/
│   ├── ARCHITECTURE.md      # The full system design
│   ├── AGENT_PROTOCOL.md    # How the LLM agent thinks and acts
│   ├── MEMORY_SCHEMA.md     # Database schemas (memory.db)
│   ├── ROADMAP.md           # Phase 0 → 4 progress tracker
│   └── DECISIONS.md         # Architecture Decision Records (ADRs)
├── .github/workflows/       # GitHub Actions (phone-triggerable)
├── anju_core/               # Proven primitives forked from anju-trader
├── anju_ai/                 # The new agentic brain
│   ├── memory/              # Persistent reasoning state
│   ├── tools/               # Pure functions the agent can call
│   ├── llm/                 # LLM clients (Gemini free, Claude paid)
│   ├── loops/               # Cadenced reasoning loops
│   └── tg/                  # Telegram delivery (incl. interactive)
├── config/                  # YAML configs (capital, risk, LLM, strategy)
├── data/                    # SQLite memory + historical cache
├── scripts/                 # One-off CLIs
└── tests/                   # Yes, this one has tests
```

---

## Workflows (all phone-triggerable from GitHub mobile)

| Workflow | Schedule | What it does |
|---|---|---|
| **🌅 morning.yml** | 6:30 AM IST Mon–Fri | Refresh data → detect regime → score universe → catalyst LLM augment → paper-fill top 15 → Telegram digest |
| **📡 intraday.yml** | every 30 min market hrs | Check open paper positions for stop/target hits → Telegram alerts only on triggers |
| **📊 eod_close.yml** | 4:00 PM IST Mon–Fri | Event-driven outcome closure with first-touch detection + cost-adjusted P&L |
| **📝 postmortem.yml** | 4:30 PM IST Mon–Fri | LLM writes a structured lesson for each closed trade |
| **🔍 anomaly_qa.yml** | every 3 hours | System self-monitor — Telegram only on WARN/CRITICAL |
| **📋 weekly_critic.yml** | Sun 9:00 AM IST | Claude reviews the week and proposes revisions |
| **📒 audit_report.yml** | Sat 11:00 AM IST | Reads memory.db and Telegrams a structured weekly summary |
| **🧪 backtest.yml** | on demand | Walk-forward replay of scoring on 2 years of bhavcopy |
| **📥 backfill_history.yml** | on demand | Backfill N days of bhavcopy into Actions cache |
| **🔎 verify_history.yml** | on demand | Confirms cached historical.db has real data |
| **🔍 manual_scan.yml** | on demand | Re-run the morning scan with custom universe/mode |
| **🔬 manual_review.yml** | on demand | Deep LLM review of a single symbol from your phone |
| **✅ manual_revision.yml** | on demand | Approve/reject a weekly_critic proposal by id |
| **📒 paper_book.yml** | Sat 10 AM + on demand | Paper portfolio snapshot to Telegram |
| **📊 ab_compare.yml** | Sun 11 AM IST | anju-trader-AI vs anju-trader comparison report |

**Approval flow** (per ADR-005, human approval forever): when weekly_critic proposes a change, the Telegram message includes a proposal id. Trigger `manual_revision.yml` from GitHub mobile with that id + action (approve / reject). The change applies only on approve.

---

## What's different from anju-trader

| Capability | anju-trader | anju-trader-AI |
|---|---|---|
| Architecture | Rule-based scripts | Agentic (LLM-orchestrated) |
| Outcome tracking | 10/20-day calendar check | Event-driven (first touch of stop/target) |
| Scoring weights | Hand-picked intuition | Walk-forward optimised from backtest |
| Position sizing | Fixed 1% risk | Kelly-fraction × score percentile × regime |
| Cost model | None | Full (brokerage + STT + slippage) |
| Institutional flows | None | FII/DII + bulk/block + promoter daily |
| News/catalyst | Stub (returns None) | LLM-driven daily scan of filings + news |
| F&O leverage | None | ATM call recommendation on high-conviction + low IV |
| Bear-market strategy | Hibernate (min_score=9) | Defensive rotation + selective shorts |
| Self-learning | Parametric tuner (no data) | LLM post-mortem → lessons.db → weekly critic |
| Self-QA | None | Anomaly detector + reasoning audit |
| Survivorship-clean universe | No | Yes (delisted stocks included in backtests) |
| Tax-aware exits | No | LTCG deferral logic |
| Concentration | 44 tiny positions | 8–15 conviction positions |
| Reasoning trail | None | Every decision logged with full context |

Every difference traces to one of the **12 critical findings** in the v2 audit
([../anju-trader/reports/anju_framework_audit_v2.pdf](../anju-trader/reports/anju_framework_audit_v2.pdf)).

---

## Cost to run

**Phase 0–2: ₹0/month.**

- GitHub Actions: free (2,000 min/mo private, unlimited public)
- Gemini 1.5 Flash: free tier (1,500 req/day, 15 RPM) — sufficient for daily catalyst scans + post-mortems
- NSE data: free (bhavcopy + FII/DII + bulk-deals from NSE website)
- yfinance: free
- Kite Connect: free if not subscribed to Kite (₹500/mo only required for live tick streaming, optional for Phase 0–2)

**Phase 3+: ~₹500–2,000/month (only if performance validates it).**

- Claude API for weekly strategy critic: ~₹500/mo at projected usage
- Kite Connect subscription for tick streaming: ₹500/mo
- Premium news API (optional): ~₹500/mo

If `anju-trader-AI` is doing what it's supposed to do, every ₹1 of cost should return ≥₹10 of additional alpha. We measure this monthly.

---

## What you (Manish) need to do

### Right now (immediate unblock)

**Re-trigger 🧪 Backtest workflow** with these defaults:
- start_date: 2024-05-14
- end_date: 2026-05-14
- universe: nifty100
- mode: strict
- min_score: 6

The first attempt produced 0 closed trades. I've added aggressive diagnostic
logging to `anju_ai/tools/backtest.py` that will surface the root cause on
re-run. Paste the diagnostic lines from the workflow log (`[backtest] Day N/493 ...`
and `[backtest] DIAGNOSTIC SUMMARY`) and I'll ship the fix.

### Ongoing (after backtest works)

| Cadence | Action | Effort |
|---|---|---|
| Weekly | Approve/reject `weekly_critic` proposals via manual_revision.yml | ~5 min |
| Weekly | Glance at the Saturday audit report Telegram | ~2 min |
| As needed | Trigger `manual_review.yml` to deep-LLM-review a symbol | ~30s |
| As needed | Trigger `manual_scan.yml` for ad-hoc scan on different universe | ~30s |
| Phase 4 cutover | Flip `config/runtime.yaml: live=true` after validation passes | one config edit |

You do not need to be in front of a laptop. Everything is phone-controllable.

---

## Reading order

If you have 10 minutes on your phone, read in this order:

1. **This README** — vision + status
2. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the system design
3. **[docs/AGENT_PROTOCOL.md](docs/AGENT_PROTOCOL.md)** — how the LLM actually thinks
4. **[docs/ROADMAP.md](docs/ROADMAP.md)** — what gets built when
5. **[docs/DECISIONS.md](docs/DECISIONS.md)** — why we made every non-obvious choice

If anything in those docs doesn't make sense or feels wrong, **say so before we write executable code**. The docs are cheap to change; the code isn't.

---

## License & disclaimer

Not financial advice. This is a personal research project. Past performance does not predict future results. Equity trading carries substantial risk of loss. The agent will make mistakes — that's why every action is logged, reviewed, and reversible.

Built by Manish + Claude Opus.
