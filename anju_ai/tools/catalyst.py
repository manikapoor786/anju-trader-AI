#!/usr/bin/env python3
"""
anju_ai.tools.catalyst — LLM-driven catalyst review for one candidate signal.

Implements the catalyst_review agent loop from AGENT_PROTOCOL.md §2.1.
Typed inputs + outputs, prompt versioned in anju_ai/llm/prompts/.

Mode is CALIBRATION until Phase 2.4 validates predictive value:
  - catalyst_score is computed and stored
  - it is multiplied into final_score by `catalyst_weight` (config-driven)
  - default catalyst_weight = 0.0 (no effect on score) until backtest
    validates a non-zero weight

Usage:
    from anju_ai.tools.catalyst import review_catalyst, CatalystReviewInput
    out = review_catalyst(CatalystReviewInput(
        symbol="RELIANCE", company_name="Reliance Industries",
        sector="Energy", rule_based_score=18.5,
        news_24h=[...], filings_7d=[...], earnings_calendar=None,
        open_position=None,
    ))
    # out.catalyst_score, out.confidence, out.flags, ...
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from anju_ai.llm.base import LLMResponse
from anju_ai.llm.gemini import GeminiClient


# ── Typed I/O (matches AGENT_PROTOCOL §2.1) ───────────────────────────────────

class NewsItem(BaseModel):
    title:        str
    source:       str = ""
    url:          str = ""
    snippet:      str = ""
    published_at: str = ""


class FilingItem(BaseModel):
    kind:     str        # 'RESULTS' | 'BOARD_MEETING' | 'CORPORATE_ACTION' | 'REGULATORY'
    headline: str
    url:      str = ""
    filed_at: str = ""


class EarningsItem(BaseModel):
    next_date:        str = ""        # 'YYYY-MM-DD'
    consensus_eps:    float | None = None
    last_q_beat_pct:  float | None = None    # last quarter actual vs consensus


class PositionContext(BaseModel):
    qty:            int
    entry_price:    float
    days_held:      int
    pnl_pct:        float


class CatalystReviewInput(BaseModel):
    symbol:             str
    company_name:       str = ""
    sector:             str = ""
    rule_based_score:   float
    news_24h:           list[NewsItem] = Field(default_factory=list)
    filings_7d:         list[FilingItem] = Field(default_factory=list)
    earnings_calendar:  EarningsItem | None = None
    open_position:      PositionContext | None = None


class CatalystReviewOutput(BaseModel):
    catalyst_score:    float = Field(ge=-1.0, le=1.0)
    confidence:        float = Field(ge=0.0, le=1.0)
    primary_driver:    str
    reasoning:         str
    flags:             list[str] = Field(default_factory=list)
    suggested_action:  Literal["STRENGTHEN", "NEUTRAL", "WEAKEN", "BLOCK"]


# ── Prompt loader ─────────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"


def _load_prompt(name: str, version: int) -> str:
    """Load the markdown prompt file. Strips frontmatter, returns body."""
    path = _PROMPTS_DIR / f"{name}.v{version}.md"
    text = path.read_text()
    # Strip YAML frontmatter (between leading ---\n and the next ---\n)
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    return text.strip()


# ── Render input → prompt text ────────────────────────────────────────────────

def _render_input(inp: CatalystReviewInput) -> str:
    """Compact, deterministic rendering of CatalystReviewInput for the LLM.
    Don't dump JSON — the model handles structured prose better."""
    out = []
    out.append(f"## STOCK: {inp.symbol}  ({inp.company_name or '—'})")
    if inp.sector:
        out.append(f"Sector: {inp.sector}")
    out.append(f"Rule-based score: {inp.rule_based_score:.1f}/100")

    if inp.earnings_calendar and inp.earnings_calendar.next_date:
        out.append(f"\n## EARNINGS")
        e = inp.earnings_calendar
        out.append(f"Next earnings: {e.next_date}")
        if e.consensus_eps is not None:
            out.append(f"Consensus EPS: {e.consensus_eps}")
        if e.last_q_beat_pct is not None:
            out.append(f"Last quarter beat/miss: {e.last_q_beat_pct:+.1f}%")

    if inp.filings_7d:
        out.append(f"\n## CORPORATE FILINGS (last 7 days, {len(inp.filings_7d)})")
        for f in inp.filings_7d[:8]:
            out.append(f"- [{f.kind}] {f.headline}  ({f.filed_at})")
    else:
        out.append("\n## CORPORATE FILINGS: none in last 7 days")

    if inp.news_24h:
        out.append(f"\n## NEWS HEADLINES (last 24h, {len(inp.news_24h)})")
        for n in inp.news_24h[:10]:
            src = f" [{n.source}]" if n.source else ""
            snippet = f" — {n.snippet}" if n.snippet else ""
            out.append(f"- {n.title}{src}{snippet}")
    else:
        out.append("\n## NEWS HEADLINES: none in last 24h")

    if inp.open_position:
        p = inp.open_position
        out.append(f"\n## OPEN POSITION CONTEXT")
        out.append(f"Held {p.days_held}d, entry ₹{p.entry_price:.2f}, "
                   f"P&L {p.pnl_pct:+.1f}%, qty {p.qty}")

    return "\n".join(out)


# ── Public API ────────────────────────────────────────────────────────────────

def review_catalyst(inp: CatalystReviewInput,
                    client=None,
                    prompt_version: int = 1) -> LLMResponse:
    """Run the catalyst_review agent loop. Returns LLMResponse — parsed is
    CatalystReviewOutput on success, None otherwise. Caller is responsible
    for logging the trace via anju_ai.llm.trace.log_trace.
    """
    if client is None:
        client = GeminiClient()
    system_prompt = _load_prompt("catalyst_review", prompt_version)
    rendered_input = _render_input(inp)
    full_prompt = system_prompt + "\n\n" + rendered_input

    return client.complete(
        prompt=full_prompt,
        schema=CatalystReviewOutput,
        model="gemini-1.5-flash",
        prompt_name="catalyst_review",
        prompt_version=prompt_version,
        max_tokens_in=1500,
        max_tokens_out=400,
        temperature=0.2,
        timeout_s=30.0,
    )


def apply_catalyst_to_score(rule_score: float,
                            catalyst: CatalystReviewOutput | None,
                            catalyst_weight: float = 0.0) -> float:
    """Combine rule_score with catalyst signal.

    Formula: final_score = rule_score × (1 + catalyst_score × catalyst_weight)

    With catalyst_weight=0.0 (Phase 2.5 calibration default) → catalyst is
    captured but has no effect on final score. Phase 2.4 backtest assigns
    the real weight (probably 0.1–0.3 based on predictive value).
    """
    if catalyst is None or catalyst.suggested_action == "BLOCK":
        return -1.0 if (catalyst and catalyst.suggested_action == "BLOCK") else rule_score
    if catalyst_weight == 0.0:
        return rule_score
    return rule_score * (1.0 + catalyst.catalyst_score * catalyst_weight)
