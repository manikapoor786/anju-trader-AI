"""Tests for anju_ai.tools.deals — mock NSE responses, no network."""

from dataclasses import dataclass
import json
import pytest

from anju_ai.tools.deals import (
    Deal,
    _merge_deals,
    _parse_deals,
    _to_nse_date,
    fetch_deals,
    get_deals_for_symbol,
    save_deals,
)


@dataclass
class FakeResponse:
    status_code: int
    _json: object

    def json(self):
        return self._json


# ── Sample NSE payloads ──────────────────────────────────────────────────────

BULK_PAYLOAD = {
    "data": [
        {"BD_DT_DATE": "14-May-2026", "BD_SYMBOL": "RELIANCE",
         "BD_CLIENT_NAME": "GS Investment Partners", "BD_BUY_SELL": "BUY",
         "BD_QTY_TRD": "500000", "BD_TP_WATP": "1425.50"},
        {"BD_DT_DATE": "14-May-2026", "BD_SYMBOL": "TCS",
         "BD_CLIENT_NAME": "Morgan Stanley", "BD_BUY_SELL": "SELL",
         "BD_QTY_TRD": "1,25,000", "BD_TP_WATP": "3525.00"},
    ]
}

BLOCK_PAYLOAD = [
    {"BD_DT_DATE": "14-May-2026", "BD_SYMBOL": "HDFCBANK",
     "BD_CLIENT_NAME": "Goldman Sachs", "BD_BUY_SELL": "B",
     "BD_QTY_TRD": "750000", "BD_TP_WATP": "1620.25"},
]


# ── _parse_deals ─────────────────────────────────────────────────────────────

def test_parse_bulk_deals_with_data_wrapper():
    deals = _parse_deals(BULK_PAYLOAD, "bulk")
    assert len(deals) == 2
    assert deals[0].symbol == "RELIANCE"
    assert deals[0].side == "BUY"
    assert deals[0].quantity == 500000
    assert deals[0].avg_price == 1425.50
    assert deals[0].value_inr == 500000 * 1425.50


def test_parse_block_deals_as_plain_list():
    deals = _parse_deals(BLOCK_PAYLOAD, "block")
    assert len(deals) == 1
    assert deals[0].symbol == "HDFCBANK"
    assert deals[0].side == "BUY"     # "B" normalised


def test_parse_handles_commas_in_quantity():
    deals = _parse_deals(BULK_PAYLOAD, "bulk")
    tcs = [d for d in deals if d.symbol == "TCS"][0]
    assert tcs.quantity == 125000


def test_parse_normalises_date_to_iso():
    deals = _parse_deals(BULK_PAYLOAD, "bulk")
    for d in deals:
        assert d.deal_date == "2026-05-14"


def test_parse_filters_zero_quantity_or_price():
    payload = {"data": [
        {"BD_DT_DATE": "14-May-2026", "BD_SYMBOL": "ZERO_QTY",
         "BD_CLIENT_NAME": "X", "BD_BUY_SELL": "BUY",
         "BD_QTY_TRD": "0", "BD_TP_WATP": "100"},
        {"BD_DT_DATE": "14-May-2026", "BD_SYMBOL": "ZERO_PX",
         "BD_CLIENT_NAME": "Y", "BD_BUY_SELL": "BUY",
         "BD_QTY_TRD": "1000", "BD_TP_WATP": "0"},
        {"BD_DT_DATE": "14-May-2026", "BD_SYMBOL": "OK",
         "BD_CLIENT_NAME": "Z", "BD_BUY_SELL": "BUY",
         "BD_QTY_TRD": "100", "BD_TP_WATP": "100"},
    ]}
    deals = _parse_deals(payload, "bulk")
    assert len(deals) == 1
    assert deals[0].symbol == "OK"


def test_parse_returns_empty_on_wrong_shape():
    assert _parse_deals("not a dict or list", "bulk") == []
    assert _parse_deals({}, "bulk") == []
    assert _parse_deals(None, "bulk") == []


def test_parse_normalises_buy_sell_variants():
    payload = {"data": [
        {"BD_DT_DATE": "14-May-2026", "BD_SYMBOL": "A", "BD_CLIENT_NAME": "X",
         "BD_BUY_SELL": "Buy", "BD_QTY_TRD": "100", "BD_TP_WATP": "100"},
        {"BD_DT_DATE": "14-May-2026", "BD_SYMBOL": "B", "BD_CLIENT_NAME": "X",
         "BD_BUY_SELL": "sell", "BD_QTY_TRD": "100", "BD_TP_WATP": "100"},
        {"BD_DT_DATE": "14-May-2026", "BD_SYMBOL": "C", "BD_CLIENT_NAME": "X",
         "BD_BUY_SELL": "S", "BD_QTY_TRD": "100", "BD_TP_WATP": "100"},
    ]}
    deals = _parse_deals(payload, "bulk")
    sides = {d.symbol: d.side for d in deals}
    assert sides["A"] == "BUY"
    assert sides["B"] == "SELL"
    assert sides["C"] == "SELL"


# ── _to_nse_date ─────────────────────────────────────────────────────────────

def test_to_nse_date_converts_iso_to_dd_mm_yyyy():
    assert _to_nse_date("2026-05-14") == "14-05-2026"


def test_to_nse_date_passes_through_invalid_input():
    assert _to_nse_date("nonsense") == "nonsense"


# ── fetch_deals ──────────────────────────────────────────────────────────────

def test_fetch_deals_returns_list_on_success():
    def mock_get(url, **kwargs):
        assert "bulk" in url
        return FakeResponse(200, BULK_PAYLOAD)
    deals = fetch_deals("bulk", "2026-05-14", "2026-05-14", http_get=mock_get)
    assert len(deals) == 2


def test_fetch_deals_returns_empty_on_http_error():
    def mock_get(url, **kwargs):
        return FakeResponse(503, [])
    deals = fetch_deals("bulk", http_get=mock_get)
    assert deals == []


def test_fetch_deals_returns_empty_on_exception():
    def mock_get(url, **kwargs):
        raise ConnectionError("offline")
    deals = fetch_deals("block", http_get=mock_get)
    assert deals == []


# ── _merge_deals ─────────────────────────────────────────────────────────────

def test_merge_deals_dedupes_on_key():
    existing = [{"symbol": "A", "side": "BUY", "quantity": 100, "avg_price": 50.0}]
    new = [
        {"symbol": "A", "side": "BUY", "quantity": 100, "avg_price": 50.0},  # dup
        {"symbol": "A", "side": "BUY", "quantity": 200, "avg_price": 50.0},  # new
    ]
    merged = _merge_deals(existing, new)
    assert len(merged) == 2


def test_merge_deals_handles_empty():
    assert _merge_deals([], []) == []
    assert _merge_deals([], [{"symbol": "A", "side": "BUY",
                              "quantity": 1, "avg_price": 1.0}])[0]["symbol"] == "A"


# ── save_deals + get_deals_for_symbol (DB integration) ───────────────────────

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    yield con
    con.close()


def _sample_deals():
    return [
        Deal(deal_type="bulk", deal_date="2026-05-14", symbol="RELIANCE",
             client_name="GS", side="BUY", quantity=500000,
             avg_price=1425.50, value_inr=712750000),
        Deal(deal_type="block", deal_date="2026-05-14", symbol="HDFCBANK",
             client_name="MS", side="BUY", quantity=750000,
             avg_price=1620.25, value_inr=1215187500),
    ]


def test_save_deals_creates_row_with_json_arrays(isolated_db):
    rid = save_deals(isolated_db, _sample_deals(), "2026-05-14")
    assert rid > 0
    row = isolated_db.execute(
        "SELECT bulk_deals_json, block_deals_json FROM flows_snapshots WHERE id=?",
        (rid,),
    ).fetchone()
    bulk_parsed = json.loads(row["bulk_deals_json"])
    block_parsed = json.loads(row["block_deals_json"])
    assert len(bulk_parsed) == 1
    assert len(block_parsed) == 1
    assert bulk_parsed[0]["symbol"] == "RELIANCE"
    assert block_parsed[0]["symbol"] == "HDFCBANK"


def test_save_deals_merges_on_resave(isolated_db):
    deals1 = _sample_deals()
    deals2 = [
        Deal(deal_type="bulk", deal_date="2026-05-14", symbol="TCS",
             client_name="X", side="SELL", quantity=125000,
             avg_price=3525.00, value_inr=440625000),
    ]
    save_deals(isolated_db, deals1, "2026-05-14")
    save_deals(isolated_db, deals2, "2026-05-14")
    row = isolated_db.execute(
        "SELECT bulk_deals_json FROM flows_snapshots WHERE snapshot_date='2026-05-14'"
    ).fetchone()
    bulk = json.loads(row["bulk_deals_json"])
    assert len(bulk) == 2   # RELIANCE + TCS
    syms = {d["symbol"] for d in bulk}
    assert syms == {"RELIANCE", "TCS"}


def test_save_deals_empty_returns_zero(isolated_db):
    assert save_deals(isolated_db, [], "2026-05-14") == 0


def test_get_deals_for_symbol_finds_matching(isolated_db):
    save_deals(isolated_db, _sample_deals(), "2026-05-14")
    deals = get_deals_for_symbol(isolated_db, "RELIANCE", days_back=30)
    assert len(deals) == 1
    assert deals[0]["side"] == "BUY"
    assert deals[0]["snapshot_date"] == "2026-05-14"


def test_get_deals_for_symbol_handles_ns_suffix(isolated_db):
    save_deals(isolated_db, _sample_deals(), "2026-05-14")
    deals = get_deals_for_symbol(isolated_db, "RELIANCE.NS", days_back=30)
    assert len(deals) == 1


def test_get_deals_for_symbol_empty_when_no_match(isolated_db):
    save_deals(isolated_db, _sample_deals(), "2026-05-14")
    deals = get_deals_for_symbol(isolated_db, "NONEXISTENT", days_back=30)
    assert deals == []
