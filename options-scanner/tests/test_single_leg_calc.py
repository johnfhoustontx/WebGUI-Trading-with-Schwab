"""Tests for single-leg strategies in options_calculator.calc_summary."""

import pytest

from options_calculator import calc_summary, bs_price

R = 0.045
IV = 0.20
T = 30 / 365.0
SPOT = 100.0

UNLIMITED = 999999


def _leg(strike, opt_type, side, qty=1):
    premium = bs_price(SPOT, strike, T, R, IV, opt_type)
    return {
        "strike": strike,
        "option_type": opt_type,
        "side": side,
        "premium": premium,
        "qty": qty,
    }


def test_long_put_summary():
    legs = [_leg(95.0, "put", "long")]
    s = calc_summary(legs, "LONG_PUT", SPOT, r=R, iv=IV, T=T)
    debit = legs[0]["premium"] * 100
    assert s["entry_credit"] == pytest.approx(-debit, abs=0.01)
    assert s["max_profit"] == pytest.approx(95.0 * 100 - debit, abs=0.01)
    assert s["max_loss"] == pytest.approx(debit, abs=0.01)
    assert s["breakevens"] == [pytest.approx(95.0 - legs[0]["premium"], abs=0.01)]
    assert 0.0 <= s["pop"] <= 100.0


def test_long_call_summary():
    legs = [_leg(105.0, "call", "long")]
    s = calc_summary(legs, "LONG_CALL", SPOT, r=R, iv=IV, T=T)
    debit = legs[0]["premium"] * 100
    assert s["entry_credit"] == pytest.approx(-debit, abs=0.01)
    assert s["max_profit"] == UNLIMITED
    assert s["max_loss"] == pytest.approx(debit, abs=0.01)
    assert s["breakevens"] == [pytest.approx(105.0 + legs[0]["premium"], abs=0.01)]
    assert 0.0 <= s["pop"] <= 100.0


def test_naked_put_summary():
    legs = [_leg(95.0, "put", "short")]
    s = calc_summary(legs, "NAKED_PUT", SPOT, r=R, iv=IV, T=T)
    credit = legs[0]["premium"] * 100
    assert s["entry_credit"] == pytest.approx(credit, abs=0.01)
    assert s["max_profit"] == pytest.approx(credit, abs=0.01)
    assert s["max_loss"] == pytest.approx(95.0 * 100 - credit, abs=0.01)
    assert s["breakevens"] == [pytest.approx(95.0 - legs[0]["premium"], abs=0.01)]
    assert 0.0 <= s["pop"] <= 100.0


def test_naked_call_summary():
    legs = [_leg(105.0, "call", "short")]
    s = calc_summary(legs, "NAKED_CALL", SPOT, r=R, iv=IV, T=T)
    credit = legs[0]["premium"] * 100
    assert s["entry_credit"] == pytest.approx(credit, abs=0.01)
    assert s["max_profit"] == pytest.approx(credit, abs=0.01)
    assert s["max_loss"] == UNLIMITED
    assert s["breakevens"] == [pytest.approx(105.0 + legs[0]["premium"], abs=0.01)]
    assert 0.0 <= s["pop"] <= 100.0


def test_long_put_pop_directional():
    """Deep OTM long put should have low PoP; deep ITM should be high."""
    otm = calc_summary([_leg(80.0, "put", "long")], "LONG_PUT", SPOT, r=R, iv=IV, T=T)
    itm = calc_summary([_leg(120.0, "put", "long")], "LONG_PUT", SPOT, r=R, iv=IV, T=T)
    assert otm["pop"] < itm["pop"]


def test_naked_put_pop_directional():
    """Deep OTM naked put should have high PoP."""
    otm = calc_summary([_leg(80.0, "put", "short")], "NAKED_PUT", SPOT, r=R, iv=IV, T=T)
    atm = calc_summary([_leg(100.0, "put", "short")], "NAKED_PUT", SPOT, r=R, iv=IV, T=T)
    assert otm["pop"] > atm["pop"]
