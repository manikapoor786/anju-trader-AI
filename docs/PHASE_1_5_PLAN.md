# Phase 1.5 — The Real Scoring Fix

> Created: 2026-05-15
> Status: Diagnosis complete, implementation pending

## What the backtest told us

The first real backtest produced this:

```
Total closed trades: 1015  (still open: 10)
Win rate: 67.2%
Avg winner: +1.44%  ·  Avg loser: -4.59%
Realised R:R: 0.31x
NET expectancy: -0.536%/trade

By score bucket — ALL NEGATIVE:
  05-09  trades=139  win=68.3%  net=-0.563%
  10-14  trades=288  win=66.7%  net=-0.368%
  15-19  trades=328  win=67.1%  net=-0.566%
  20-24  trades=174  win=62.6%  net=-0.870%
  25-29  trades=79   win=77.2%  net=-0.173%   ← least bad
  30-34  trades=7    win=71.4%  net=-1.306%   ← too few

By entry model:
  🚀 Breakout/Retest Entry   68 trades   77.9% win  net +0.165%   ← POSITIVE
  📈 Momentum Entry          278 trades  66.5% win  net -0.620%
  🎯 Early Base Entry        16 trades   56.2% win  net -0.598%
  — (no model)               645 trades  66.2% win  net -0.596%
```

## What we learned

### 1. The scoring picks direction correctly (67% win rate is GENUINE).

But 67% wins at +1.45% avg can't beat 33% losses at -4.59% avg after costs.

### 2. The targets are systematically too close.

Why? In `scoring.py` exit_logic:
```python
for i in peaks_idx:
    p = float(h_arr[-lookback:][i])
    if p > cur_price * 1.005:        # any swing high 0.5%+ above price
        target_candidates.append((round(p, 2), "swing high"))
```

T1 = nearest swing high above entry. Often only +1-2% away. With a -5% stop, R:R = 0.2-0.4 — guaranteed loss math.

### 3. Naive R:R floor (1.5x) makes it WORSE.

Local test with `MIN_RR = 1.5`: win rate dropped 67% → 22%, expectancy got worse. **The +1.5% squeezes don't continue to +5-7%.** They are mean-reversion, not trend continuation.

### 4. The only profitable entry model is Breakout/Retest (77.9% win, +0.165% net).

Only 68 trades over 2 years. Hyper-selective but works.

## What Phase 1.5 actually needs

Three changes in order of priority:

### A. Two-stage exit with breakeven-stop after T1

When T1 hits, take 50% profit, move stop to ENTRY (breakeven), let remainder ride to T2 or get stopped at breakeven.

**Expected math impact**:
- Current: avg winner = +1.45% (all positions exit at T1)
- After A: avg winner ≈ (0.5 × 1.45) + (0.5 × X) where X is average post-T1 outcome
  - If post-T1 average is +2% (some hit T2 at +4%, some retrace to 0%): avg winner = +1.7%
  - If post-T1 average is +4% (winners trend strongly): avg winner = +2.7%

Even modest second-half capture gets us close to breakeven.

**Implementation challenge**: requires schema change. New outcome_kinds:
- `WIN_T1_T2` (T1 hit + ran to T2 — full winner)
- `WIN_T1_BE` (T1 hit + remainder breakeven — half winner)
- `WIN_T1_TIME` (T1 hit + time exit on remainder — partial winner)

Existing tests use single-stage semantics; ALL must be updated.

### B. Filter to "no entry_model" rejected

645 of 1015 trades (64%) had `entry_model == ""` — signals that fired a volume condition but didn't match any clean entry pattern (accumulation alone, etc). They lose -0.596% net.

Removing them keeps only the 370 clean-pattern trades. Population:
- Breakout/Retest: 68 trades (+0.165%) — keep
- Momentum: 278 trades (-0.620%) — investigate
- Early Base: 16 trades (-0.598%) — too few, sample noise

### C. Boost score weight on entry_model when present

Currently `entry_model` is recorded but doesn't affect score. Phase 1.5 makes "having a clean entry model" worth +2 score points so the BUY threshold (>=15) is harder to hit without a pattern.

## What we are NOT doing

- ❌ Adding T1 R:R floor (proved counterproductive in local test)
- ❌ Cutting score buckets (all are negative; pruning low buckets doesn't help)
- ❌ Tuning stops (current -5% stop is fine; the problem is target placement)
- ❌ Restructuring scoring weights (the direction signal works — 67% win rate is real)

## Backtest re-run protocol

After Phase 1.5 implementation, validation gates:
- Net expectancy must turn ≥ 0% per trade
- Closed trade count must remain ≥ 200 over 2 years (concentration not over-aggressive)
- Max drawdown ≤ 25%
- Win rate may decline to 50-55% (smaller wins reaching T1 less often) but that's fine if expectancy is positive

## Implementation order

1. Two-stage exit in outcome_tracker.py (with new outcome_kinds + updated tests + schema migration)
2. Empty-entry-model filter in scoring.py
3. entry_model score bonus (+2 when set)
4. Re-run backtest, paste results to Manish
5. If positive → unlock Phase 2.4 + Phase 4 cutover prep
6. If still negative → deeper scoring redesign (different strategy module entirely)

Estimated effort: 1-2 days focused work. Can ship incrementally.
