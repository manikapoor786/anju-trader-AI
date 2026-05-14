"""Tests for anju_ai.tools.flows — mock NSE responses, no network."""

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from anju_ai.tools.flows import (
    FIIDIIRow,
    FIIDIISnapshot,
    _parse_nse_response,
    fetch_fii_dii,
    latest_flows,
    save_flows_snapshot,
)


@dataclass
class FakeResponse:
    """Minimal Response-like object for tests."""
    status_code: int
    _json: object

    def json(self):
        return self._json

    @property
    def ok(self):
        return 200 <= self.status_code < 300


# ── _parse_nse_response ───────────────────────────────────────────────────────

VALID_NSE_PAYLOAD = [
    {"category": "FII/FPI **", "date": "14-May-2026",
     "buyValue": "12500.50", "sellValue": "11200.00", "netValue": "1300.50"},
    {"category": "DII **", "date": "14-May-2026",
     "buyValue": "9800.00", "sellValue": "10500.25", "netValue": "-700.25"},
]


def test_parse_valid_payload():
    out = _parse_nse_response(VALID_NSE_PAYLOAD)
    assert out is not None
    fii, dii = out
    assert fii.category == "FII/FPI"
    assert fii.buy_value_cr == 12500.50
    assert fii.sell_value_cr == 11200.00
    assert fii.net_value_cr == pytest.approx(1300.50, abs=0.01)
    assert dii.category == "DII"
    assert dii.net_value_cr == pytest.approx(-700.25, abs=0.01)


def test_parse_handles_numbers_with_commas():
    payload = [
        {"category": "FII/FPI", "date": "14-May-2026",
         "buyValue": "1,25,00,000.50", "sellValue": "1,000.00", "netValue": "12499000.50"},
        {"category": "DII", "date": "14-May-2026",
         "buyValue": "100", "sellValue": "50", "netValue": "50"},
    ]
    out = _parse_nse_response(payload)
    assert out is not None
    fii, dii = out
    assert fii.buy_value_cr == 12500000.50
    assert dii.net_value_cr == 50.0


def test_parse_recomputes_net_if_corrupt():
    # netValue says 0 but buy-sell suggests 100 → trust derived
    payload = [
        {"category": "FII/FPI", "date": "14-May-2026",
         "buyValue": "1000", "sellValue": "900", "netValue": "0"},
        {"category": "DII", "date": "14-May-2026",
         "buyValue": "500", "sellValue": "400", "netValue": "0"},
    ]
    out = _parse_nse_response(payload)
    assert out is not None
    fii, dii = out
    assert fii.net_value_cr == 100   # derived buy-sell
    assert dii.net_value_cr == 100


def test_parse_returns_none_when_only_one_category():
    payload = [VALID_NSE_PAYLOAD[0]]   # FII only, no DII
    assert _parse_nse_response(payload) is None


def test_parse_returns_none_on_wrong_shape():
    assert _parse_nse_response({"unrelated": "data"}) is None
    assert _parse_nse_response([]) is None
    assert _parse_nse_response("not a list or dict") is None


def test_parse_handles_data_wrapper():
    """NSE has occasionally wrapped responses in {"data": [...]} too."""
    payload = {"data": VALID_NSE_PAYLOAD}
    out = _parse_nse_response(payload)
    assert out is not None
    fii, _ = out
    assert fii.buy_value_cr == 12500.50


def test_parse_normalises_date_to_iso():
    out = _parse_nse_response(VALID_NSE_PAYLOAD)
    assert out is not None
    fii, _ = out
    assert fii.date == "2026-05-14"


# ── fetch_fii_dii ─────────────────────────────────────────────────────────────

def test_fetch_fii_dii_returns_snapshot():
    def mock_get(url, **kwargs):
        return FakeResponse(200, VALID_NSE_PAYLOAD)

    snap = fetch_fii_dii(http_get=mock_get)
    assert snap is not None
    assert snap.snapshot_date == "2026-05-14"
    assert snap.fii_net_cr == pytest.approx(1300.50)
    assert snap.dii_net_cr == pytest.approx(-700.25)


def test_fetch_fii_dii_returns_none_on_http_error():
    def mock_get(url, **kwargs):
        return FakeResponse(503, [])

    assert fetch_fii_dii(http_get=mock_get) is None


def test_fetch_fii_dii_returns_none_on_parse_failure():
    def mock_get(url, **kwargs):
        return FakeResponse(200, {"unexpected": "shape"})

    assert fetch_fii_dii(http_get=mock_get) is None


def test_fetch_fii_dii_returns_none_on_exception():
    def mock_get(url, **kwargs):
        raise ConnectionError("network down")

    assert fetch_fii_dii(http_get=mock_get) is None


# ── signal_strength ──────────────────────────────────────────────────────────

def test_signal_strength_fii_buying_dii_selling():
    snap = FIIDIISnapshot(
        snapshot_date="2026-05-14",
        fii_buy_cr=12500, fii_sell_cr=11200, fii_net_cr=1300,
        dii_buy_cr=9800, dii_sell_cr=10500, dii_net_cr=-700,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
    s = snap.signal_strength()
    assert "FII Buying" in s
    assert "DII Selling" in s


# ── save_flows_snapshot + latest_flows (DB integration) ──────────────────────

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    yield con
    con.close()


def test_save_flows_snapshot_inserts(isolated_db):
    snap = FIIDIISnapshot(
        snapshot_date="2026-05-14",
        fii_buy_cr=12500, fii_sell_cr=11200, fii_net_cr=1300,
        dii_buy_cr=9800, dii_sell_cr=10500, dii_net_cr=-700,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
    rid = save_flows_snapshot(isolated_db, snap)
    assert rid > 0

    row = isolated_db.execute(
        "SELECT fii_cash_cr, dii_cash_cr FROM flows_snapshots WHERE id=?", (rid,)
    ).fetchone()
    assert row["fii_cash_cr"] == 1300
    assert row["dii_cash_cr"] == -700


def test_save_flows_snapshot_is_upsert(isolated_db):
    """Re-fetching the same day should UPDATE not duplicate."""
    snap1 = FIIDIISnapshot(
        snapshot_date="2026-05-14",
        fii_buy_cr=12500, fii_sell_cr=11200, fii_net_cr=1300,
        dii_buy_cr=9800, dii_sell_cr=10500, dii_net_cr=-700,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
    snap2 = snap1.model_copy(update={"fii_net_cr": 1500, "dii_net_cr": -900})

    save_flows_snapshot(isolated_db, snap1)
    save_flows_snapshot(isolated_db, snap2)

    rows = isolated_db.execute(
        "SELECT fii_cash_cr, dii_cash_cr FROM flows_snapshots WHERE snapshot_date=?",
        ("2026-05-14",),
    ).fetchall()
    assert len(rows) == 1   # not duplicated
    assert rows[0]["fii_cash_cr"] == 1500
    assert rows[0]["dii_cash_cr"] == -900


def test_latest_flows_returns_recent_in_desc_order(isolated_db):
    dates = ["2026-05-10", "2026-05-12", "2026-05-14", "2026-05-13"]
    for i, d in enumerate(dates):
        snap = FIIDIISnapshot(
            snapshot_date=d,
            fii_buy_cr=1000, fii_sell_cr=900, fii_net_cr=100 * i,
            dii_buy_cr=800, dii_sell_cr=700, dii_net_cr=-100 * i,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        save_flows_snapshot(isolated_db, snap)

    flows = latest_flows(isolated_db, days_back=3)
    assert len(flows) == 3
    # Must be in descending date order
    assert flows[0]["date"] == "2026-05-14"
    assert flows[1]["date"] == "2026-05-13"
    assert flows[2]["date"] == "2026-05-12"
