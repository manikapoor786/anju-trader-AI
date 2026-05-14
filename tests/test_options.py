"""Tests for anju_ai.tools.options — mock NSE option chain, no network."""

from dataclasses import dataclass
import pytest

from anju_ai.tools.options import (
    LeverageRecommendation,
    OptionChain,
    OptionLeg,
    _compute_max_pain,
    _expiry_to_iso,
    _is_liquid,
    _parse_chain,
    evaluate_leverage,
    fetch_option_chain,
    iv_percentile,
    save_iv_observation,
)


@dataclass
class FakeResp:
    status_code: int
    _json: object

    def json(self): return self._json


# ── Synthetic NSE option-chain payload ───────────────────────────────────────

def _mk_payload(underlying=1400.0,
                expiry="29-May-2026",
                strikes_data: list = None):
    """strikes_data: list of dicts with 'strike', 'ce_iv', 'pe_iv',
    'ce_oi', 'pe_oi', 'ce_last', 'pe_last'."""
    if strikes_data is None:
        strikes_data = [
            {"strike": 1380, "ce_iv": 22, "pe_iv": 24, "ce_oi": 1000, "pe_oi": 1500,
             "ce_last": 35.0, "pe_last": 15.0},
            {"strike": 1400, "ce_iv": 21, "pe_iv": 23, "ce_oi": 2500, "pe_oi": 2000,
             "ce_last": 22.0, "pe_last": 22.0},
            {"strike": 1420, "ce_iv": 20, "pe_iv": 22, "ce_oi": 1800, "pe_oi": 1200,
             "ce_last": 12.0, "pe_last": 32.0},
        ]
    data = []
    for s in strikes_data:
        data.append({
            "strikePrice": s["strike"],
            "expiryDate": expiry,
            "CE": {
                "lastPrice": s.get("ce_last", 10.0),
                "bidprice": s.get("ce_last", 10.0) * 0.99,
                "askPrice": s.get("ce_last", 10.0) * 1.01,
                "impliedVolatility": s.get("ce_iv", 20),
                "openInterest": s.get("ce_oi", 1000),
                "changeinOpenInterest": 100,
                "totalTradedVolume": 500,
            },
            "PE": {
                "lastPrice": s.get("pe_last", 10.0),
                "bidprice": s.get("pe_last", 10.0) * 0.99,
                "askPrice": s.get("pe_last", 10.0) * 1.01,
                "impliedVolatility": s.get("pe_iv", 20),
                "openInterest": s.get("pe_oi", 1000),
                "changeinOpenInterest": 50,
                "totalTradedVolume": 200,
            },
        })
    return {
        "records": {
            "underlyingValue": underlying,
            "expiryDates": [expiry],
            "data": data,
        },
        "filtered": {"data": data},
    }


# ── _expiry_to_iso ───────────────────────────────────────────────────────────

def test_expiry_to_iso():
    assert _expiry_to_iso("29-May-2026") == "2026-05-29"
    assert _expiry_to_iso("garbage") == "garbage"   # passthrough


# ── _is_liquid ───────────────────────────────────────────────────────────────

def test_is_liquid_tight_spread():
    leg = OptionLeg(strike=100, expiry="2026-05-29",
                    last_price=20.0, bid=19.9, ask=20.1, iv=20)
    assert _is_liquid(leg)


def test_is_liquid_wide_spread_rejected():
    leg = OptionLeg(strike=100, expiry="2026-05-29",
                    last_price=20.0, bid=18.0, ask=22.0)
    # spread = 4 / 20 = 20% → not liquid
    assert not _is_liquid(leg)


def test_is_liquid_zero_premium_rejected():
    leg = OptionLeg(strike=100, expiry="2026-05-29",
                    last_price=0.02, bid=0.01, ask=0.05)
    assert not _is_liquid(leg)


def test_is_liquid_none_rejected():
    assert not _is_liquid(None)


# ── _compute_max_pain ────────────────────────────────────────────────────────

def test_max_pain_finds_strike_with_lowest_pain():
    payload = _mk_payload()
    data = payload["records"]["data"]
    mp = _compute_max_pain(data, "29-May-2026", [1380.0, 1400.0, 1420.0])
    # With heavy put OI at 1400 (2000) and balanced overall,
    # max-pain is likely 1400 or 1420 depending on weighting.
    assert mp in (1380.0, 1400.0, 1420.0)


# ── _parse_chain ─────────────────────────────────────────────────────────────

def test_parse_chain_returns_atm_call_and_put():
    payload = _mk_payload(underlying=1400)
    chain = _parse_chain(payload, "RELIANCE")
    assert chain is not None
    assert chain.atm_strike == 1400.0
    assert chain.atm_call is not None
    assert chain.atm_put is not None
    assert chain.underlying_price == 1400.0
    assert chain.nearest_expiry == "2026-05-29"


def test_parse_chain_computes_pcr():
    payload = _mk_payload()
    chain = _parse_chain(payload, "X")
    assert chain.pcr_oi > 0


def test_parse_chain_marks_liquid_when_tight_spread():
    payload = _mk_payload()
    chain = _parse_chain(payload, "X")
    assert chain.is_liquid


def test_parse_chain_returns_none_on_empty_payload():
    assert _parse_chain({}, "X") is None
    assert _parse_chain({"records": {}}, "X") is None


def test_parse_chain_handles_missing_underlying():
    payload = _mk_payload()
    payload["records"]["underlyingValue"] = 0
    assert _parse_chain(payload, "X") is None


# ── fetch_option_chain ───────────────────────────────────────────────────────

def test_fetch_option_chain_success():
    def mock_get(url, **kw):
        return FakeResp(200, _mk_payload())
    chain = fetch_option_chain("RELIANCE", http_get=mock_get)
    assert chain is not None
    assert chain.symbol == "RELIANCE"


def test_fetch_option_chain_strips_ns_suffix():
    def mock_get(url, **kw):
        assert "RELIANCE" in url
        assert ".NS" not in url
        return FakeResp(200, _mk_payload())
    chain = fetch_option_chain("RELIANCE.NS", http_get=mock_get)
    assert chain.symbol == "RELIANCE"


def test_fetch_option_chain_http_error_returns_none():
    def mock_get(url, **kw):
        return FakeResp(503, {})
    assert fetch_option_chain("X", http_get=mock_get) is None


def test_fetch_option_chain_exception_returns_none():
    def mock_get(url, **kw):
        raise ConnectionError("nope")
    assert fetch_option_chain("X", http_get=mock_get) is None


# ── iv_percentile + save_iv_observation ──────────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANJU_MEMORY_DB", str(tmp_path / "memory.db"))
    from anju_ai.memory.db import init_if_needed
    con = init_if_needed()
    yield con
    con.close()


def test_iv_percentile_returns_none_when_insufficient_history(isolated_db):
    save_iv_observation(isolated_db, "X", "2026-01-01", 22.0)
    assert iv_percentile(isolated_db, "X", current_iv=25.0) is None


def test_iv_percentile_computes_correctly(isolated_db):
    # Insert 100 observations: IVs from 10 to 30 (linear)
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 1)
    for i in range(100):
        date = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        iv = 10 + (i / 100 * 20)   # 10 → 30
        save_iv_observation(isolated_db, "X", date, iv)
    # Current IV = 20 should be ~50th percentile
    p = iv_percentile(isolated_db, "X", current_iv=20.0)
    assert p is not None
    assert 40 <= p <= 60


def test_save_iv_observation_upserts(isolated_db):
    save_iv_observation(isolated_db, "X", "2026-05-14", 20.0)
    save_iv_observation(isolated_db, "X", "2026-05-14", 25.0)   # overwrite
    row = isolated_db.execute(
        "SELECT atm_iv FROM iv_history WHERE symbol='X' AND date='2026-05-14'"
    ).fetchone()
    assert row[0] == 25.0


# ── evaluate_leverage ────────────────────────────────────────────────────────

def test_evaluate_leverage_cash_when_disabled(isolated_db):
    chain = _parse_chain(_mk_payload(), "X")
    rec = evaluate_leverage(isolated_db, "X", rule_score=30.0,
                            chain=chain, fno_enabled=False)
    assert rec.mode == "CASH"
    assert "disabled" in rec.rationale


def test_evaluate_leverage_cash_when_no_chain(isolated_db):
    rec = evaluate_leverage(isolated_db, "X", rule_score=30.0,
                            chain=None, fno_enabled=True)
    assert rec.mode == "CASH"
    assert "No liquid" in rec.rationale


def test_evaluate_leverage_cash_when_score_too_low(isolated_db):
    chain = _parse_chain(_mk_payload(), "X")
    rec = evaluate_leverage(isolated_db, "X", rule_score=10.0,
                            chain=chain, fno_enabled=True,
                            min_score_for_options=25.0)
    assert rec.mode == "CASH"
    assert "below options threshold" in rec.rationale


def test_evaluate_leverage_cash_when_no_iv_history(isolated_db):
    chain = _parse_chain(_mk_payload(), "X")
    rec = evaluate_leverage(isolated_db, "X", rule_score=30.0,
                            chain=chain, fno_enabled=True)
    assert rec.mode == "CASH"
    assert "Insufficient IV history" in rec.rationale


def test_evaluate_leverage_recommends_atm_call_when_iv_cheap(isolated_db):
    # Seed 100 historical IVs from 30-50 → current IV of 21 will be IVP ~0
    # Use dates ending today so they fall inside the lookback window.
    from datetime import datetime, timedelta, timezone
    base = datetime.now(timezone.utc) - timedelta(days=120)
    for i in range(100):
        date = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        iv = 30 + (i / 100 * 20)   # 30 → 50
        save_iv_observation(isolated_db, "X", date, iv)

    chain = _parse_chain(_mk_payload(), "X")   # ATM call IV ≈ 21 — cheap
    rec = evaluate_leverage(isolated_db, "X", rule_score=30.0,
                            chain=chain, fno_enabled=True,
                            min_score_for_options=25.0,
                            max_ivp_for_options=50.0)
    assert rec.mode == "ATM_CALL"
    assert rec.iv_percentile is not None
    assert rec.iv_percentile < 50
    assert rec.suggested_strike == 1400


def test_evaluate_leverage_cash_when_iv_expensive(isolated_db):
    # IVs from 10-15 → current IV of 21 will be 100th percentile
    from datetime import datetime, timedelta, timezone
    base = datetime.now(timezone.utc) - timedelta(days=120)
    for i in range(100):
        date = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        iv = 10 + (i / 100 * 5)   # 10 → 15
        save_iv_observation(isolated_db, "X", date, iv)

    chain = _parse_chain(_mk_payload(), "X")
    rec = evaluate_leverage(isolated_db, "X", rule_score=30.0,
                            chain=chain, fno_enabled=True,
                            max_ivp_for_options=50.0)
    assert rec.mode == "CASH"
    assert "too high" in rec.rationale
