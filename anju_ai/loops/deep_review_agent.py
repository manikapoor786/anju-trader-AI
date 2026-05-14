#!/usr/bin/env python3
"""
anju_ai.loops.deep_review_agent — on-demand deep symbol review.

Per AGENT_PROTOCOL §2.5 / ROADMAP 3.5. Triggered from Manish's phone
via manual_review.yml. Pulls comprehensive context for ONE symbol and
asks Gemini Pro for a bull/bear/base-case decomposition.

This module is named `deep_review_agent` to avoid clashing with the
existing `deep_review.py` connectivity stub from Phase 0.

Usage (via workflow):
    python -m anju_ai.loops.deep_review_agent --symbol RELIANCE \
        --horizon BOTH --question "Is the breakout sustainable?"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from anju_core import get_ohlcv
from anju_ai.memory.db import audit_log, init_if_needed
from anju_ai.llm.gemini import GeminiClient
from anju_ai.llm.base import LLMResponse, OK
from anju_ai.llm.trace import log_trace
from anju_ai.tools.scoring import score_signal, ScoreInput
from anju_ai.tools.insider import insider_signal_for_symbol
from anju_ai.tools.deals import get_deals_for_symbol


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class KeyLevels(BaseModel):
    support: float
    resistance: float
    invalidation: float


class OptionsRecommendation(BaseModel):
    instrument: Literal["ATM_CALL", "ATM_PUT"]
    rationale: str


class DeepReviewInput(BaseModel):
    symbol: str
    company_name: str = ""
    horizon: Literal["SWING_1_4W", "POSITIONAL_1_3M", "BOTH"]
    user_question: str = ""
    rule_based_score: float
    rule_based_verdict: str
    daily_summary: dict           # last 252 bars compressed: close/high/low ranges, recent moves
    weekly_summary: dict
    hourly_summary: dict          # last 60 days hourly
    flows_summary: dict           # last 30d FII/DII + bulk/block/insider net
    news_30d: list[dict]
    filings_90d: list[dict]
    similar_past_trades: list[dict]


class DeepReviewOutput(BaseModel):
    bull_case: list[str] = Field(min_length=1, max_length=8)
    bear_case: list[str] = Field(min_length=1, max_length=8)
    base_case_outcome: str
    swing_verdict: Literal["BUY", "WATCH", "AVOID"]
    positional_verdict: Literal["BUY", "WATCH", "AVOID"]
    key_levels: KeyLevels
    options_recommendation: OptionsRecommendation | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    blind_spots: list[str] = Field(min_length=1, max_length=6)


# ── Telegram ─────────────────────────────────────────────────────────────────

def tg_send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print(text)
        return False
    try:
        for chunk in _split_text(text, 4000):
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": chunk,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=20,
            )
            if not r.ok:
                return False
        return True
    except Exception:
        return False


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            out.append(cur); cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        out.append(cur)
    return out


# ── Snapshot builders (compress OHLCV to fit in prompt budget) ──────────────

def _df_summary(df, label: str) -> dict:
    """Compress an OHLCV df into a few headline numbers for the LLM."""
    if df is None or df.empty or len(df) < 5:
        return {"label": label, "bars": 0, "summary": "no data"}
    import pandas as pd
    cur = float(df["Close"].iloc[-1])
    high_52 = float(df["High"].iloc[-min(252, len(df)):].max())
    low_52  = float(df["Low"].iloc[-min(252, len(df)):].min())
    move_30 = ((cur - float(df["Close"].iloc[-min(30, len(df))])) /
               float(df["Close"].iloc[-min(30, len(df))]) * 100)
    avg_vol = float(df["Volume"].tail(20).mean())
    return {
        "label": label,
        "bars": len(df),
        "current_price": round(cur, 2),
        "52w_high": round(high_52, 2),
        "52w_low": round(low_52, 2),
        "30bar_move_pct": round(move_30, 2),
        "from_52w_high_pct": round((cur - high_52) / high_52 * 100, 2),
        "avg_vol_20": int(avg_vol),
    }


def build_input(symbol: str, horizon: str, user_question: str,
                con) -> DeepReviewInput | None:
    """Pull everything we have on this symbol from local data + memory.db."""
    bare = symbol.upper().replace(".NS", "")
    sym_ns = bare + ".NS"

    # Daily OHLCV + scoring snapshot
    df_d = get_ohlcv(sym_ns, days=500)
    if df_d is None or df_d.empty:
        return None

    score_result = score_signal(ScoreInput(symbol=sym_ns, df=df_d, mode="strict"))
    rule_score   = score_result.score if score_result else 0.0
    rule_verdict = score_result.verdict if score_result else "AVOID"

    # Weekly resampled
    import pandas as pd
    df_w = df_d.resample("W").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna()

    # Hourly — Phase 2.7+. Not in historical.db; placeholder.
    df_h = None

    # Flows (last 30d)
    insider_sig = insider_signal_for_symbol(con, bare, days_back=30)
    deals = get_deals_for_symbol(con, bare, days_back=30)
    flows = {
        "insider": insider_sig,
        "bulk_block_deals": len(deals),
        "recent_deals_sample": deals[:5],
    }

    # News + filings — Phase 2 ingest writes these to news_items / filings.
    # For Phase 3.5 stub we read what's there (could be empty until ingest is wired).
    news_rows = con.execute(
        """SELECT title, source, published_at, snippet
             FROM news_items
            WHERE UPPER(symbol) = ?
              AND published_at >= datetime('now', '-30 days')
            ORDER BY published_at DESC LIMIT 15""", (bare,)
    ).fetchall()
    filings_rows = con.execute(
        """SELECT kind, headline, filed_at
             FROM filings
            WHERE UPPER(symbol) = ?
              AND filed_at >= datetime('now', '-90 days')
            ORDER BY filed_at DESC LIMIT 10""", (bare,)
    ).fetchall()

    # Similar past trades (rule-score band ±5, any kind)
    similar_rows = con.execute(
        """SELECT s.symbol, s.final_score, o.outcome_kind, o.net_pnl_pct,
                  o.days_held, l.lesson
             FROM outcomes o
             JOIN fills f ON o.fill_id = f.id
             JOIN signals_current s ON f.signal_id = s.id
             LEFT JOIN lessons l ON l.outcome_id = o.id
            WHERE s.final_score BETWEEN ? AND ?
              AND s.backtest_run_id IS NULL
            ORDER BY o.id DESC LIMIT 5""",
        (rule_score - 5, rule_score + 5)
    ).fetchall()

    return DeepReviewInput(
        symbol=bare,
        horizon=horizon,
        user_question=user_question,
        rule_based_score=rule_score,
        rule_based_verdict=rule_verdict,
        daily_summary=_df_summary(df_d, "1D"),
        weekly_summary=_df_summary(df_w, "1W"),
        hourly_summary=_df_summary(df_h, "1H"),
        flows_summary=flows,
        news_30d=[dict(r) for r in news_rows],
        filings_90d=[dict(r) for r in filings_rows],
        similar_past_trades=[dict(r) for r in similar_rows],
    )


# ── Prompt + render ──────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"


def _load_prompt(name: str, version: int) -> str:
    text = (_PROMPTS_DIR / f"{name}.v{version}.md").read_text()
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    return text.strip()


def _render(inp: DeepReviewInput) -> str:
    parts = [
        f"## SYMBOL: {inp.symbol}  ·  Horizon: {inp.horizon}",
        f"Rule-based score: {inp.rule_based_score:.1f}/100  ·  "
        f"Verdict: {inp.rule_based_verdict}",
    ]
    if inp.user_question:
        parts.append(f"\n## SPECIFIC QUESTION FROM USER\n{inp.user_question}")

    parts.append(f"\n## DAILY SUMMARY\n{json.dumps(inp.daily_summary, indent=2)}")
    parts.append(f"\n## WEEKLY SUMMARY\n{json.dumps(inp.weekly_summary, indent=2)}")
    parts.append(f"\n## HOURLY SUMMARY\n{json.dumps(inp.hourly_summary, indent=2)}")
    parts.append(f"\n## FLOWS (last 30d)\n{json.dumps(inp.flows_summary, indent=2, default=str)}")

    parts.append(f"\n## NEWS (last 30d, {len(inp.news_30d)} items)")
    if inp.news_30d:
        for n in inp.news_30d[:10]:
            parts.append(f"- {n.get('title', '')}  [{n.get('source', '')}]")
    else:
        parts.append("- (no news in DB)")

    parts.append(f"\n## FILINGS (last 90d, {len(inp.filings_90d)} items)")
    if inp.filings_90d:
        for f in inp.filings_90d[:6]:
            parts.append(f"- [{f.get('kind', '')}] {f.get('headline', '')}")
    else:
        parts.append("- (no filings in DB)")

    parts.append(f"\n## SIMILAR PAST TRADES ({len(inp.similar_past_trades)})")
    if inp.similar_past_trades:
        for t in inp.similar_past_trades:
            lesson = t.get("lesson", "—") or "—"
            parts.append(
                f"- {t.get('symbol', '?')}  score {t.get('final_score', 0):.1f}  "
                f"{t.get('outcome_kind', '?')}  {t.get('net_pnl_pct', 0):+.1f}%  "
                f"{t.get('days_held', 0)}d — {lesson[:80]}"
            )
    else:
        parts.append("- (no similar past closed trades in same score band)")

    return "\n".join(parts)


def review(inp: DeepReviewInput, client=None,
           prompt_version: int = 1) -> LLMResponse:
    if client is None:
        client = GeminiClient()
    prompt = _load_prompt("deep_review", prompt_version) + "\n\n" + _render(inp)
    return client.complete(
        prompt=prompt, schema=DeepReviewOutput,
        model="gemini-1.5-pro",   # Pro for deeper analysis
        prompt_name="deep_review", prompt_version=prompt_version,
        max_tokens_in=10000, max_tokens_out=3000,
        temperature=0.3, timeout_s=60.0,
    )


def render_report_telegram(symbol: str, out: DeepReviewOutput) -> str:
    lines = [f"🔬 <b>anju-AI · Deep Review</b>  ·  {symbol}"]
    lines.append(f"Confidence: <b>{out.confidence:.0%}</b>")
    lines.append(f"\n<b>Swing (1-4w)</b>: {_emoji(out.swing_verdict)} "
                 f"<b>{out.swing_verdict}</b>")
    lines.append(f"<b>Positional (1-3m)</b>: {_emoji(out.positional_verdict)} "
                 f"<b>{out.positional_verdict}</b>")

    lines.append(f"\n<b>Bull case</b>")
    for b in out.bull_case:
        lines.append(f"  🟢 {b}")
    lines.append(f"\n<b>Bear case</b>")
    for b in out.bear_case:
        lines.append(f"  🔴 {b}")

    lines.append(f"\n<b>Base case</b>: <i>{out.base_case_outcome}</i>")

    k = out.key_levels
    lines.append(f"\n<b>Key levels</b>")
    lines.append(f"  Support: ₹{k.support:.2f}  ·  Resistance: ₹{k.resistance:.2f}")
    lines.append(f"  Invalidation: ₹{k.invalidation:.2f}")

    if out.options_recommendation:
        opt = out.options_recommendation
        lines.append(f"\n<b>Options</b>: {opt.instrument}")
        lines.append(f"  <i>{opt.rationale}</i>")

    lines.append(f"\n<b>Blind spots</b>")
    for b in out.blind_spots:
        lines.append(f"  ⚠️ {b}")
    return "\n".join(lines)


def _emoji(v: str) -> str:
    return "🟢" if v == "BUY" else "🟡" if v == "WATCH" else "🔴"


# ── Main ─────────────────────────────────────────────────────────────────────

def run(symbol: str, horizon: str, question: str = "") -> int:
    print(f"\n🔬 anju-AI deep_review_agent  {symbol}  horizon={horizon}\n")
    con = init_if_needed()
    try:
        inp = build_input(symbol, horizon, question, con)
        if inp is None:
            msg = f"❌ <b>Deep Review {symbol}</b>: insufficient data"
            tg_send(msg)
            return 1

        resp = review(inp)
        log_trace(con, "deep_review", resp, input_payload=inp.model_dump())

        if resp.status != OK or resp.parsed is None:
            tg_send(f"❌ <b>Deep Review {symbol}</b>: LLM error "
                    f"({resp.status}) — {resp.error_message[:200]}")
            audit_log(con, "DEEP_REVIEW_FAILED",
                      f"{symbol}: {resp.error_message}", severity="WARN")
            return 1

        tg_send(render_report_telegram(symbol, resp.parsed))
        audit_log(con, "DEEP_REVIEW",
                  f"{symbol} swing={resp.parsed.swing_verdict} "
                  f"pos={resp.parsed.positional_verdict} "
                  f"confidence={resp.parsed.confidence:.2f}")
    finally:
        con.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--horizon", default="BOTH",
                   choices=["SWING_1_4W", "POSITIONAL_1_3M", "BOTH"])
    p.add_argument("--question", default="")
    args = p.parse_args()
    return run(symbol=args.symbol, horizon=args.horizon, question=args.question)


if __name__ == "__main__":
    sys.exit(main())
