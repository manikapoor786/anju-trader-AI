# Agent Protocol — How the LLM thinks and acts

> Last updated: 2026-05-13
> Status: Phase 0 (specification only)

This document defines **exactly** what the LLM does at each reasoning loop, what it sees, what it can do, and what it cannot. Without this contract, "agentic AI" is hand-wavy marketing. With it, the agent is a constrained, auditable component.

---

## 1. The agent's contract

The agent is **not** a free-form trading bot. It is a structured reasoner constrained by:

1. **Typed inputs** — every prompt has a Pydantic schema. No surprises.
2. **Typed outputs** — every response is parsed into a dataclass. If parsing fails, retry once, then human escalation.
3. **A bounded toolbox** — only the tools listed in this doc, no others.
4. **A reasoning budget** — max tokens, max tool calls, max latency per loop. Exceed = abort.
5. **Memory access (read-only by default)** — the agent can SELECT but cannot UPDATE memory. Writes happen in the calling loop after the agent returns.
6. **No external network access from prompts** — the agent can't be tricked into calling URLs.

---

## 2. Five named agent loops

Each loop is a distinct LLM call with its own prompt, schema, and authority.

### 2.1 `agent.catalyst_review` — runs in `morning.yml`

**Purpose**: grade the news catalyst for one candidate signal.

**Input schema**:
```python
class CatalystReviewInput(BaseModel):
    symbol: str
    company_name: str
    sector: str
    rule_based_score: float            # 0–100, what scoring tools said
    news_24h: list[NewsItem]           # title + source + url + snippet
    filings_7d: list[FilingItem]       # type + headline + url
    earnings_calendar: EarningsItem    # next earnings + estimate + history
    open_position: Optional[Position]  # is this already in portfolio?
```

**Output schema**:
```python
class CatalystReviewOutput(BaseModel):
    catalyst_score: float        # -1.0 (very bearish) to +1.0 (very bullish)
    confidence: float            # 0–1
    primary_driver: str          # one short phrase, e.g. "Q4 results beat"
    reasoning: str               # 2–4 sentences explaining
    flags: list[str]             # ['EARNINGS_THIS_WEEK', 'REGULATORY_RISK', ...]
    suggested_action: Literal['STRENGTHEN', 'NEUTRAL', 'WEAKEN', 'BLOCK']
```

**Authority**:
- Can shift `final_score = rule_based_score × (1 + catalyst_score × catalyst_weight)`
- Can BLOCK a signal (rare — only when news is unambiguously bad)
- Cannot change `rule_based_score` itself
- Cannot trigger fills directly

**Budget**: 1500 input tokens, 400 output tokens, 1 LLM call per candidate. With ~30 candidates/day = ~50k input tokens/day = comfortably within Gemini free tier.

**Model**: `gemini-1.5-flash` (or successor). Fallback: `gemini-1.0-pro`.

---

### 2.2 `agent.post_mortem` — runs in `eod.yml`

**Purpose**: write a structured lesson from one closed trade.

**Input schema**:
```python
class PostMortemInput(BaseModel):
    signal: SignalRow              # what we saw at signal time
    fill: FillRow                  # entry price, qty, cost
    outcome: OutcomeRow            # exit price, days held, P&L
    market_context: MarketContext  # regime over the holding period, sector behaviour
    similar_past_trades: list[OutcomeRow]  # 5 most similar past trades + outcomes
```

**Output schema**:
```python
class PostMortemOutput(BaseModel):
    classification: Literal['EDGE_WORKING', 'EDGE_BROKEN', 'BAD_LUCK',
                            'BAD_EXECUTION', 'WRONG_REGIME', 'BLACK_SWAN']
    primary_factor: str            # what mattered most
    lesson: str                    # 1–2 sentence, written for future-self
    similar_pattern_id: Optional[str]  # links to lessons.id if we've seen this before
    suggests_revision: bool        # should the weekly critic look at this?
    revision_hint: Optional[str]   # if suggests_revision, what to consider
```

**Authority**:
- Read-only on all memory
- Writes a row to `lessons` (via the calling loop)
- Can flag patterns for the weekly critic to investigate
- Cannot directly modify any scoring weight

**Budget**: 2500 input tokens, 500 output tokens, 1 LLM call per closed trade. With 1–5 closes/day = trivial cost.

---

### 2.3 `agent.weekly_critic` — runs in `weekly_critic.yml`

**Purpose**: review the week's lessons + expectancy data and propose specific revisions.

**Input schema**:
```python
class WeeklyCriticInput(BaseModel):
    week_signals: list[SignalRow]
    week_outcomes: list[OutcomeRow]
    week_lessons: list[LessonRow]
    expectancy_by_feature: dict[str, ExpectancyStats]  # win_rate, R:R, count
    expectancy_by_score_bucket: dict[str, ExpectancyStats]
    expectancy_by_regime: dict[str, ExpectancyStats]
    open_proposals: list[RevisionRow]   # not yet approved/rejected
    recent_approved: list[RevisionRow]  # what we changed in last 30d
```

**Output schema**:
```python
class WeeklyCriticOutput(BaseModel):
    summary: str                        # 3–5 sentences on the week
    proposals: list[RevisionProposal]   # 0–5 specific change proposals
    flags: list[str]                    # ['LOW_TRADE_COUNT', 'REGIME_SHIFT', ...]

class RevisionProposal(BaseModel):
    kind: Literal['PARAMETER', 'WEIGHT', 'FILTER', 'NEW_RULE']
    target: str                         # e.g. 'tools.scoring.MIN_BASE_SCORE'
    current_value: str                  # e.g. '5'
    proposed_value: str                 # e.g. '7'
    rationale: str                      # 2–4 sentences citing evidence
    expected_impact: str                # 'expectancy +0.3% per trade based on 60d sim'
    confidence: float                   # 0–1
    backtest_required: bool             # should we backtest before approving?
```

**Authority**:
- Read-only on memory + recent revisions
- Writes proposals to `revisions` table with status='proposed'
- Cannot apply changes directly — only Manish can approve via Telegram
- Required to provide backtest evidence for any WEIGHT or FILTER change

**Budget**: 8000 input tokens, 2000 output tokens, 1 LLM call per week. **This is the only loop that uses Claude** (paid). Why: weekly cadence + high-leverage decision. Cost: ~₹3–8/week.

**Model**: `claude-sonnet-4-6` (or successor). Fallback: `claude-haiku-4-5` then `gemini-1.5-pro`.

---

### 2.4 `agent.anomaly_qa` — runs in `anomaly_qa.yml` (every 3 hours)

**Purpose**: detect when the system itself is broken or behaving oddly.

**Input schema**:
```python
class AnomalyQAInput(BaseModel):
    last_run_health: dict[str, RunStats]   # each workflow: success, duration, error_count
    data_freshness: dict[str, datetime]    # how stale is each data source?
    regime_history: list[RegimeRow]        # last 30 days of regime classifications
    signal_count_history: list[int]        # daily signal count for last 30 days
    error_log_tail: list[ErrorRow]         # last 50 errors from audit
    open_position_health: list[PositionHealth]
```

**Output schema**:
```python
class AnomalyQAOutput(BaseModel):
    anomalies: list[Anomaly]

class Anomaly(BaseModel):
    severity: Literal['INFO', 'WARN', 'CRITICAL']
    category: str                       # 'DATA_STALE', 'REGIME_FLIP', 'NO_SIGNALS', ...
    description: str
    suggested_fix: str
    auto_remediable: bool               # can a workflow fix this without human?
    workflow_to_trigger: Optional[str]  # which manual workflow to run
```

**Authority**:
- Read-only on everything
- Writes to `audit` with category='ANOMALY'
- Sends Telegram alerts only for WARN/CRITICAL
- Cannot auto-fix — only suggests

**Budget**: 3000 input tokens, 800 output tokens. Free tier (Gemini Flash).

---

### 2.5 `agent.deep_review` — runs in `manual_review.yml` (you trigger)

**Purpose**: when you want a deep multi-angle review of one symbol on demand.

**Input schema**:
```python
class DeepReviewInput(BaseModel):
    symbol: str
    horizon: Literal['SWING_1_4W', 'POSITIONAL_1_3M', 'BOTH']
    user_question: Optional[str]    # if you want a specific angle answered
    df_1d: pd.DataFrame             # 1 year daily
    df_1w: pd.DataFrame             # 3 years weekly
    df_1h: pd.DataFrame             # 60 days hourly
    flows: FlowsSnapshot
    news_30d: list[NewsItem]
    filings_90d: list[FilingItem]
    rule_based_score: float
    similar_setups_outcomes: list[OutcomeRow]  # 5 most similar past trades
```

**Output schema**:
```python
class DeepReviewOutput(BaseModel):
    bull_case: list[str]            # bullet points
    bear_case: list[str]
    base_case_outcome: str          # most likely scenario
    swing_verdict: Verdict          # BUY / WATCH / AVOID
    positional_verdict: Verdict
    key_levels: dict[str, float]    # support, resistance, invalidation
    options_recommendation: Optional[OptionsRec]  # ATM call/put suggestion
    confidence: float
    blind_spots: list[str]          # what the model knows it doesn't know
```

**Authority**: read-only on everything, sends a rich Telegram message back with the structured review.

**Budget**: 10k input tokens, 3k output tokens. Used only when you ask. Cost: ₹1–2 per review.

---

## 3. Prompt versioning

Every prompt is a file under `anju_ai/llm/prompts/`:

```
prompts/
├── catalyst_review.v1.md
├── catalyst_review.v2.md           # newer version — A/B-able
├── post_mortem.v1.md
├── weekly_critic.v1.md
├── anomaly_qa.v1.md
└── deep_review.v1.md
```

Each prompt file starts with frontmatter:

```yaml
---
name: catalyst_review
version: 1
model: gemini-1.5-flash
input_schema: CatalystReviewInput
output_schema: CatalystReviewOutput
max_tokens_in: 1500
max_tokens_out: 400
temperature: 0.2
created: 2026-05-13
description: Grade catalyst sentiment for one candidate signal
---
```

The agent loop loads prompts by name+version. We can roll new prompts forward, A/B test them, roll back if they regress. Every `reasoning_trace` row records exactly which prompt version was used.

---

## 4. Tool-use contract (when the agent calls tools)

The agent loops that need tools (`deep_review`, `weekly_critic`) use structured tool-use:

```python
# What the agent sees in its prompt:
tools_available = [
    "fetch_ohlcv(symbol, period)",
    "fetch_flows(symbol, days)",
    "run_backtest(strategy, params, start, end)",
    "lookup_similar_outcomes(symbol, setup_type, n)",
    "compute_correlation(symbol_a, symbol_b)",
]
```

When the LLM wants to call one, it emits structured tool-use JSON. The orchestrator parses it, calls the actual Python tool, returns the result. The agent keeps reasoning. **The LLM never sees raw Python objects** — every tool I/O is JSON-serializable.

**Hard limit**: max 8 tool calls per single agent invocation. After that the loop terminates with a "ran out of budget" sentinel and Manish gets a Telegram about it.

---

## 5. Failure modes and fallbacks

What happens when an agent loop fails?

| Failure | Detection | Fallback |
|---|---|---|
| LLM timeout | 30s wall clock | Retry once with same prompt |
| LLM returns malformed JSON | Pydantic validation fails | Retry once with stricter instruction; if still fails, write `reasoning_trace` with status='parse_error' and skip this candidate (don't kill the whole loop) |
| LLM API rate-limit | HTTP 429 | Exponential backoff up to 60s; if persists, switch to fallback model |
| LLM API down | Connection error | Skip this loop's LLM step; signal generation falls back to rule-based score only; Telegram notify |
| Token budget exceeded | Pre-call estimate | Truncate inputs (drop oldest news/lessons); if still over, skip |
| Tool call fails inside agent | Exception in tool | Return error to LLM, agent decides how to proceed; if 3 tools fail in a row, abort with structured error |
| Memory DB locked | sqlite3.OperationalError | Retry with backoff; if persists, alert |

Every failure writes a row to `audit` with the full context. Failures are not silent.

---

## 6. Reasoning trace example (what's stored in memory)

For one `catalyst_review` call on RELIANCE:

```sql
SELECT * FROM reasoning_traces WHERE id = 12345;
```

```json
{
  "id": 12345,
  "loop": "catalyst_review",
  "prompt_name": "catalyst_review",
  "prompt_version": 1,
  "model": "gemini-1.5-flash",
  "input_tokens": 1387,
  "output_tokens": 312,
  "latency_ms": 1840,
  "input_payload": "{...full Pydantic-validated input as JSON...}",
  "output_payload": "{...full parsed output as JSON...}",
  "raw_llm_output": "...the actual model text before parsing...",
  "tool_calls": [],
  "errors": [],
  "linked_signal_id": 98765,
  "created_at": "2026-05-14T06:32:18+05:30"
}
```

When you ask "why did the system buy RELIANCE on May 14?", you join:
```sql
SELECT * FROM signals WHERE symbol='RELIANCE' AND signal_date='2026-05-14';
SELECT * FROM reasoning_traces WHERE linked_signal_id = <that signal's id>;
```

And you get the complete reasoning. **This is the auditability foundation that makes the system trustable.**

---

## 7. What the agent is explicitly forbidden to do

- Recommend "buy/sell" without a fully-populated scoring breakdown
- Cite news that wasn't in its input (no hallucination)
- Recommend a size larger than what the sizing tool returned
- Override the regime classifier (it can disagree in reasoning but must defer)
- Mutate any memory row (writes are caller's responsibility)
- Send Telegram directly (delivery happens in the loop based on agent output)
- Call out to URLs or APIs not in its toolbox
- Use information from earlier conversations (no context leakage across loops)

These rules are enforced via prompt + post-parse validation. Violations get logged and the offending output is rejected.

---

## 8. How we'll know the agent is working

After Phase 3 (when all loops are live), success metrics:

| Metric | Target | How measured |
|---|---|---|
| Catalyst score predictive value | r > 0.2 between `catalyst_score` and post-trade P&L | 60-day rolling |
| Post-mortem quality | ≥70% of lessons cited by future post-mortems as "similar pattern" | Manual review monthly |
| Weekly critic approval rate | 60–80% (too high = rubber stamping, too low = noise) | Tracked over time |
| Anomaly QA precision | <10% false-positive WARNs | Manual tagging |
| Deep review utility | You actually use it ≥3x/week | Self-reported |
| Agent vs rule-only signal performance | Agent-augmented signals have ≥1.2× expectancy of rule-only | A/B comparison in memory |

If any metric stays below target for 30 days, the corresponding loop is reviewed and either fixed or disabled.

---

## 9. Bottom line

The agent is not magic. It's a **constrained, auditable, replayable reasoning component** that:

- Adds catalyst intelligence rule-based scoring can't see
- Learns from every closed trade
- Proposes improvements you approve
- Detects when itself is broken
- Lets you ask "what about XYZ stock?" and get a real answer

It is not a black box. It is a glass box. Every decision can be traced, replayed, and challenged.

That is what makes it trustable.
