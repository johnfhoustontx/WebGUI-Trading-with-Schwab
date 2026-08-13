import pytest
from services.options_svc import commission as c


def test_index_symbols_detected():
    assert c.is_index_symbol("$SPX") is True
    assert c.is_index_symbol("SPX") is True
    assert c.is_index_symbol("$VIX") is True
    assert c.is_index_symbol("SPY") is False
    assert c.is_index_symbol("AAPL") is False


def test_equity_option_per_leg():
    assert c.commission_for(legs=2, symbol="SPY", qty=1) == pytest.approx(1.30)


def test_equity_option_scales_with_qty():
    assert c.commission_for(legs=4, symbol="SPY", qty=3) == pytest.approx(7.80)


def test_index_uses_index_rate_plus_exchange_fee(monkeypatch):
    monkeypatch.setattr(c, "_RATES", {
        "options": {"equity": 0.65, "index": 0.65, "index_exchange_fee": 0.49},
        "futures": {"standard": 2.25, "exchange_fee": 0.0},
    })
    assert c.commission_for(legs=2, symbol="$SPX", qty=1) == pytest.approx(2.28)


def test_zero_legs_is_free():
    assert c.commission_for(legs=0, symbol="SPY", qty=5) == 0.0


def test_futures_round_turn_per_side():
    assert c.futures_commission(qty=2) == pytest.approx(2 * 2.25 * 2)


def test_index_path_uses_real_config():
    # With the shipped config (index_exchange_fee = 0.00) the index branch
    # reads real rates and equals the equity cost. Pins the $-strip + _INDEX_ROOTS
    # membership against committed config, not a monkeypatched table.
    assert c.commission_for(legs=2, symbol="$SPX", qty=1) == pytest.approx(1.30)
    assert c.commission_for(legs=2, symbol="$VIX", qty=1) == pytest.approx(1.30)


# ── round_trip_commission (break-even close floor) ──────────────────────────
def test_round_trip_pcs_is_four_leg_fills():
    # PCS = 2 legs → open + close = 4 leg-fills × $0.65 = $2.60.
    assert c.round_trip_commission("PCS", "SPY", 1) == pytest.approx(2.60)


def test_round_trip_ccs_is_four_leg_fills():
    assert c.round_trip_commission("CCS", "SPY", 1) == pytest.approx(2.60)


def test_round_trip_ic_is_eight_leg_fills():
    # IC = 4 legs → open + close = 8 leg-fills × $0.65 = $5.20.
    assert c.round_trip_commission("IC", "SPY", 1) == pytest.approx(5.20)


def test_round_trip_scales_with_qty():
    assert c.round_trip_commission("PCS", "SPY", 3) == pytest.approx(2 * 3 * 0.65 * 2)


def test_round_trip_case_insensitive_and_default_two_legs():
    assert c.round_trip_commission("pcs", "SPY", 1) == pytest.approx(2.60)
    # An unknown structure conservatively assumes 2 legs (a vertical).
    assert c.round_trip_commission("WAT", "SPY", 1) == pytest.approx(2.60)
    assert c.round_trip_commission(None, "SPY", 1) == pytest.approx(2.60)
