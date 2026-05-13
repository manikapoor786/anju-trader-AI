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

**Phase**: 0 — Scaffolding
**Branch**: `main`
**Last update**: 2026-05-13
**Live capital deployed**: ₹0 (paper-only until validated)
**Next milestone**: Phase 1 — Tier 1 fixes (outcome tracker, cost model, backtest validation)

See [docs/ROADMAP.md](docs/ROADMAP.md) for the 4-phase plan and current progress.

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

## How to use (when implemented)

All workflows have `workflow_dispatch` inputs and are triggerable from the GitHub mobile app.

```
Workflow                  Schedule            Phone-triggerable?
─────────────────────────────────────────────────────────────────
morning.yml               6:30 AM IST         ✅
intraday.yml              every 30m mkt hrs   ✅
eod.yml                   4:00 PM IST         ✅
weekly_critic.yml         Sun 9:00 AM IST     ✅
manual_scan.yml           on demand           ✅
manual_review.yml         on demand           ✅ (deep LLM review of any stock)
manual_backtest.yml       on demand           ✅
manual_paper_book.yml     on demand           ✅ (paper portfolio snapshot)
```

Interactive Telegram: when the weekly critic proposes a change, you'll get a message with `/approve_<id>` and `/reject_<id>` buttons. The change merges only on approve.

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

| When | Action | Where |
|---|---|---|
| Now | Review docs, approve architecture | `docs/ARCHITECTURE.md` + this file |
| Phase 0 done | Create GitHub repo, push code | One push command from terminal |
| Phase 1 done | Review backtest results | Telegram digest + a PDF |
| Phase 2 done | Compare against anju-trader for 30 days | A/B report sent to Telegram |
| Phase 3 done | Approve weekly critic proposals (5 min/wk) | Telegram interactive buttons |
| Phase 4 cutover | Migrate live capital | Single config flip |

You do not need to be in front of a laptop to run this. Everything is phone-controllable.

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
