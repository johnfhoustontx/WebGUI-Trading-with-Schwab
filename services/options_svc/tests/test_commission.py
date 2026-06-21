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
