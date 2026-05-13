"""Tools — pure functions the agent calls.

Rules (enforced by code review):
    1. Tools are pure functions of their (typed) inputs.
    2. Tools may read memory (SELECT) but must not write — writes happen in the calling loop.
    3. Tools never call the LLM. The LLM lives only in anju_ai.agent.
    4. Tools never send Telegram. That's anju_ai.tg.
    5. Every tool has typed input + typed output dataclasses (Pydantic).
    6. Every tool has a unit test in tests/.

Planned tools (Phase 0–3):
    scoring.py             Composite score for one symbol
    position_sizing.py     Kelly + score × regime × correlation
    costs.py               Round-trip cost model (₹, IST tax slabs)
    outcome_tracker.py     Event-driven WIN/LOSS detection
    flows.py               FII/DII + bulk/block + promoter ingest
    catalyst.py            (Phase 2) Wrapper around the catalyst LLM agent
    options.py             (Phase 2) IV percentile + ATM call recommender
    backtest.py            Walk-forward backtest with full cost model
    paper_fill.py          Simulated fills with modelled slippage
    correlation.py         Correlation matrix among open positions
    regime_proxy.py        Convenience wrapper around anju_core.regime
"""
