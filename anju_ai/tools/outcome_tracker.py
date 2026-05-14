#!/usr/bin/env python3
"""
anju_ai.tools.outcome_tracker — event-driven WIN/LOSS detection.

Fixes the v2 audit Finding 3.1: anju-trader's signal_tracker.check_outcomes
only marks WIN/LOSS at day 9-21 post-signal, using EOD close. Result:
  - A stock that hit T1 on day 3 then gave back gains → mislabelled LOSS
  - A stock that hit stop intraday on day 4 then recovered → still OPEN
  - Wins on day 5 → invisible until day 10

This module checks first-touch: the first day where high >= T1 (WIN_T1),
or low <= stop (LOSS_STOP), wins. T2 checked separately. If neither hits
within max_hold_days, TIME_EXIT at last close.

Same-day stop AND target intraday: industry convention is "exit at the
unfavourable level first" because that's the conservative assumption for
backtests (otherwise we'd cherry-pick which level hit first based on
intraday data we don't have at EOD). For BUY positions: stop first.

Also computes MFE (max favourable excursion) and MAE (max adverse
excursion) — needed for Phase 1 backtest reports + Phase 3 LLM post-mortem.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


# ── Typed I/O ─────────────────────────────────────────────────────────────────

class TrackInput(BaseModel):
    """Inputs to track one open position to outcome."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    entry_price: float                     # actual fill price (post-slippage)
    qty: int
    side: Literal["BUY", "SELL"] = "BUY"
    stop: float
    t1: float | None = None
    t2: float | None = None
    df_post_fill: pd.DataFrame             # OHLCV rows strictly after fill date
    max_hold_days: int = 90                # force TIME_EXIT after this many days


class TrackResult(BaseModel):
    """Outcome of tracking — matches outcomes table schema."""
    outcome_kind: Literal[
        "WIN_T1", "WIN_T2", "LOSS_STOP", "TIME_EXIT", "OPEN"
    ]
    exit_date: str | None = None
    exit_price: float | None = None
    days_held: int = 0
    gross_pnl_paise: int = 0
    gross_pnl_pct: float = 0.0
    max_favourable_excursion_pct: float = 0.0
    max_adverse_excursion_pct: float = 0.0
    bars_examined: int = 0
    is_closed: bool = False                # False if still open (insufficient data)


# ── Core tracker ──────────────────────────────────────────────────────────────

def track_outcome(inp: TrackInput) -> TrackResult:
    """Walk forward through df_post_fill day by day. First touch wins.

    For BUY:
      - If today's low <= stop → LOSS_STOP at stop price
      - If both stop and t1 hit same day → LOSS_STOP (conservative)
      - If today's high >= t1 (and stop didn't hit) → WIN_T1 at t1 price
      - If t2 set and high >= t2 → WIN_T2 at t2 price
      - Gap-down below stop on open → exit at open (worse than stop)
      - Gap-up above target on open → exit at open (better than target)
      - max_hold_days reached → TIME_EXIT at last close
      - No bars left → OPEN (return without closing)
    """
    df = inp.df_post_fill
    if df is None or df.empty:
        return TrackResult(outcome_kind="OPEN", bars_examined=0, is_closed=False)

    # For now: BUY only. SELL implemented in Phase 2 (F&O shorts).
    if inp.side != "BUY":
        raise NotImplementedError("SELL outcome tracking lands in Phase 2")

    entry = float(inp.entry_price)
    stop  = float(inp.stop)
    t1    = float(inp.t1) if inp.t1 else None
    t2    = float(inp.t2) if inp.t2 else None

    mfe_price = entry   # tracks the highest high seen so far
    mae_price = entry   # tracks the lowest low seen so far

    n_bars = min(len(df), inp.max_hold_days)
    for i in range(n_bars):
        row = df.iloc[i]
        try:
            o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        except (KeyError, ValueError, TypeError):
            continue

        # Track extremes
        if h > mfe_price:
            mfe_price = h
        if l < mae_price:
            mae_price = l

        # Gap-down below stop: exit at open (worse than expected)
        if o <= stop:
            return _close(inp, "LOSS_STOP", df.index[i], o, i + 1,
                          mfe_price, mae_price)

        # Gap-up above target: exit at open (better than expected)
        if t1 and o >= t1:
            kind = "WIN_T2" if (t2 and o >= t2) else "WIN_T1"
            return _close(inp, kind, df.index[i], o, i + 1, mfe_price, mae_price)

        # Intraday touch — conservative tiebreak: if both stop AND t1 hit
        # in the same bar, LOSS_STOP wins (we don't know which came first).
        stop_hit = l <= stop
        t1_hit   = t1 is not None and h >= t1
        t2_hit   = t2 is not None and h >= t2

        if stop_hit:
            return _close(inp, "LOSS_STOP", df.index[i], stop, i + 1,
                          mfe_price, mae_price)
        if t2_hit:
            return _close(inp, "WIN_T2", df.index[i], t2, i + 1,
                          mfe_price, mae_price)
        if t1_hit:
            return _close(inp, "WIN_T1", df.index[i], t1, i + 1,
                          mfe_price, mae_price)

    # No stop/target hit within max_hold_days
    if n_bars > 0 and n_bars >= inp.max_hold_days:
        last = df.iloc[n_bars - 1]
        return _close(inp, "TIME_EXIT", df.index[n_bars - 1],
                      float(last["Close"]), n_bars, mfe_price, mae_price)

    # Insufficient data — still open
    return TrackResult(outcome_kind="OPEN", bars_examined=n_bars,
                       is_closed=False,
                       max_favourable_excursion_pct=round((mfe_price - entry) / entry * 100, 2),
                       max_adverse_excursion_pct=round((mae_price - entry) / entry * 100, 2))


def _close(inp: TrackInput, kind: str, exit_date, exit_price: float,
           days_held: int, mfe_price: float, mae_price: float) -> TrackResult:
    """Build a closed TrackResult."""
    entry = float(inp.entry_price)
    gross_pnl_pct = round((exit_price - entry) / entry * 100, 4)
    gross_pnl_paise = int(round((exit_price - entry) * inp.qty * 100))
    return TrackResult(
        outcome_kind=kind,
        exit_date=str(exit_date)[:10] if exit_date is not None else None,
        exit_price=round(exit_price, 2),
        days_held=days_held,
        gross_pnl_paise=gross_pnl_paise,
        gross_pnl_pct=gross_pnl_pct,
        max_favourable_excursion_pct=round((mfe_price - entry) / entry * 100, 2),
        max_adverse_excursion_pct=round((mae_price - entry) / entry * 100, 2),
        bars_examined=days_held,
        is_closed=True,
    )


# ── Memory-DB integration ─────────────────────────────────────────────────────

def close_open_outcomes(con, ohlcv_loader, max_hold_days: int = 90,
                        today: str | None = None,
                        apply_costs: bool = True) -> dict:
    """Close all open fills (no outcome row yet) by tracking forward through
    historical OHLCV. Used by the EOD close loop and the backtest replay.

    Args:
        con: open memory.db connection (with apply_migrations already run)
        ohlcv_loader: callable(symbol, days) -> DataFrame — usually
            anju_core.get_ohlcv. Tests inject a mock.
        max_hold_days: force TIME_EXIT after this many trading days
        today: 'YYYY-MM-DD' — only consider data up to this date (for backtest
            replay). None = no upper bound (use everything available).
        apply_costs: when True (default), subtract round-trip costs from
            gross P&L using anju_ai.tools.costs. When False, gross = net
            (useful for sanity checks).

    Returns dict with counts: scanned, closed, still_open.
    """
    from anju_ai.tools.costs import net_pnl
    from anju_ai.tools.paper_fill import classify_segment

    open_fills = con.execute("""
        SELECT f.id        AS fill_id,
               f.signal_id AS signal_id,
               f.fill_date AS fill_date,
               f.fill_price AS fill_price,
               f.fill_qty  AS fill_qty,
               s.symbol    AS symbol,
               s.suggested_stop AS suggested_stop,
               s.suggested_t1   AS suggested_t1,
               s.suggested_t2   AS suggested_t2
        FROM fills f
        JOIN signals_current s ON f.signal_id = s.id
        WHERE NOT EXISTS (SELECT 1 FROM outcomes o WHERE o.fill_id = f.id)
    """).fetchall()

    scanned = len(open_fills)
    closed = 0
    still_open = 0

    for f in open_fills:
        try:
            df = ohlcv_loader(f["symbol"], days=180)
        except Exception:
            still_open += 1
            continue
        if df is None or df.empty:
            still_open += 1
            continue

        # Trim to dates strictly after fill_date (and ≤ today if given)
        df.index = pd.to_datetime(df.index)
        df = df[df.index > pd.to_datetime(f["fill_date"])]
        if today:
            df = df[df.index <= pd.to_datetime(today)]
        if df.empty:
            still_open += 1
            continue

        result = track_outcome(TrackInput(
            entry_price=f["fill_price"], qty=f["fill_qty"],
            stop=f["suggested_stop"],
            t1=f["suggested_t1"], t2=f["suggested_t2"],
            df_post_fill=df, max_hold_days=max_hold_days,
        ))

        if not result.is_closed:
            still_open += 1
            continue

        # Apply round-trip costs (brokerage + STT + slippage etc).
        # Segment defaults to midcap — Phase 2 wires symbol-aware classification.
        if apply_costs and result.exit_price is not None:
            seg = classify_segment(f["fill_price"], avg_volume_10d=None)
            np_ = net_pnl(
                buy_price=f["fill_price"], sell_price=result.exit_price,
                qty=f["fill_qty"], segment=seg,
            )
            costs_paise = int(round(np_["costs_inr"] * 100))
            net_pnl_paise = int(round(np_["net_inr"] * 100))
            net_pnl_pct_v = float(np_["net_pct"])
        else:
            costs_paise = 0
            net_pnl_paise = result.gross_pnl_paise
            net_pnl_pct_v = result.gross_pnl_pct

        con.execute("""
            INSERT INTO outcomes
                (fill_id, outcome_date, outcome_kind, exit_price, days_held,
                 gross_pnl_paise, costs_total_paise, net_pnl_paise,
                 net_pnl_pct, max_favourable_excursion, max_adverse_excursion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f["fill_id"], result.exit_date, result.outcome_kind,
            result.exit_price, result.days_held,
            result.gross_pnl_paise,
            costs_paise,
            net_pnl_paise,
            net_pnl_pct_v,
            result.max_favourable_excursion_pct,
            result.max_adverse_excursion_pct,
        ))
        closed += 1

    return {"scanned": scanned, "closed": closed, "still_open": still_open}
