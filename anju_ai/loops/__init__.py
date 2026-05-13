"""Reasoning loops — the cadenced jobs that drive the system.

Each loop is invoked by a GitHub Actions workflow under .github/workflows/.

Phase 0:
    morning_scan.py        Daily 6:30 AM IST — rule-based signals + paper fills
    paper_book.py          On-demand portfolio snapshot

Phase 1:
    eod_close.py           4 PM IST — event-driven outcome closure
    backtest.py            On-demand walk-forward backtest

Phase 2:
    intraday_monitor.py    Every 30 min during market hours
    catalyst_augment.py    Called from morning_scan after rule scoring

Phase 3:
    eod_postmortem.py      Post-mortem LLM after each closed trade
    weekly_critic.py       Sunday 9 AM IST — strategy critic
    anomaly_qa.py          Every 3 hours — system health LLM
    deep_review.py         On-demand single-symbol deep review

Each loop:
    1. Loads runtime.yaml + strategies.yaml + llm.yaml
    2. Opens memory.db with WAL journal mode
    3. Logs start in audit table
    4. Does its work (calling tools + agent.* functions)
    5. Logs end + outcome in audit table
    6. Always exits cleanly (no traceback to the runner)
"""
