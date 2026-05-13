"""anju_ai — the agentic brain.

Layered architecture (lower never imports higher):

    memory/    Persistent reasoning state (SQLite)
    tools/     Pure functions the agent can call — deterministic, testable
    llm/       LLM client adapters + versioned prompts
    loops/     Cadenced reasoning jobs (morning, intraday, EOD, weekly, anomaly)
    tg/        Telegram delivery + interactive reply handling

The LLM lives only at the agent-orchestrator level (anju_ai.agent).
Tools never call LLMs. Memory never sees prompts.

See docs/ARCHITECTURE.md and docs/AGENT_PROTOCOL.md for the full design.
"""

__version__ = "0.0.1"
