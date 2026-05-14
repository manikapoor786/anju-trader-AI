"""Tests for anju_ai.tools.insider — mock NSE responses, no network."""

from dataclasses import dataclass
import json
import pytest

from anju_ai.tools.insider import (
    InsiderTransaction,
    _merge_insider,
    _parse_insider,
    _to_nse_date,
    fetch_insider,
    get_insider_for_symbol,
    insider_signal_for_symbol,
    save_insider,
)


@dataclass
class FakeResponse:
    status_code: int
    _json: object

    def json(self):
        return self._json


# ── Sample NSE payload ───────────────────────────────────────────────────────

INSIDER_PAYLOAD = {
    "data": [
        {"symbol": "RELIANCE", "company": "Reliance Industries",
         "personCategory": "Promoter", "transactionType": "Buy",
         "acquisitionMode": "Market Purchase",
         "securitiesAcquired": "50000", "securitiesValue": "71375000",
         "date": "13-May-2026", "personName": "ABC Holdings Pvt Ltd"},
        {"symbol": "TCS", "company": "Tata Consultancy",
         "personCategory": "Designated Person", "transactionType": "Sell",
         "acquisitionMode": "Off Market",
         "securitiesAcquired": "1,200", "securitiesValue": "4230000",
         "date": "13-May-2026", "personName": "Director XYZ"},
    ]
}


# ── _parse_insider ───────────────────────────────────────────────────────────

def test_parse_extracts_two_transactions():
    txs = _parse_insider(INSIDER_PAYLOAD)
    assert len(txs) == 2
    rel = next(t for t in txs if t.symbol == "RELIANCE")
    assert rel.side == "BUY"
    assert rel.person_category == "Promoter"
    assert rel.qty == 50000
    assert rel.value_inr == 71375000.0


def test_parse_handles_commas_in_quantity():
    txs = _parse_insider(INSIDER_PAYLOAD)
    tcs = next(t for t in txs if t.symbol == "TCS")
    assert tcs.qty == 1200


def test_parse_normalises_sell_variants():
    payload = {"data": [
        {"symbol": "A", "company": "X", "personCategory": "P",
         "transactionType": "Sell", "securitiesAcquired": "100",
         "securitiesValue": "1000", "date": "13-May-2026"},
        {"symbol": "B", "company": "X", "personCategory": "P",
         "transactionType": "Buy", "securitiesAcquired": "100",
         "securitiesValue": "1000", "date": "13-May-2026"},
        {"symbol": "C", "company": "X", "personCategory": "P",
         "transactionType": "ACQUISITION", "securitiesAcquired": "100",
         "securitiesValue": "1000", "date": "13-May-2026"},
    ]}
    txs = _parse_insider(payload)
    sides = {t.symbol: t.side for t in txs}
    assert sides == {"A": "SELL", "B": "BUY", "C": "BUY"}


def test_parse_handles_data_wrapper_vs_plain_list():
    plain = INSIDER_PAYLOAD["data"]
    a = _parse_insider(INSIDER_PAYLOAD)
    b = _parse_insider(plain)
    assert len(a) == len(b) == 2


def test_parse_returns_empty_on_wrong_shape():
    assert _parse_insider("string") == []
    assert _parse_insider(None) == []
    assert _parse_insider([{"missing": "fields"}]) == []


def test_parse_normalises_date_to_iso():
    txs = _parse_insider(INSIDER_PAYLOAD)
    for t in txs:
        assert t.date == "2026-05-13"


def test_parse_filters_zero_qty():
    payload = {"data": [{
        "symbol": "A", "company": "X", "personCategory": "P",
        "transactionType": "Buy", "securitiesAcquired": "0",
        "securitiesValue": "0", "date": "13-May-2026"
    }]}
    assert _parse_insider(payload) == []


# ── _to_nse_date ─────────────────────────────────────────────────────────────

def test_to_nse_date_iso_to_ddmmyyyy():
    assert _to_nse_date("2026-05-14") == "14-05-2026"


# ── fetch_insider ────────────────────────────────────────────────────────────

def test_fetch_insider_success():
    def mock_get(url, **kwargs):
        return FakeResponse(200, INSIDER_PAYLOAD)
    txs = fetch_insider(http_get=mock_get)
    assert len(txs) == 2


def test_fetch_insider_http_error_returns_empty():
    def mock_get(url, **kwargs):
        return FakeResponse(503, {})
    assert fetch_insider(http_get=mock_get) == []


def test_fetch_insider_exception_returns_empty():
    def mock_get(url, **kwargs):
        raise ConnectionError("nope")
    assert fetch_insider(http_get=mock_get) == []


# ── _merge_insider ───────────────────────────────────────────────────────────

def test_merge_insider_dedupes():
    base = [{"symbol": "A", "date": "2026-05-13", "side": "BUY",
             "qty": 100, "value_inr": 10000.0, "person_name": "X"}]
    new = [
        {"symbol": "A", "date": "2026-05-13", "side": "BUY",
         "qty": 100, "value_inr": 10000.0, "person_name": "X"},   # dup
        {"symbol": "A", "date": "2026-05-13", "side": "BUY",
         "qty": 200, "value_inr": 20000.0, "person_name": "Y"},   # new
    ]
    merged = _merge_insider(base, new)
    assert len(merged) == 2


# ── Save + read (DB integration) ─────────────────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    yield con
    con.close()


def _sample_txs():
    return [
        InsiderTransaction(date="2026-05-13", symbol="RELIANCE",
                           company="Reliance Industries",
                           person_category="Promoter", side="BUY",
                           mode="Market Purchase", qty=50000,
                           value_inr=71375000, person_name="ABC"),
        InsiderTransaction(date="2026-05-13", symbol="TCS",
                           company="Tata Consultancy",
                           person_category="Designated Person", side="SELL",
                           mode="Off Market", qty=1200, value_inr=4230000),
    ]


def test_save_insider_creates_snapshot_with_json(isolated_db):
    out = save_insider(isolated_db, _sample_txs())
    assert "2026-05-13" in out
    rid = out["2026-05-13"]
    row = isolated_db.execute(
        "SELECT insider_json FROM flows_snapshots WHERE id=?", (rid,)
    ).fetchone()
    parsed = json.loads(row["insider_json"])
    assert len(parsed) == 2


def test_save_insider_groups_by_date(isolated_db):
    txs = _sample_txs() + [InsiderTransaction(
        date="2026-05-14", symbol="HDFCBANK", company="HDFC Bank",
        person_category="Promoter", side="BUY", mode="Market",
        qty=10000, value_inr=16000000)]
    out = save_insider(isolated_db, txs)
    assert set(out.keys()) == {"2026-05-13", "2026-05-14"}


def test_save_insider_merges_on_resave(isolated_db):
    save_insider(isolated_db, _sample_txs())
    save_insider(isolated_db, _sample_txs())  # same txs again
    row = isolated_db.execute(
        "SELECT insider_json FROM flows_snapshots WHERE snapshot_date='2026-05-13'"
    ).fetchone()
    parsed = json.loads(row["insider_json"])
    assert len(parsed) == 2   # not duplicated


def test_get_insider_for_symbol_filters_correctly(isolated_db):
    save_insider(isolated_db, _sample_txs())
    rel = get_insider_for_symbol(isolated_db, "RELIANCE", days_back=30)
    assert len(rel) == 1
    assert rel[0]["side"] == "BUY"


def test_get_insider_for_symbol_strips_ns_suffix(isolated_db):
    save_insider(isolated_db, _sample_txs())
    rel = get_insider_for_symbol(isolated_db, "RELIANCE.NS", days_back=30)
    assert len(rel) == 1


def test_insider_signal_aggregates_correctly(isolated_db):
    save_insider(isolated_db, _sample_txs())
    sig = insider_signal_for_symbol(isolated_db, "RELIANCE", days_back=30)
    assert sig["n"] == 1
    assert sig["n_buys"] == 1
    assert sig["n_sells"] == 0
    assert sig["net_qty"] == 50000
    assert sig["promoter_net_value_inr"] == 71375000.0


def test_insider_signal_zero_when_no_data(isolated_db):
    sig = insider_signal_for_symbol(isolated_db, "NONEXISTENT", days_back=30)
    assert sig["n"] == 0
    assert sig["net_qty"] == 0
    assert sig["net_value_inr"] == 0.0
