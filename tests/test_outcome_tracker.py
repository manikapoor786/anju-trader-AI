"""Tests for anju_ai.tools.outcome_tracker — synthetic OHLCV, no network."""

import pandas as pd
import pytest

from anju_ai.tools.outcome_tracker import (
    TrackInput,
    TrackResult,
    track_outcome,
    close_open_outcomes,
)


def make_df(bars: list[tuple[float, float, float, float]],
            start: str = "2026-05-15") -> pd.DataFrame:
    """bars = list of (open, high, low, close)."""
    dates = pd.bdate_range(start=start, periods=len(bars))
    return pd.DataFrame({
        "Open":   [b[0] for b in bars],
        "High":   [b[1] for b in bars],
        "Low":    [b[2] for b in bars],
        "Close":  [b[3] for b in bars],
        "Volume": [100_000] * len(bars),
    }, index=dates)


# ── First-touch detection ─────────────────────────────────────────────────────

def test_win_t1_on_first_touch():
    # Phase 1.5 two-stage exit: first half captured at T1=110, second
    # half rides with breakeven stop. Only 1 bar available so second
    # half closes at last_close=108. Blended exit = (110+108)/2 = 109.
    df = make_df([(101, 112, 99, 108)])
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, df_post_fill=df,
    ))
    assert out.outcome_kind == "WIN_T1"
    assert out.exit_price == 109   # blended: (110 + 108) / 2
    assert out.days_held == 1
    assert out.is_closed


def test_loss_stop_on_first_touch():
    # Day 1: low = 93 → LOSS_STOP at 95
    df = make_df([(99, 100, 93, 97)])
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, df_post_fill=df,
    ))
    assert out.outcome_kind == "LOSS_STOP"
    assert out.exit_price == 95
    assert out.days_held == 1


def test_t2_wins_over_t1_when_both_hit_same_bar():
    # Both T1 (110) and T2 (120) hit same bar. First half at T1, second
    # half at T2 → blended (110 + 120) / 2 = 115.
    df = make_df([(101, 122, 99, 120)])
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, t2=120,
        df_post_fill=df,
    ))
    assert out.outcome_kind == "WIN_T2"
    assert out.exit_price == 115   # blended


def test_conservative_tiebreak_stop_wins_when_both_hit():
    # Same bar hits both stop AND t1 → LOSS_STOP (we don't know intraday order)
    df = make_df([(100, 112, 93, 105)])
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, df_post_fill=df,
    ))
    assert out.outcome_kind == "LOSS_STOP"
    assert out.exit_price == 95


def test_walks_multiple_days_until_touch():
    # T1 hit on day 3 (high 113 >= 110). First half captured at T1=110.
    # No more bars → second half closes at last_close=110. Blended=110.
    bars = [
        (101, 105, 99, 103),   # day 1 — no touch
        (103, 108, 101, 106),  # day 2 — no touch
        (107, 113, 105, 110),  # day 3 — t1 hit at 110
    ]
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, df_post_fill=df,
    ))
    assert out.outcome_kind == "WIN_T1"
    assert out.days_held == 3
    assert out.exit_price == 110   # blended (110+110)/2 = 110


# ── Gap handling ──────────────────────────────────────────────────────────────

def test_gap_down_below_stop_exits_at_open():
    # Bad news overnight → opens at 90, well below stop 95
    df = make_df([(90, 92, 89, 91)])
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, df_post_fill=df,
    ))
    assert out.outcome_kind == "LOSS_STOP"
    assert out.exit_price == 90   # at the open, not at stop


def test_gap_up_above_target_exits_at_open():
    # Gap-up above T1 → first half at OPEN=115 (favourable to T1=110).
    # Second half rides, no more bars → last_close=116. Blended=(115+116)/2=115.5
    df = make_df([(115, 117, 113, 116)])
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, df_post_fill=df,
    ))
    assert out.outcome_kind == "WIN_T1"
    assert out.exit_price == 115.5   # blended


def test_gap_up_above_t2_classifies_as_t2():
    df = make_df([(125, 127, 123, 126)])
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, t2=120,
        df_post_fill=df,
    ))
    assert out.outcome_kind == "WIN_T2"


# ── Time exit ─────────────────────────────────────────────────────────────────

def test_time_exit_at_max_hold_days():
    # Sideways move — never hits stop or target
    bars = [(101, 105, 99, 103)] * 30
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=90, t1=120,
        df_post_fill=df, max_hold_days=10,
    ))
    assert out.outcome_kind == "TIME_EXIT"
    assert out.days_held == 10
    assert out.exit_price == 103   # last close at day 10


# ── Open state ────────────────────────────────────────────────────────────────

def test_open_when_no_post_fill_data():
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110,
        df_post_fill=pd.DataFrame(),
    ))
    assert out.outcome_kind == "OPEN"
    assert not out.is_closed


def test_open_when_insufficient_bars_and_no_touch():
    # 5 bars, none touch, max_hold_days=20 → still OPEN
    bars = [(101, 105, 99, 103)] * 5
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=90, t1=120,
        df_post_fill=df, max_hold_days=20,
    ))
    assert out.outcome_kind == "OPEN"
    assert not out.is_closed
    assert out.bars_examined == 5


# ── MFE / MAE ─────────────────────────────────────────────────────────────────

def test_mfe_and_mae_tracked():
    # Entry 100. Goes up to 108 then down to 91 then bounces. No stop, no target.
    bars = [
        (101, 108, 100, 105),  # MFE: 108
        (105, 107,  91, 95),    # MAE: 91
        (96, 100, 94, 98),
    ]
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=80, t1=130,
        df_post_fill=df, max_hold_days=3,
    ))
    assert out.outcome_kind == "TIME_EXIT"
    assert out.max_favourable_excursion_pct == 8.0   # (108-100)/100 * 100
    assert out.max_adverse_excursion_pct == -9.0     # (91-100)/100 * 100


# ── P&L computation ───────────────────────────────────────────────────────────

def test_gross_pnl_paise_correct():
    # Two-stage: T1 hit at 110, no more bars → second half at last_close=108.
    # Blended exit = (110+108)/2 = 109. Gain = (109-100)*50 = ₹450 = 45000 paise.
    df = make_df([(101, 112, 99, 108)])
    out = track_outcome(TrackInput(
        entry_price=100, qty=50, stop=95, t1=110, df_post_fill=df,
    ))
    assert out.gross_pnl_paise == 45000
    assert out.gross_pnl_pct == 9.0


# ── Phase 1.5 two-stage exit ────────────────────────────────────────────────

def test_two_stage_t1_then_t2_blended_exit():
    """T1 hit day 1, second half runs to T2 day 3. Both halves contribute."""
    bars = [
        (101, 112, 99, 111),    # day 1: T1=110 hit, first half at 110
        (111, 115, 109, 114),   # day 2: second half rides, T2=120 not yet
        (114, 122, 113, 121),   # day 3: T2 hit, blended (110+120)/2 = 115
    ]
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, t2=120, df_post_fill=df,
    ))
    assert out.outcome_kind == "WIN_T2"
    assert out.exit_price == 115   # blended


def test_two_stage_t1_then_breakeven_stop():
    """T1 hit day 1, second half reverses, hits breakeven stop (= entry).
    Blended exit = (T1 + entry) / 2."""
    bars = [
        (101, 112, 99, 111),    # day 1: T1=110 hit
        (110, 111, 105, 106),   # day 2: drops
        (105, 106, 99, 100),    # day 3: low=99 < entry=100 → BE stop hit
    ]
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, t2=120, df_post_fill=df,
    ))
    assert out.outcome_kind == "WIN_T1"
    assert out.exit_price == 105   # blended (110 + 100) / 2 = 105


def test_two_stage_t1_then_time_exit_blended():
    """T1 hit, second half rides to time_exit at higher close."""
    bars = [(101, 112, 99, 111)] + [(112, 114, 110, 113)] * 9
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, t2=140,
        df_post_fill=df, max_hold_days=10,
    ))
    # T1 hit day 1, second half rides 10 days, last close=113.
    # Blended (110+113)/2 = 111.5
    assert out.outcome_kind == "WIN_T1"
    assert out.exit_price == 111.5


def test_two_stage_breakeven_stop_does_not_trigger_below_original_stop():
    """After T1 hit, the stop is at ENTRY (breakeven). A bar that touches
    the ORIGINAL stop (e.g. 95) but not breakeven (entry=100) should not
    exit — wait, breakeven IS higher than original stop, so the stop is
    actually TIGHTER. Verify breakeven is the binding constraint."""
    bars = [
        (101, 112, 99, 111),    # day 1: T1=110 hit
        (110, 112, 98, 105),    # day 2: low=98 < entry=100 → BE stop hit
    ]
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=92, t1=110, df_post_fill=df,
    ))
    # Breakeven stop (=100) is HIGHER than original stop (=92).
    # Low=98 triggers breakeven first.
    assert out.outcome_kind == "WIN_T1"
    # Blended (110 + 100) / 2 = 105
    assert out.exit_price == 105


def test_first_half_stop_still_triggers_loss_stop():
    """If price hits the ORIGINAL stop before T1 is captured, this is
    still a LOSS_STOP (no T1 magic happens)."""
    bars = [(99, 100, 93, 97)]
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=110, df_post_fill=df,
    ))
    assert out.outcome_kind == "LOSS_STOP"
    assert out.exit_price == 95


# ── Corporate-action filter ──────────────────────────────────────────────────

def test_corporate_action_recorded_as_zero_pnl_not_loss_stop():
    """REGRESSION: VEDL gapped from ₹773 close to ₹289 open on a split day
    in 2026. Outcome tracker was recording this as LOSS_STOP at ₹289 —
    a fake -65% loss. With the 25% adverse-gap filter, the trade closes
    as CORPORATE_ACTION with 0% P&L (real shareholder is unaffected)."""
    bars = [
        (101, 105, 99, 103),   # normal day 1, close 103
        (29, 32, 28, 30),       # day 2: ~71% gap-down vs prev close 103 (split)
    ]
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=120,
        df_post_fill=df, max_hold_days=10,
    ))
    assert out.outcome_kind == "CORPORATE_ACTION"
    assert out.gross_pnl_pct == 0.0
    assert out.gross_pnl_paise == 0
    assert out.is_closed


def test_real_gap_down_below_threshold_still_triggers_stop():
    """Make sure normal gap-down losses still get caught. A 10% gap is
    a real loss event, not a corporate action."""
    bars = [(85, 86, 84, 85)]   # opens at 85, well below stop 95
    df = make_df(bars)
    out = track_outcome(TrackInput(
        entry_price=100, qty=10, stop=95, t1=120,
        df_post_fill=df, max_hold_days=10,
    ))
    assert out.outcome_kind == "LOSS_STOP"
    assert out.exit_price == 85   # at the open


# ── close_open_outcomes loop ──────────────────────────────────────────────────

@pytest.fixture
def db_with_open_fill(tmp_path, monkeypatch):
    """Set up a memory.db with one open fill ready to be closed."""
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()

    con.execute("""INSERT INTO regime_snapshots
        (snapshot_date, state, min_score, nifty_close, payload_json)
        VALUES ('2026-05-14', 'Trending', 6, 22000.0, '{}')""")
    rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    con.execute("""INSERT INTO signals
        (signal_date, symbol, horizon, regime_id, rule_score, final_score,
         verdict, entry_price, suggested_stop, suggested_t1, suggested_t2,
         suggested_qty, breakdown_json)
        VALUES ('2026-05-14', 'TEST', 'SWING', ?, 12.0, 12.0, 'BUY',
                100.0, 95.0, 110.0, 120.0, 10, '{}')""", (rid,))
    sid = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    con.execute("""INSERT INTO fills
        (signal_id, fill_date, fill_price, fill_qty, gross_value, is_paper)
        VALUES (?, '2026-05-15', 100.0, 10, 1000.0, 1)""", (sid,))

    yield con
    con.close()


def test_close_open_outcomes_marks_winner(db_with_open_fill):
    con = db_with_open_fill

    # Phase 1.5: two-stage exit. T1=110 hit, second half closes at
    # last_close=112 → blended exit = (110+112)/2 = 111.
    # Gross gain = (111-100)*10 = ₹110 = 11000 paise.
    def mock_loader(symbol, days):
        return make_df([(101, 115, 99, 112)], start="2026-05-16")

    res = close_open_outcomes(con, mock_loader)
    assert res["scanned"] == 1
    assert res["closed"] == 1
    assert res["still_open"] == 0

    row = con.execute("SELECT outcome_kind, exit_price, "
                      "gross_pnl_paise, costs_total_paise, net_pnl_pct "
                      "FROM outcomes").fetchone()
    assert row["outcome_kind"] == "WIN_T1"
    assert row["exit_price"] == 111   # blended
    assert row["gross_pnl_paise"] == 11000   # (111-100)*10 in paise
    assert row["costs_total_paise"] > 0
    assert row["net_pnl_pct"] < 11.0   # costs reduce gross
    assert row["net_pnl_pct"] > 0       # still positive


def test_close_open_outcomes_apply_costs_false_keeps_gross(db_with_open_fill):
    con = db_with_open_fill

    def mock_loader(symbol, days):
        return make_df([(101, 115, 99, 112)], start="2026-05-16")

    res = close_open_outcomes(con, mock_loader, apply_costs=False)
    assert res["closed"] == 1
    row = con.execute("SELECT net_pnl_pct, costs_total_paise FROM outcomes").fetchone()
    assert row["net_pnl_pct"] == 11.0   # blended (111-100)/100*100 = 11%
    assert row["costs_total_paise"] == 0


def test_close_open_outcomes_skips_when_no_data(db_with_open_fill):
    con = db_with_open_fill

    def mock_loader(symbol, days):
        return pd.DataFrame()

    res = close_open_outcomes(con, mock_loader)
    assert res["scanned"] == 1
    assert res["closed"] == 0
    assert res["still_open"] == 1


def test_close_open_outcomes_is_idempotent(db_with_open_fill):
    con = db_with_open_fill

    def mock_loader(symbol, days):
        return make_df([(101, 115, 99, 112)], start="2026-05-16")

    res1 = close_open_outcomes(con, mock_loader)
    res2 = close_open_outcomes(con, mock_loader)
    assert res1["closed"] == 1
    assert res2["scanned"] == 0  # nothing left open after first pass
