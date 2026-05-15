#!/usr/bin/env python3
"""
anju_ai.tools.backtest — walk-forward backtest engine.

Replays the actual production scoring (anju_ai.tools.scoring.score_signal)
against historical OHLCV data day-by-day. Applies modelled slippage on
T+1 open fills, event-driven outcomes via outcome_tracker, and full
round-trip costs via tools.costs.

Output: comprehensive expectancy report sliced by score bucket, regime,
entry model, and universe segment — the answer to "is our edge real?".

The audit Finding 3.2 ("scoring weights never backtested") gets resolved
by running this against 2 years of bhavcopy. If positive cost-adjusted
expectancy survives, the system is real. If not, we know which buckets
to cut before deploying capital.

Forms a closed feedback loop:
    score_signal()  → paper_fill  → outcome_tracker  → costs  → report

Usage (via loops/backtest_run.py):
    python -m anju_ai.loops.backtest_run --start 2024-05-01 --end 2026-05-01
"""

from __future__ import annotations

import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from anju_ai.tools.costs import net_pnl
from anju_ai.tools.outcome_tracker import track_outcome, TrackInput
from anju_ai.tools.paper_fill import classify_segment
from anju_ai.tools.scoring import score_signal, ScoreInput, ScoreResult


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class BacktestInput(BaseModel):
    """Inputs to one backtest run."""
    name: str
    start_date: str                       # 'YYYY-MM-DD'
    end_date: str
    universe_symbols: list[str]
    mode: Literal["strict", "aggressive"] = "strict"
    min_score: float = 6.0                # below this → no signal
    max_open_positions: int = 15
    capital_inr: float = 17_500_000
    base_risk_pct: float = 1.0
    max_position_pct: float = 10.0
    max_hold_days: int = 90
    apply_costs: bool = True
    slippage_pct_buy: float = 0.15        # midcap default
    # Phase 1.6: when True (default), only verdict=BUY signals get filled.
    # WATCH/AVOID candidates are scored + traced but never deployed.
    # Tests can set False to verify close-loop logic independently.
    respect_verdict_gate: bool = True
    # Phase 1.7: when True (default), reject fills where T1 ended up within
    # 0.5% of fill price (typically due to gap-up overshooting the swing
    # high used as T1). This is Layer 2 of the T1-distance defence;
    # Layer 1 is the signal-time +1.5% buffer in scoring.py.
    respect_t1_distance: bool = True
    base_segment: str = "midcap"          # used for cost calc
    max_workers: int = 8                  # parallelism for symbol scoring


class TradeRecord(BaseModel):
    """One closed round-trip in the backtest."""
    signal_date: str
    symbol: str
    score: float
    verdict: str
    entry_model: str
    fill_date: str
    fill_price: float
    qty: int
    stop: float
    t1: float | None
    t2: float | None
    exit_date: str | None
    exit_price: float | None
    outcome_kind: str
    days_held: int
    gross_pnl_pct: float
    costs_pct: float
    net_pnl_pct: float
    mfe_pct: float
    mae_pct: float


class BacktestReport(BaseModel):
    """The answer to 'is our edge real?'"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    start_date: str
    end_date: str
    universe_size: int
    days_scanned: int

    # Headline metrics
    total_signals: int
    total_fills: int
    total_closed: int
    total_open: int

    # Win/loss breakdown
    by_outcome_kind: dict[str, int]
    win_rate_pct: float
    avg_winner_pct: float
    avg_loser_pct: float
    rr_realized: float                 # avg_win / abs(avg_loss)

    # Expectancy
    gross_expectancy_pct: float
    net_expectancy_pct: float          # the number that matters
    expectancy_inr_per_trade: float

    # Equity / drawdown
    final_equity_pct: float            # total return on starting capital
    max_drawdown_pct: float

    # Slices
    by_score_bucket: dict[str, dict]   # {"15-19": {trades, win_rate, net_exp}, ...}
    by_entry_model: dict[str, dict]
    by_segment: dict[str, dict]
    by_month: dict[str, dict]

    # Trade samples
    best_trades: list[TradeRecord]
    worst_trades: list[TradeRecord]

    # Run metadata
    config: dict


# ── Internal helpers ──────────────────────────────────────────────────────────

def _trading_days(ohlcv_loader: Callable, symbol: str,
                  start_date: str, end_date: str) -> list[pd.Timestamp]:
    """Use one liquid reference symbol to get the actual trading-day index
    within the backtest window. Falls back to weekday filter if no data."""
    try:
        df = ohlcv_loader(symbol, days=1000)
        if df is None or df.empty:
            raise ValueError
        df.index = pd.to_datetime(df.index)
        mask = (df.index >= pd.to_datetime(start_date)) & (df.index <= pd.to_datetime(end_date))
        days = df.index[mask].tolist()
        if days:
            return days
    except Exception:
        pass
    # Fallback: weekday range
    return list(pd.bdate_range(start=start_date, end=end_date))


def _score_one_at_date(symbol: str, df_full: pd.DataFrame,
                       as_of_date: pd.Timestamp, mode: str,
                       nifty_close: pd.Series | None) -> tuple[str, ScoreResult | None]:
    """Score one symbol using data strictly up to (and including) as_of_date.
    Returns (symbol, ScoreResult or None)."""
    try:
        df_full.index = pd.to_datetime(df_full.index)
        df = df_full[df_full.index <= as_of_date]
        if len(df) < 60:
            return symbol, None
        nf = None
        if nifty_close is not None:
            try:
                nifty_close.index = pd.to_datetime(nifty_close.index)
                nf = nifty_close[nifty_close.index <= as_of_date]
            except Exception:
                pass
        return symbol, score_signal(ScoreInput(
            symbol=symbol, df=df, mode=mode, nifty_close=nf,
        ))
    except Exception:
        return symbol, None


def _compute_qty(price: float, stop: float | None,
                 capital: float, risk_pct: float, max_pos_pct: float) -> int:
    if price <= 0:
        return 0
    risk_amount = capital * (risk_pct / 100)
    risk_per_share = max(price - (stop or price * 0.95), price * 0.005)
    qty_by_risk = int(risk_amount / risk_per_share)
    qty_by_cap  = int((capital * max_pos_pct / 100) / price)
    return max(0, min(qty_by_risk, qty_by_cap))


# ── Core engine ───────────────────────────────────────────────────────────────

def run_backtest(inp: BacktestInput,
                 ohlcv_loader: Callable[[str, int], pd.DataFrame],
                 nifty_loader: Callable[[], pd.Series] | None = None,
                 progress_cb: Callable[[int, int, str], None] | None = None
                 ) -> tuple[BacktestReport, list[TradeRecord]]:
    """Run the walk-forward backtest. Returns (report, trade_list).

    Args:
        inp: BacktestInput config
        ohlcv_loader: callable(symbol, days) → DataFrame. Usually
            anju_core.data_layer.get_ohlcv. Each call should return the
            FULL history for that symbol — we slice by date internally
            to avoid lookahead bias.
        nifty_loader: callable() → Nifty Close series. Used for RS in scoring.
        progress_cb: optional callable(current_day, total_days, message)
            for live progress updates (Telegram pings during long runs).
    """
    # ── 1. Pre-load all symbol histories once ───────────────────
    if progress_cb:
        progress_cb(0, 0, f"Loading {len(inp.universe_symbols)} symbol histories...")

    histories: dict[str, pd.DataFrame] = {}
    load_stats = {"loaded": 0, "empty": 0, "errors": 0, "lengths": []}
    for sym in inp.universe_symbols:
        try:
            df = ohlcv_loader(sym, days=1500)
            if df is not None and not df.empty:
                df.index = pd.to_datetime(df.index)
                histories[sym] = df
                load_stats["loaded"] += 1
                load_stats["lengths"].append(len(df))
            else:
                load_stats["empty"] += 1
        except Exception:
            load_stats["errors"] += 1
            continue

    avg_len = (sum(load_stats["lengths"]) / max(len(load_stats["lengths"]), 1))
    print(f"[backtest] Histories loaded: {load_stats['loaded']}/{len(inp.universe_symbols)} "
          f"(empty={load_stats['empty']}, errors={load_stats['errors']}). "
          f"Avg rows/symbol: {avg_len:.0f}")
    if load_stats["lengths"]:
        print(f"[backtest] Sample dates from first symbol: "
              f"{list(histories.values())[0].index.min()} → "
              f"{list(histories.values())[0].index.max()}")

    if not histories:
        raise RuntimeError("No symbol histories loaded — check ohlcv_loader / data availability")

    # Use the first available symbol to derive trading days
    ref_sym = next(iter(histories.keys()))
    trading_days = _trading_days(ohlcv_loader, ref_sym, inp.start_date, inp.end_date)
    if progress_cb:
        progress_cb(0, len(trading_days), f"Loaded {len(histories)} symbols, {len(trading_days)} trading days")

    nifty_close = None
    if nifty_loader:
        try:
            nifty_close = nifty_loader()
            if nifty_close is not None:
                nifty_close.index = pd.to_datetime(nifty_close.index)
        except Exception:
            nifty_close = None

    # ── 2. Walk day-by-day ──────────────────────────────────────
    trades: list[TradeRecord] = []
    open_positions: list[dict] = []   # signals filled but not yet closed
    diag = {"days_scored": 0, "candidates_seen": 0, "candidates_filled": 0,
            "fill_lookup_misses": 0, "scoring_returned_none": 0,
            "scoring_below_threshold": 0}

    for day_idx, as_of in enumerate(trading_days):
        # Close any open positions whose stop/target was hit by today
        still_open = []
        for pos in open_positions:
            sym = pos["symbol"]
            # CRITICAL FIX: histories is keyed by SYMBOL.NS (with suffix),
            # but pos["symbol"] is the STRIPPED form (e.g. "RELIANCE").
            # DataFrame doesn't have a truthy value so check explicitly.
            df_full = histories.get(sym + ".NS")
            if df_full is None:
                df_full = histories.get(sym)
            if df_full is None:
                for key in histories:
                    if key.replace(".NS", "") == sym:
                        df_full = histories[key]; break
            if df_full is None:
                # Don't silently DROP the position — keep it open and try
                # again tomorrow. Dropping = losing the trade entirely.
                still_open.append(pos)
                continue
            df_post = df_full[df_full.index > pd.to_datetime(pos["fill_date"])]
            df_post = df_post[df_post.index <= as_of]
            if df_post.empty:
                still_open.append(pos)
                continue
            result = track_outcome(TrackInput(
                entry_price=pos["fill_price"], qty=pos["qty"],
                stop=pos["stop"], t1=pos["t1"], t2=pos["t2"],
                df_post_fill=df_post, max_hold_days=inp.max_hold_days,
            ))
            if not result.is_closed:
                still_open.append(pos)
                continue

            # Record the closed trade with costs
            if inp.apply_costs and result.exit_price is not None:
                np_ = net_pnl(buy_price=pos["fill_price"],
                              sell_price=result.exit_price,
                              qty=pos["qty"], segment=inp.base_segment)
                net_pct = float(np_["net_pct"])
                costs_pct = float(np_["costs_pct"])
            else:
                net_pct = result.gross_pnl_pct
                costs_pct = 0.0

            trades.append(TradeRecord(
                signal_date=pos["signal_date"],
                symbol=pos["symbol"], score=pos["score"],
                verdict=pos["verdict"], entry_model=pos["entry_model"],
                fill_date=pos["fill_date"], fill_price=pos["fill_price"],
                qty=pos["qty"], stop=pos["stop"], t1=pos["t1"], t2=pos["t2"],
                exit_date=result.exit_date, exit_price=result.exit_price,
                outcome_kind=result.outcome_kind, days_held=result.days_held,
                gross_pnl_pct=result.gross_pnl_pct,
                costs_pct=costs_pct,
                net_pnl_pct=net_pct,
                mfe_pct=result.max_favourable_excursion_pct,
                mae_pct=result.max_adverse_excursion_pct,
            ))
        open_positions = still_open

        # Generate new signals (only if we have room)
        slots = max(0, inp.max_open_positions - len(open_positions))
        if slots == 0:
            if progress_cb and day_idx % 25 == 0:
                progress_cb(day_idx, len(trading_days),
                            f"Day {day_idx+1}/{len(trading_days)} {as_of.date()} "
                            f"· trades={len(trades)} · open={len(open_positions)}")
            continue

        # Score every symbol as of this date in parallel
        score_results: list[ScoreResult] = []
        day_none = 0
        day_below = 0
        with ThreadPoolExecutor(max_workers=inp.max_workers) as ex:
            futures = [
                ex.submit(_score_one_at_date, sym, df, as_of, inp.mode, nifty_close)
                for sym, df in histories.items()
            ]
            for fut in as_completed(futures):
                try:
                    _, res = fut.result(timeout=60)
                except Exception:
                    continue
                if res is None:
                    day_none += 1
                elif res.score < inp.min_score:
                    day_below += 1
                elif inp.respect_verdict_gate and res.verdict != "BUY":
                    # Phase 1.6: backtest respects verdict gate. WATCH/AVOID
                    # signals are tracked but never deployed.
                    day_below += 1
                else:
                    score_results.append(res)

        diag["days_scored"] += 1
        diag["scoring_returned_none"] += day_none
        diag["scoring_below_threshold"] += day_below
        diag["candidates_seen"] += len(score_results)

        # Periodic diagnostic — surface what's filtering everything out
        if day_idx % 25 == 0 and day_idx > 0:
            print(f"[backtest] Day {day_idx}/{len(trading_days)} {as_of.date()}: "
                  f"scored={len(histories)} → None={day_none}, "
                  f"below_min={day_below}, candidates={len(score_results)}")

        # Take top N for available slots
        score_results.sort(key=lambda r: r.score, reverse=True)
        for r in score_results[:slots]:
            # Skip if already in open_positions (don't double-buy)
            if any(p["symbol"] == r.symbol for p in open_positions):
                continue

            # Fill at NEXT day's open (T+1)
            df_full = histories[r.symbol + ".NS"] if (r.symbol + ".NS") in histories else None
            if df_full is None:
                # Try with .NS suffix back
                for key in histories:
                    if key.replace(".NS", "") == r.symbol:
                        df_full = histories[key]
                        break
            if df_full is None:
                diag["fill_lookup_misses"] += 1
                diag.setdefault("fill_drop_reasons", {})["no_df_full"] = \
                    diag.get("fill_drop_reasons", {}).get("no_df_full", 0) + 1
                continue
            df_post = df_full[df_full.index > as_of]
            if df_post.empty:
                diag.setdefault("fill_drop_reasons", {})["empty_df_post"] = \
                    diag.get("fill_drop_reasons", {}).get("empty_df_post", 0) + 1
                continue
            fill_row = df_post.iloc[0]
            fill_date = str(df_post.index[0])[:10]
            # Slippage on BUY side
            base_open = float(fill_row["Open"])
            if base_open <= 0:
                diag.setdefault("fill_drop_reasons", {})["zero_open_price"] = \
                    diag.get("fill_drop_reasons", {}).get("zero_open_price", 0) + 1
                continue
            fill_price = round(base_open * (1 + inp.slippage_pct_buy / 100), 2)

            qty = _compute_qty(fill_price,
                               r.exit_logic.stop if r.exit_logic else None,
                               inp.capital_inr, inp.base_risk_pct,
                               inp.max_position_pct)
            if qty <= 0:
                diag.setdefault("fill_drop_reasons", {})["qty_zero"] = \
                    diag.get("fill_drop_reasons", {}).get("qty_zero", 0) + 1
                continue

            # Phase 1.7 Layer 2: reject fill if T1 ended up too close to fill
            # price after a gap-up. Layer 1 (signal-time +1.5% T1 buffer) is
            # the primary defense; this catches edge cases where gap-up was
            # larger than expected. Threshold: T1 must be >= +0.5% above fill.
            t1_val = r.exit_logic.partial_target if r.exit_logic else None
            if inp.respect_t1_distance and t1_val is not None and fill_price > 0:
                t1_dist_pct = (t1_val - fill_price) / fill_price
                if t1_dist_pct < 0.005:
                    diag.setdefault("fill_drop_reasons", {})["t1_too_close_at_fill"] = \
                        diag.get("fill_drop_reasons", {}).get("t1_too_close_at_fill", 0) + 1
                    continue

            diag["candidates_filled"] = diag.get("candidates_filled", 0) + 1
            open_positions.append({
                "signal_date": str(as_of)[:10],
                "symbol": r.symbol, "score": r.score, "verdict": r.verdict,
                "entry_model": r.entry_model,
                "fill_date": fill_date, "fill_price": fill_price, "qty": qty,
                "stop": r.exit_logic.stop if r.exit_logic else fill_price * 0.95,
                "t1": r.exit_logic.partial_target if r.exit_logic else None,
                "t2": r.exit_logic.full_target if r.exit_logic else None,
            })

        if progress_cb and day_idx % 25 == 0:
            progress_cb(day_idx, len(trading_days),
                        f"Day {day_idx+1}/{len(trading_days)} {as_of.date()} "
                        f"· trades={len(trades)} · open={len(open_positions)}")

    # Final diagnostic dump
    print(f"[backtest] DIAGNOSTIC SUMMARY")
    print(f"  Days scored: {diag['days_scored']}")
    print(f"  Scoring returned None: {diag['scoring_returned_none']:,} "
          f"({diag['scoring_returned_none'] / max(diag['days_scored'] * len(histories), 1) * 100:.1f}%)")
    print(f"  Fill drop reasons: {diag.get('fill_drop_reasons', {})}")
    print(f"  Candidates filled: {diag.get('candidates_filled', 0)}")
    print(f"  Scoring below min_score: {diag['scoring_below_threshold']:,} "
          f"({diag['scoring_below_threshold'] / max(diag['days_scored'] * len(histories), 1) * 100:.1f}%)")
    print(f"  Candidates above threshold: {diag['candidates_seen']:,}")
    print(f"  Fill lookup misses: {diag['fill_lookup_misses']}")
    print(f"  Total trades closed: {len(trades)}")
    print(f"  Positions still open at end: {len(open_positions)}")

    # ── 3. Aggregate report ─────────────────────────────────────
    report = _build_report(inp, trades, open_positions, len(trading_days),
                           len(histories))
    return report, trades


def _build_report(inp: BacktestInput, trades: list[TradeRecord],
                  open_positions: list[dict], days_scanned: int,
                  universe_size: int) -> BacktestReport:
    if not trades:
        return BacktestReport(
            name=inp.name, start_date=inp.start_date, end_date=inp.end_date,
            universe_size=universe_size, days_scanned=days_scanned,
            total_signals=0, total_fills=0, total_closed=0,
            total_open=len(open_positions),
            by_outcome_kind={}, win_rate_pct=0.0,
            avg_winner_pct=0.0, avg_loser_pct=0.0, rr_realized=0.0,
            gross_expectancy_pct=0.0, net_expectancy_pct=0.0,
            expectancy_inr_per_trade=0.0,
            final_equity_pct=0.0, max_drawdown_pct=0.0,
            by_score_bucket={}, by_entry_model={}, by_segment={}, by_month={},
            best_trades=[], worst_trades=[],
            config=inp.model_dump(),
        )

    wins   = [t for t in trades if t.outcome_kind.startswith("WIN")]
    losses = [t for t in trades if t.outcome_kind == "LOSS_STOP"]

    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_w = statistics.mean(t.net_pnl_pct for t in wins)   if wins   else 0.0
    avg_l = statistics.mean(t.net_pnl_pct for t in losses) if losses else 0.0
    rr    = avg_w / abs(avg_l) if avg_l != 0 else float("inf")

    net_exp   = statistics.mean(t.net_pnl_pct for t in trades)
    gross_exp = statistics.mean(t.gross_pnl_pct for t in trades)
    exp_inr   = net_exp / 100 * (inp.capital_inr * inp.max_position_pct / 100)

    # Equity curve + drawdown — simulate compounding one trade at a time
    # (real life is parallel; this is a useful approximation for the cumulative
    #  effect of the edge)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for t in trades:
        equity *= (1 + t.net_pnl_pct / 100)
        peak = max(peak, equity)
        dd = (equity - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd
    final_eq_pct = (equity - 1) * 100

    # By score bucket
    def bucket(score: float) -> str:
        lo = int(score // 5) * 5
        return f"{lo:02d}-{lo+4:02d}"
    by_score: dict[str, dict] = {}
    for t in trades:
        b = bucket(t.score)
        by_score.setdefault(b, {"trades": 0, "wins": 0, "net_pnls": []})
        by_score[b]["trades"] += 1
        if t.outcome_kind.startswith("WIN"):
            by_score[b]["wins"] += 1
        by_score[b]["net_pnls"].append(t.net_pnl_pct)
    for b, v in by_score.items():
        v["win_rate_pct"] = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0
        v["net_expectancy_pct"] = round(statistics.mean(v["net_pnls"]), 3) if v["net_pnls"] else 0
        del v["net_pnls"]

    # By entry model
    by_em: dict[str, dict] = {}
    for t in trades:
        em = t.entry_model or "—"
        by_em.setdefault(em, {"trades": 0, "wins": 0, "net_pnls": []})
        by_em[em]["trades"] += 1
        if t.outcome_kind.startswith("WIN"):
            by_em[em]["wins"] += 1
        by_em[em]["net_pnls"].append(t.net_pnl_pct)
    for em, v in by_em.items():
        v["win_rate_pct"] = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0
        v["net_expectancy_pct"] = round(statistics.mean(v["net_pnls"]), 3) if v["net_pnls"] else 0
        del v["net_pnls"]

    # By month
    by_month: dict[str, dict] = {}
    for t in trades:
        m = t.fill_date[:7]   # YYYY-MM
        by_month.setdefault(m, {"trades": 0, "wins": 0, "net_pnls": []})
        by_month[m]["trades"] += 1
        if t.outcome_kind.startswith("WIN"):
            by_month[m]["wins"] += 1
        by_month[m]["net_pnls"].append(t.net_pnl_pct)
    for m, v in by_month.items():
        v["win_rate_pct"] = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0
        v["net_expectancy_pct"] = round(statistics.mean(v["net_pnls"]), 3) if v["net_pnls"] else 0
        del v["net_pnls"]

    by_outcome = {}
    for t in trades:
        by_outcome[t.outcome_kind] = by_outcome.get(t.outcome_kind, 0) + 1

    sorted_by_pnl = sorted(trades, key=lambda t: t.net_pnl_pct)
    best_trades  = list(reversed(sorted_by_pnl[-5:]))
    worst_trades = sorted_by_pnl[:5]

    return BacktestReport(
        name=inp.name, start_date=inp.start_date, end_date=inp.end_date,
        universe_size=universe_size, days_scanned=days_scanned,
        total_signals=len(trades) + len(open_positions),
        total_fills=len(trades) + len(open_positions),
        total_closed=len(trades), total_open=len(open_positions),
        by_outcome_kind=by_outcome,
        win_rate_pct=round(win_rate, 1),
        avg_winner_pct=round(avg_w, 3),
        avg_loser_pct=round(avg_l, 3),
        rr_realized=round(rr, 2),
        gross_expectancy_pct=round(gross_exp, 3),
        net_expectancy_pct=round(net_exp, 3),
        expectancy_inr_per_trade=round(exp_inr, 0),
        final_equity_pct=round(final_eq_pct, 2),
        max_drawdown_pct=round(max_dd, 2),
        by_score_bucket=dict(sorted(by_score.items())),
        by_entry_model=by_em,
        by_segment={},   # Phase 2 wires symbol→segment classification
        by_month=dict(sorted(by_month.items())),
        best_trades=best_trades,
        worst_trades=worst_trades,
        config=inp.model_dump(),
    )
