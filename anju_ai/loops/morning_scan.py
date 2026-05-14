#!/usr/bin/env python3
"""
anju_ai.loops.morning_scan — daily 6:30 IST scan pipeline.

This is the central orchestrator that runs every morning:

    1. refresh   Download missing bhavcopy → data/historical.db
    2. regime    Detect market regime → save to memory.regime_snapshots
    3. scan      Parallel score all universe symbols → top N candidates
    4. catalyst  (Phase 2 LLM) — currently a no-op
    5. paper_fill   Hypothetical T+1 open fills with slippage → memory.fills
    6. digest    Send Telegram message with reasoning per signal
    7. full      All steps in order

Each step writes its outcome to memory.audit so failures are visible.
Each signal lands in memory.signals with a breakdown linked to its
regime snapshot — fully replayable.

Usage:
    python -m anju_ai.loops.morning_scan --step full --universe nifty100
    python -m anju_ai.loops.morning_scan --step scan --mode aggressive
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import requests

# Load .env from project root
ROOT = Path(__file__).resolve().parents[2]
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from anju_core import refresh_daily, get_ohlcv, get_index
from anju_core.regime import detect as detect_regime
from anju_core.universe import get_universe
from anju_ai.tools.scoring import score_signal, ScoreInput, ScoreResult
from anju_ai.tools.paper_fill import simulate_fill, FillInput, classify_segment
from anju_ai.memory.db import init_if_needed, audit_log


# ── Config helpers ────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    """Read YAML without adding PyYAML to required deps for tests."""
    try:
        import yaml
        return yaml.safe_load(path.read_text())
    except Exception:
        return {}


_CFG_RUNTIME = _load_yaml(ROOT / "config" / "runtime.yaml")
_CFG_STRATS  = _load_yaml(ROOT / "config" / "strategies.yaml")

CAPITAL_INR        = float(_CFG_RUNTIME.get("capital", {}).get("total_inr", 17_500_000))
MAX_POSITION_PCT   = float(_CFG_RUNTIME.get("capital", {}).get("max_position_pct", 10))
BASE_RISK_PCT      = float(_CFG_RUNTIME.get("risk", {}).get("base_risk_per_trade_pct", 1.0))
MAX_OPEN_POSITIONS = int(_CFG_RUNTIME.get("positions", {}).get("max_open", 15))


# ── Telegram ──────────────────────────────────────────────────────────────────

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")


def tg_send(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        print("  ⚠️  Telegram creds missing — printing instead:")
        print(text)
        return False
    try:
        # Telegram caps at 4096 chars — split if needed
        for chunk in _split_text(text, 4000):
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": chunk,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=20,
            )
            if not r.ok:
                print(f"  ⚠️  Telegram {r.status_code}: {r.text[:200]}")
                return False
        return True
    except Exception as e:
        print(f"  ⚠️  Telegram send failed: {e}")
        return False


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = (current + "\n" + line) if current else line
    if current:
        chunks.append(current)
    return chunks


# ── Position sizing (v0 — fixed 1% risk; Phase 2 makes Kelly-scaled) ──────────

def _compute_qty(price: float, stop: float | None, total_capital: float = CAPITAL_INR,
                 risk_pct: float = BASE_RISK_PCT,
                 max_pos_pct: float = MAX_POSITION_PCT) -> int:
    """Position size for one trade — capped by max position percentage."""
    if price <= 0:
        return 0
    risk_amount = total_capital * (risk_pct / 100)
    risk_per_share = max(price - (stop or price * 0.95), price * 0.005)
    qty_by_risk = int(risk_amount / risk_per_share)
    qty_by_cap  = int((total_capital * max_pos_pct / 100) / price)
    return max(0, min(qty_by_risk, qty_by_cap))


# ── Pipeline steps ────────────────────────────────────────────────────────────

def step_refresh() -> dict:
    """Refresh historical.db with any missing bhavcopy dates + today's flows."""
    print("[1/7] Refresh historical data + flows...")
    out = {"ok": True}
    try:
        n = refresh_daily(days_back=10, verbose=False)
        out["new_ohlcv_rows"] = n
    except Exception as e:
        out["ohlcv_error"] = str(e)
        out["ok"] = False

    # Fetch + persist today's FII/DII (passive — not yet used in scoring).
    # Failure is non-fatal: scan proceeds without flows data.
    try:
        from anju_ai.tools.flows import fetch_fii_dii, save_flows_snapshot
        snap = fetch_fii_dii()
        if snap is not None:
            con = init_if_needed()
            try:
                rid = save_flows_snapshot(con, snap)
                audit_log(con, "FLOWS_FETCHED",
                          f"#{rid} {snap.snapshot_date} | {snap.signal_strength()}")
                out["flows"] = snap.signal_strength()
            finally:
                con.close()
    except Exception as e:
        out["flows_error"] = str(e)

    # Fetch + persist today's bulk + block deals (passive — Phase 2.4 wires scoring).
    try:
        from anju_ai.tools.deals import fetch_deals, save_deals
        bulk  = fetch_deals("bulk")
        block = fetch_deals("block")
        all_deals = bulk + block
        if all_deals:
            con = init_if_needed()
            try:
                rid = save_deals(con, all_deals)
                audit_log(con, "DEALS_FETCHED",
                          f"#{rid} bulk={len(bulk)} block={len(block)}")
                out["deals"] = f"bulk={len(bulk)} block={len(block)}"
            finally:
                con.close()
    except Exception as e:
        out["deals_error"] = str(e)

    # Fetch + persist last 7 days of insider/SAST disclosures (passive).
    try:
        from anju_ai.tools.insider import fetch_insider, save_insider
        txs = fetch_insider()
        if txs:
            con = init_if_needed()
            try:
                rids = save_insider(con, txs)
                audit_log(con, "INSIDER_FETCHED",
                          f"{len(txs)} transactions across {len(rids)} dates")
                out["insider"] = f"{len(txs)} transactions"
            finally:
                con.close()
    except Exception as e:
        out["insider_error"] = str(e)

    return out


def step_regime() -> dict:
    """Detect market regime + persist to memory.db."""
    print("[2/7] Detect regime...")
    reg = detect_regime(quiet=True)
    con = init_if_needed()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        # UPSERT, not INSERT-OR-REPLACE. INSERT-OR-REPLACE does DELETE+INSERT,
        # which violates FK when child rows in signals already reference this
        # regime_snapshots row. UPSERT updates in place — FKs survive.
        con.execute(
            """INSERT INTO regime_snapshots
                  (snapshot_date, state, min_score, nifty_close, nifty_ma20,
                   nifty_ma50, nifty_ma200, breadth_pct, vol_10d_pct, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapshot_date) DO UPDATE SET
                  state         = excluded.state,
                  min_score     = excluded.min_score,
                  nifty_close   = excluded.nifty_close,
                  nifty_ma20    = excluded.nifty_ma20,
                  nifty_ma50    = excluded.nifty_ma50,
                  nifty_ma200   = excluded.nifty_ma200,
                  breadth_pct   = excluded.breadth_pct,
                  vol_10d_pct   = excluded.vol_10d_pct,
                  payload_json  = excluded.payload_json""",
            (
                today, reg["state"], reg["min_score"],
                reg["data"].get("price", 0),
                reg["data"].get("ma20"), reg["data"].get("ma50"),
                reg["data"].get("ma200"), reg["data"].get("breadth_pct"),
                reg["data"].get("vol_10d_pct"),
                json.dumps(reg, default=str),
            ),
        )
        rid = con.execute(
            "SELECT id FROM regime_snapshots WHERE snapshot_date = ?",
            (today,),
        ).fetchone()
        regime_id = rid["id"] if rid else None
        audit_log(con, "REGIME_DETECTED",
                  f"{reg['emoji']} {reg['state']} (min_score={reg['min_score']})")
    finally:
        con.close()
    return {"ok": True, "regime": reg, "regime_id": regime_id}


def _score_one(symbol: str, nifty_close, mode: str) -> ScoreResult | None:
    """Single-symbol worker for ThreadPoolExecutor."""
    try:
        df = get_ohlcv(symbol, days=500)
        if df is None or len(df) < 60:
            return None
        return score_signal(ScoreInput(symbol=symbol, df=df, mode=mode,
                                       nifty_close=nifty_close))
    except Exception:
        return None


def step_scan(universe: str, mode: str, min_score: float,
              max_workers: int = 8) -> dict:
    """Score every symbol in `universe` in parallel. Returns sorted candidates."""
    print(f"[3/7] Scan {universe} (mode={mode})...")
    symbols = get_universe(universe)
    print(f"      {len(symbols)} symbols to score")

    # Nifty for RS computation
    try:
        nifty_df = get_index("^NSEI", days=60)
        nifty_close = nifty_df["Close"] if nifty_df is not None and not nifty_df.empty else None
    except Exception:
        nifty_close = None

    candidates: list[ScoreResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_score_one, s, nifty_close, mode): s for s in symbols}
        for fut in as_completed(futures):
            try:
                res = fut.result(timeout=60)
            except Exception:
                continue
            if res and res.score >= min_score:
                candidates.append(res)

    candidates.sort(key=lambda r: r.score, reverse=True)
    print(f"      {len(candidates)} above min_score={min_score}")
    return {"ok": True, "candidates": candidates, "scanned": len(symbols)}


def step_catalyst_augment(candidates: list[ScoreResult],
                          top_n: int = 15,
                          catalyst_weight: float = 0.0) -> list[ScoreResult]:
    """Phase 2.6: per-candidate Gemini catalyst review on TOP N candidates.

    Mode is CALIBRATION (catalyst_weight=0.0) until Phase 2.4 backtests the
    real weight. Reviews still run + traces still persist, so we accumulate
    the data needed for calibration without affecting live signals.

    Returns the same candidates list with optional adjustments. Caller can
    pass the result straight to step_persist_signals.
    """
    if not candidates:
        return candidates
    if not os.getenv("GEMINI_API_KEY"):
        print("[3a/7] Catalyst augment skipped — no GEMINI_API_KEY")
        return candidates

    try:
        from anju_ai.tools.catalyst import (
            review_catalyst, apply_catalyst_to_score,
            CatalystReviewInput,
        )
        from anju_ai.llm.gemini import GeminiClient
        from anju_ai.llm.trace import log_trace
    except Exception as e:
        print(f"[3a/7] Catalyst augment skipped — import failed: {e}")
        return candidates

    print(f"[3a/7] Catalyst augment top {min(top_n, len(candidates))} candidates...")
    client = GeminiClient()
    con = init_if_needed()
    try:
        for r in candidates[:top_n]:
            inp = CatalystReviewInput(
                symbol=r.symbol, rule_based_score=r.score,
                # news_24h and filings_7d are placeholder lists until
                # Phase 2 news ingest is fully wired
                news_24h=[], filings_7d=[],
            )
            try:
                resp = review_catalyst(inp, client=client)
                log_trace(con, "catalyst_review", resp,
                          input_payload=inp.model_dump())
                if resp.status == "OK" and resp.parsed is not None:
                    # Apply (with weight=0 default → no effect; capture only)
                    new_score = apply_catalyst_to_score(
                        r.score, resp.parsed, catalyst_weight=catalyst_weight)
                    if new_score != r.score:
                        r.score = new_score
                    # BLOCK signal: set verdict to AVOID + flag
                    if resp.parsed.suggested_action == "BLOCK":
                        r.verdict = "AVOID"
                        r.reasoning = f"BLOCKED by catalyst: {resp.parsed.primary_driver}"
            except Exception as e:
                # Per-symbol failure non-fatal
                audit_log(con, "CATALYST_FAILED",
                          f"{r.symbol}: {type(e).__name__}")
                continue
        audit_log(con, "CATALYST_BATCH",
                  f"Reviewed top {min(top_n, len(candidates))} candidates "
                  f"(weight={catalyst_weight} — calibration mode)")
    finally:
        con.close()
    candidates.sort(key=lambda r: r.score, reverse=True)
    return candidates


def step_persist_signals(candidates: list[ScoreResult], regime_id: int,
                         horizon: str = "SWING") -> dict:
    """Write top candidates to memory.signals + paper-fill them."""
    print(f"[4/7] Persist top {MAX_OPEN_POSITIONS} signals to memory.db...")
    top = candidates[:MAX_OPEN_POSITIONS]
    con = init_if_needed()
    signal_ids = []
    try:
        for r in top:
            qty = _compute_qty(r.price, r.exit_logic.stop if r.exit_logic else None)
            stop = r.exit_logic.stop if r.exit_logic else round(r.price * 0.95, 2)
            t1 = r.exit_logic.partial_target if r.exit_logic else None
            t2 = r.exit_logic.full_target if r.exit_logic else None

            cur = con.execute("""
                INSERT INTO signals
                  (signal_date, symbol, horizon, regime_id, rule_score, final_score,
                   verdict, entry_price, suggested_stop, suggested_t1, suggested_t2,
                   suggested_qty, suggested_instrument, breakdown_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CASH', ?)
            """, (
                datetime.now().strftime("%Y-%m-%d"),
                r.symbol, horizon, regime_id,
                r.score, r.score, r.verdict,
                r.price, stop, t1, t2, qty,
                json.dumps(r.breakdown),
            ))
            signal_ids.append((cur.lastrowid, r))
        audit_log(con, "SIGNALS_GENERATED",
                  f"Persisted {len(signal_ids)} signals (top of {len(candidates)})")
    finally:
        con.close()
    return {"ok": True, "signal_ids": signal_ids}


def step_paper_fill(signal_ids_with_results: list, today: str | None = None) -> dict:
    """For each persisted signal, simulate a T+1 open fill. Phase 0 uses the
    SAME-day df (no future data available yet) — Phase 1 wires the next-day
    open as the actual fill once outcomes.yml fires."""
    print(f"[5/7] Paper-fill {len(signal_ids_with_results)} signals...")
    con = init_if_needed()
    fills = 0
    try:
        for sig_id, r in signal_ids_with_results:
            qty = _compute_qty(r.price, r.exit_logic.stop if r.exit_logic else None)
            if qty <= 0:
                continue
            # Use today's row as the "fill" reference. Phase 1 will use T+1 open.
            seg = classify_segment(r.price, avg_volume_10d=None)
            base_slip = _CFG_RUNTIME.get("costs", {}).get("slippage_pct_by_segment", {})
            base_slippage = float(base_slip.get(seg, 0.15))

            # No post-signal data on signal day; modelled slippage applied to
            # signal-time price as proxy.
            slip_pct = base_slippage
            fill_price = round(r.price * (1 + slip_pct / 100), 2)
            gross = round(fill_price * qty, 2)
            slip_inr = round((fill_price - r.price) * qty, 2)

            con.execute("""
                INSERT INTO fills
                  (signal_id, fill_date, fill_price, fill_qty, instrument,
                   gross_value, cost_slippage, cost_total, is_paper)
                VALUES (?, ?, ?, ?, 'CASH', ?, ?, ?, 1)
            """, (
                sig_id,
                today or datetime.now().strftime("%Y-%m-%d"),
                fill_price, qty, gross, slip_inr, slip_inr,
            ))
            fills += 1
        audit_log(con, "PAPER_FILLS",
                  f"Filled {fills}/{len(signal_ids_with_results)} signals")
    finally:
        con.close()
    return {"ok": True, "fills": fills}


def step_digest(candidates: list[ScoreResult], regime: dict, scanned: int,
                top_n: int = 10) -> dict:
    """Send formatted Telegram digest."""
    print(f"[6/7] Send Telegram digest...")
    now = datetime.now().strftime("%d %b %Y %H:%M")
    top = candidates[:top_n]

    header = (
        f"🌅 <b>anju-AI · Morning Scan</b>  ·  PAPER\n"
        f"{regime['emoji']} <b>{regime['state']}</b>  "
        f"(min_score {regime['min_score']})\n"
        f"<i>{regime['label']}</i>\n"
        f"Scanned {scanned} symbols · {len(candidates)} above threshold · "
        f"showing top {len(top)}\n"
        f"<code>{now} IST</code>\n"
    )

    if not top:
        body = "\n<i>No signals above threshold today. Cash is a position.</i>"
        tg_send(header + body)
        return {"ok": True, "sent": 0}

    body_parts = []
    for i, r in enumerate(top, 1):
        sl = r.exit_logic.stop if r.exit_logic else "—"
        t1 = r.exit_logic.partial_target if r.exit_logic else "—"
        rr = r.exit_logic.rr if r.exit_logic else "—"
        qty = _compute_qty(r.price, r.exit_logic.stop if r.exit_logic else None)
        verdict_emoji = "🟢" if r.verdict == "BUY" else "🟡" if r.verdict == "WATCH" else "🔴"
        tags_str = "  ".join(r.tags[:4]) if r.tags else ""

        body_parts.append(
            f"\n<b>#{i}  {r.symbol}</b>  {verdict_emoji} <b>{r.verdict}</b>  "
            f"score <b>{r.score:.0f}</b>\n"
            f"  ₹{r.price}   SL ₹{sl}  T1 ₹{t1}  R:R {rr}\n"
            f"  Qty {qty}  ·  {r.entry_model or '—'}\n"
            f"  {tags_str}\n"
            f"  <i>{r.reasoning}</i>"
        )

    msg = header + "".join(body_parts) + (
        f"\n\n<i>📋 {len(candidates)} signals saved to memory.db · "
        f"PAPER mode (live=false) · "
        f"Phase 0 v0 scoring — backtest validation pending</i>"
    )
    sent = tg_send(msg)
    return {"ok": sent, "sent": len(top)}


# ── Entry point ───────────────────────────────────────────────────────────────

def run(step: str, universe: str = "nifty100", mode: str = "auto",
        min_score_override: str = "", replay_date: str = "",
        catalyst_llm: bool = True, paper_only: bool = True) -> int:
    """Run a single step or 'full' pipeline. Returns exit code (0 = OK)."""
    print(f"\n🌱 anju-AI morning_scan  step={step}  universe={universe}  "
          f"mode={mode}  {datetime.now().strftime('%H:%M IST')}\n")

    # Note: 'catalyst' as a standalone step would need to load candidates
    # from a prior scan — usually we run it inline as part of 'full'.
    if step == "catalyst":
        print("Catalyst is inlined into 'full' step (see step_catalyst_augment)")
        return 0

    try:
        regime_id, regime = None, None
        candidates: list[ScoreResult] = []

        if step in ("refresh", "full"):
            r = step_refresh()
            if not r["ok"]:
                print(f"  ⚠️  Refresh failed (continuing): {r.get('error')}")

        if step in ("regime", "full", "scan", "digest"):
            r = step_regime()
            regime = r["regime"]
            regime_id = r["regime_id"]
            print(f"      → {regime['emoji']} {regime['state']}")

        if step in ("scan", "full"):
            actual_mode = (regime["scanner_mode"] if mode == "auto" and regime
                           else (mode if mode != "auto" else "strict"))
            min_score = (float(min_score_override) if min_score_override
                         else (regime["min_score"] if regime else 6))
            r = step_scan(universe, actual_mode, min_score)
            candidates = r["candidates"]
            scanned = r["scanned"]

            # Phase 2.6: catalyst augment — calibration mode (weight=0)
            # until backtest validates the actual weight. Captures traces
            # so we accumulate data for Phase 2.4 backtest of catalyst
            # predictive value.
            if catalyst_llm and candidates:
                candidates = step_catalyst_augment(
                    candidates, top_n=15, catalyst_weight=0.0)

            if regime_id is not None:
                persisted = step_persist_signals(candidates, regime_id)
                step_paper_fill(persisted["signal_ids"])

            if step == "full":
                step_digest(candidates, regime, scanned)

        elif step == "digest":
            # Standalone digest — needs a prior scan to have happened
            print("[6/7] Digest-only mode reads memory.db (not implemented yet)")

        print("\n✅ done")
        return 0

    except Exception as e:
        traceback.print_exc()
        tg_send(f"❌ <b>anju-AI morning_scan crashed</b>\n<code>{e}</code>")
        return 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--step", default="full",
                   choices=["refresh", "regime", "scan", "catalyst",
                            "paper_fill", "digest", "full"])
    p.add_argument("--universe", default="nifty100")
    p.add_argument("--mode", default="auto")
    p.add_argument("--min-score", default="")
    p.add_argument("--replay-date", default="")
    p.add_argument("--paper-only", default="true")
    p.add_argument("--catalyst-llm", default="true")
    args = p.parse_args()

    return run(
        step=args.step, universe=args.universe, mode=args.mode,
        min_score_override=args.min_score, replay_date=args.replay_date,
        catalyst_llm=args.catalyst_llm.lower() == "true",
        paper_only=args.paper_only.lower() == "true",
    )


if __name__ == "__main__":
    sys.exit(main())
