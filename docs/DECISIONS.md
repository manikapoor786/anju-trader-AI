# Architecture Decision Records (ADRs)

> Every non-obvious design choice gets a short ADR here. When future-us asks
> "why did we do it this way?", this file has the answer.

Format: each ADR has Context, Decision, Consequences, Alternatives considered.

---

## ADR-001: Separate project, not in-place upgrade of anju-trader

**Date**: 2026-05-13
**Status**: Accepted

### Context
anju-trader is a working rule-based system with 18k lines of code, 10 GitHub workflows, daily signals Manish trusts the format of, and a real (under-deployed) portfolio. The v2 audit identified 12 critical issues that require invasive rewrites: outcome tracking model is wrong, scoring weights are unvalidated, no cost model, no flows, no catalysts, no F&O, etc. The agentic architecture envisioned is fundamentally different from rule-based scripts.

### Decision
Build `anju-trader-AI` as a separate GitHub repository, separate Telegram chat, separate directory on disk. Run both systems in parallel during Phases 0–3. Cut over to AI only when it provably beats the old system on cost-adjusted expectancy.

### Consequences
- **+** Old system keeps working — no downtime, no broken signals on a Tuesday
- **+** Greenfield freedom to design memory schemas and module boundaries properly
- **+** A/B comparison gives empirical evidence for cutover (not vibes)
- **+** Mental separation: "the system that works" vs "the experiment"
- **−** Two systems to maintain during overlap (acceptable for 4 months)
- **−** Duplication of some proven primitives (we fork only proven ones, keep small)

### Alternatives considered
- *In-place upgrade*: rejected — invasive changes on live trading system, high blast radius
- *Branch-based*: rejected — branches don't isolate state DBs, Telegram chats, GH Actions secrets

---

## ADR-002: Agentic-first architecture (LLM is the orchestrator, not a feature)

**Date**: 2026-05-13
**Status**: Accepted

### Context
Bolting an LLM onto rule-based code creates a hybrid that's worse than either pure approach: the rules constrain the LLM's reasoning, and the LLM's non-determinism makes the rules untrustable. We want the system to learn — that requires the LLM to be a first-class component with its own state (memory), tools, and reasoning loops.

### Decision
The LLM is a constrained reasoner with typed inputs, typed outputs, a bounded toolbox, and read-only access to a structured memory database. Five named reasoning loops (catalyst_review, post_mortem, weekly_critic, anomaly_qa, deep_review) — each with its own prompt, schema, and authority. Tools are pure functions the agent calls; the agent never sends Telegram or writes to memory directly.

### Consequences
- **+** Every LLM decision is auditable (reasoning_traces table)
- **+** Prompts are versioned and A/B-testable
- **+** Tools stay deterministic and unit-testable
- **+** Failure modes are well-defined and recoverable
- **−** More upfront design (worth it)
- **−** LLM costs need monitoring — mitigated by Gemini free tier for high-volume loops

### Alternatives considered
- *Free-form LLM agent (LangChain-style)*: rejected — non-deterministic, hard to audit, expensive
- *No LLM (rule-based only)*: rejected — can't catch news catalysts or learn from outcomes
- *LLM only at signal time (no learning loops)*: rejected — kills the "self-correcting" promise

---

## ADR-003: Free LLM tier (Gemini Flash) for high-volume loops; Claude only for weekly critic

**Date**: 2026-05-13
**Status**: Accepted

### Context
The user wants this to run free for now. Gemini 1.5 Flash has a free tier of 1500 req/day and 15 RPM as of late 2025 — sufficient for catalyst_review (~30/day) + post_mortem (~5/day) + anomaly_qa (~8/day) + deep_review (occasional). The weekly_critic is the only loop where output quality matters enough to justify Claude (~1 call/week = ~₹5).

### Decision
- `catalyst_review`, `post_mortem`, `anomaly_qa`: Gemini 1.5 Flash (free tier)
- `deep_review`: Gemini 1.5 Pro (free tier — used on demand, low volume)
- `weekly_critic`: Claude Sonnet 4.6 (paid, ~₹5/week)
- All clients implement a common `LLMClient` interface so swapping providers later requires no callsite changes

### Consequences
- **+** Phase 0–3 effectively free to operate
- **+** Easy swap to paid if quality requires
- **−** Quality of Gemini Flash for catalyst grading is unknown — must validate during Phase 2
- **−** Free-tier quotas can change; mitigated by usage monitoring + fallback to Pro tier

### Alternatives considered
- *All Claude*: rejected — costs ~₹3000/month at projected volume
- *All Gemini*: rejected — weekly critic quality matters too much
- *Local LLM (Ollama)*: rejected — too slow on Actions runners, quality unproven

---

## ADR-004: Append-only memory model

**Date**: 2026-05-13
**Status**: Accepted

### Context
Two requirements: (1) every decision must be auditable retrospectively, (2) the system must be replayable bit-for-bit. UPDATEs make replay hard and audit logs lie. The cost of append-only is more storage — SQLite handles 10s of GB without issues.

### Decision
Tables `signals`, `outcomes`, `reasoning_traces`, `audit`, `lessons`, `revisions` are append-only. Corrections are inserted as new rows with `supersedes` foreign key. The "current truth" of a record is the latest row in its chain. A small daily compaction job archives chains older than 1 year to a separate `archive.db`.

### Consequences
- **+** Full replayability — "what would the system have done with this data 6 months ago?"
- **+** Auditability — every decision has a permanent record
- **+** Simpler reasoning about state — no mutation surprises
- **−** Queries need a "latest" filter — abstracted into a `current()` view per table
- **−** DB size grows linearly — manageable with compaction

### Alternatives considered
- *Mutable rows*: rejected — destroys auditability
- *Event-sourcing with separate read model*: rejected — over-engineered for our scale

---

## ADR-005: Human approval required for every self-modification, forever

**Date**: 2026-05-13
**Status**: Accepted

### Context
The weekly critic agent proposes parameter and code changes. Auto-applying them creates a system that can silently mutate into something we don't recognise — and creates a feedback loop where bad proposals reinforce themselves.

### Decision
The weekly critic writes proposals to `revisions` table with status='proposed'. The system sends a Telegram message with the proposal and `/approve_<id>` / `/reject_<id>` buttons. Only on `/approve` does the change actually apply (via a small workflow that opens a PR, runs tests, and merges if green). This rule does not relax in later phases.

### Consequences
- **+** No silent mutation
- **+** Manish stays in the loop on direction
- **+** Failed proposals teach the critic what we don't want
- **−** Slower improvement velocity (5 min of approval per week is the cost)
- **−** Requires interactive Telegram handler (added in Phase 3)

### Alternatives considered
- *Auto-apply if backtest passes*: rejected — backtest overfits, would drift system away from operator's intent
- *Auto-apply for parameter changes only*: rejected — even parameter changes can wreck things
- *Approval only in Phase 0–3, auto in Phase 4+*: rejected — no point removing the safety once it works

---

## ADR-006: Phone-first operability via GitHub Actions

**Date**: 2026-05-13
**Status**: Accepted

### Context
Manish wants to run the entire system from a phone — no laptop dependence. GitHub Actions has a mobile app that supports workflow_dispatch with inputs. Telegram bot API supports interactive callbacks (buttons). Together they're sufficient for full operability.

### Decision
- Every workflow has `workflow_dispatch` inputs
- Every alert in Telegram includes inline buttons or `/command_<id>` reply patterns
- A slim webhook handler (running on either GH Actions self-trigger or a free service like Cloudflare Workers in Phase 3) converts Telegram replies into workflow_dispatch calls
- No web UI is built — Telegram is the UI

### Consequences
- **+** Full operability from anywhere with cell signal
- **+** Zero hosting cost for the UI
- **−** Telegram message-length limits (4096 chars) → some reports get split or PDFs
- **−** Webhook handler is one moving part to maintain

### Alternatives considered
- *Web dashboard*: rejected — hosting cost, security surface, not how Manish wants to use it
- *iOS/Android app*: rejected — total overkill
- *Email reports*: rejected — Telegram is faster and more reliable on phone

---

## ADR-007: Paper-only until Phase 4 cutover

**Date**: 2026-05-13
**Status**: Accepted

### Context
We have no validated edge for anju-trader-AI yet. Trading real money on an unvalidated system is gambling. We also can't validate against anju-trader without running them on the same data — which means a paper portfolio is required regardless.

### Decision
`config/runtime.yaml: live=false` until all Phase 4 pre-cutover gates pass. In `live=false` mode, the system generates signals, paper-fills them in `memory.db`, tracks outcomes, but never sends an order to Kite. Telegram digests are clearly labelled "PAPER".

### Consequences
- **+** Real edge proves itself with real-shaped data (just no real money)
- **+** Failures during Phase 0–3 are zero-cost
- **+** A/B comparison vs anju-trader is on the same time period, like-for-like
- **−** No actual returns during 4 months of paper running — opportunity cost
- **−** Slippage modelling has to be careful — the real cost only materialises on cutover

### Alternatives considered
- *Live with tiny size (₹10k/trade) for Phase 2+*: rejected — slippage modelling stays uncertain, and real money creates emotion that corrupts feedback signals
- *Live immediately*: rejected — not validated, not negotiable

---

## ADR-008: Realistic target ₹4–5 cr in 24 months, not ₹10 cr

**Date**: 2026-05-13
**Status**: Accepted

### Context
Manish initially targeted ₹10 cr in 24 months (140% CAGR). The v2 audit demonstrated this is not realistic at this capital scale by any verified track record in history. Designing a system around an impossible target creates wrong incentives — toward over-leverage, over-concentration, and survivorship-biased thinking.

### Decision
The system is engineered for **₹4–5 cr in 24 months (50–70% CAGR)**. This is achievable in a top-1% Indian self-directed retail bracket with discipline, costs accounted for, and reasonable bull-market conditions. Position sizing, leverage caps, and drawdown limits are calibrated to this target. If conditions outperform, we overshoot toward ₹6–7 cr — fine. If they underperform, we still survive — also fine.

### Consequences
- **+** Risk parameters align with achievable outcomes
- **+** Avoids the "blow-up to chase 10 cr" trap
- **+** Honest expectation-setting between Manish and the system
- **−** Manish has to internalise that ₹10 cr in 24 months was always fantasy
- **−** None other — this is just truth

### Alternatives considered
- *Target ₹10 cr formally*: rejected — would force leverage and concentration beyond safe bounds
- *No formal target*: rejected — vague targets produce vague systems

---

## ADR-009: Public GitHub repo until live capital deployed

**Date**: 2026-05-13
**Status**: Tentative — pending Manish confirmation

### Context
GitHub Actions on public repos is free with unlimited minutes. Private repos get 2000 min/month free. anju-trader-AI is projected at ~3000 min/month at full operation. Public also has benefits (portfolio piece, others can review the architecture). Cost: the strategy is somewhat visible — but since most of the edge is in execution, calibration, and tuning rather than the published recipe, the strategic risk is small.

### Decision (proposed)
Keep `anju-trader-AI` public during Phase 0–3 (no live capital). Switch to private at Phase 4 cutover (live capital + tuned edge).

### Consequences
- **+** Unlimited Actions minutes free during build
- **+** Forces clean code (others can see)
- **+** Architecture is itself a portfolio piece
- **−** Backtested numbers and exact thresholds visible
- **−** Need to scrub `.env` and any secret references carefully

### Alternatives considered
- *Private throughout*: rejected unless Manish strongly prefers — costs Actions minutes
- *Public forever*: rejected — when live capital is in, the strategy becomes a target

**Decision pending Manish confirmation.**

---

## ADR-010: Single Telegram chat for AI system, distinct from anju-trader chat

**Date**: 2026-05-13
**Status**: Accepted

### Context
Running both systems means each generates signals. If they share a Telegram chat, the user can't distinguish which is which, can't track which one to act on, and the A/B comparison becomes muddled.

### Decision
Create a new Telegram bot + chat for anju-trader-AI (e.g. `@AnjuAI_bot`, chat name "anju-trader-AI"). Anju-trader keeps its existing bot and chat. The A/B comparison report goes to both for transparency.

### Consequences
- **+** Clear separation of signals
- **+** Manish can mute one if needed
- **+** New chat name makes the experimental nature explicit
- **−** One more bot to maintain (trivial)

### Alternatives considered
- *Same bot, prefix every message*: rejected — clutters anju-trader chat with PAPER signals
- *Two Telegram accounts*: rejected — overkill

---

## ADR-011: Edge-augmentation features ship in CALIBRATION mode (weight=0) by default

**Date**: 2026-05-14
**Status**: Accepted

### Context
Phase 2 adds many new scoring inputs: catalyst LLM scores (2.5), FII/DII flows
(2.1), bulk/block deals (2.2), insider activity (2.3), F&O leverage suggestion
(2.7), correlation-aware sizing (2.10), bear playbook (3.8), tax-aware exits
(3.9). Each could in theory adjust signals/sizing. If they all turn on at once
without empirical validation, we have no way to attribute outcome changes to
any one cause — a classic experiment-design failure.

### Decision
Every new edge feature ships in CALIBRATION MODE by default — collected and
logged but with **zero effect on the scoring/sizing pipeline** until a
backtest validates that turning it on with a non-zero weight produces measurable
expectancy lift or DD reduction. Config flags control activation:

- `runtime.yaml:fno.enabled = false` → no F&O leverage
- `runtime.yaml:bear_playbook.enabled = false` → standard scan in Bear regime
- catalyst_weight = 0.0 hardcoded in step_catalyst_augment
- correlation penalty_strength = 0.0 default

Each gets toggled to non-zero only after Phase 1.5 (cut negative buckets) +
Phase 2.4 (backtest features) provide evidence.

### Consequences
- **+** Clean attribution: when a feature is turned on, any change in
  expectancy is its alone
- **+** Safety: a buggy new feature can't move real money — it's wired in
  but inactive
- **+** Data collection: reasoning_traces still accumulate so the eventual
  backtest has rich data to evaluate
- **−** Some early signal benefit forfeited (acceptable cost)

### Alternatives considered
- *Turn everything on, observe*: rejected — too many simultaneous variables
- *Don't wire until validated*: rejected — then we have no data to validate with


## Pending decisions (to be ADR'd as they're made)

- ADR-012: Backtest train/test split methodology (rolling window? expanding window?)
- ADR-013: Slippage model parameters (linear vs square-root in size; per segment)
- ADR-014: Specific F&O leverage rules (which signals qualify, how much leverage)
- ADR-015: Bear-regime short setups (which symbols, what triggers)
- ADR-016: Tax-aware exit deferral mechanics — when to override discipline
- ADR-017: Live cutover validation thresholds (rolling window length, required N)
