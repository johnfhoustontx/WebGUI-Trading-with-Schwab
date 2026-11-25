"""The repricers' valuation halves (2026-11-24).

``reprice_legs`` / ``reprice_swing`` / ``structure_marks.reprice`` each fetch a
chain and then value the trade on it. The valuation is split out
(``value_legs``, ``value_swing``, ``value_from_chains``) so the live Ledger
marks can run the SAME arithmetic on a chain built from streamed quotes. These
tests pin that each fetching function answers exactly what its half answers on
the chain it fetched, so the two can never drift.
"""
import json
import types

import pytest

import signal_repricer as sr
import structure_marks as sm

EXP = "2099-12-18"


def _ctr(bid, ask):
    return [{"bid": bid, "ask": ask, "delta": -0.2}]


def _chain(puts=None, calls=None, spot=410.0):
    return {"underlyingPrice": spot,
            "putExpDateMap": {f"{EXP}:30": {k: _ctr(*v) for k, v in (puts or {}).items()}},
            "callExpDateMap": {f"{EXP}:30": {k: _ctr(*v) for k, v in (calls or {}).items()}}}


def _client(chain):
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return chain
    return types.SimpleNamespace(
        get_option_chain=lambda *a, **k: _Resp(),
        Options=types.SimpleNamespace(ContractType=types.SimpleNamespace(ALL="ALL")))


@pytest.fixture(autouse=True)
def _fresh_cache():
    sr.clear_chain_cache()
    yield
    sr.clear_chain_cache()


PUTS = {"400.0": (1.00, 1.20), "399.0": (0.50, 0.70), "395.0": (0.20, 0.30)}
CALLS = {"420.0": (0.80, 1.00), "421.0": (0.40, 0.60)}


def test_value_legs_is_what_reprice_legs_answers():
    trade = {"symbol": "SPY", "expiration": EXP, "direction": "DEBIT",
             "entry_debit": 40.0,
             "legs": [{"kind": "put", "side": "long", "strike": 400.0, "qty": 1},
                      {"kind": "put", "side": "short", "strike": 395.0, "qty": 1}]}
    chain = _chain(PUTS)
    assert sr.reprice_legs(trade, _client(chain)) == sr.value_legs(trade, chain)
    assert sr.value_legs(trade, chain)["current_value"] == 0.85     # 1.10 - 0.25


@pytest.mark.parametrize("trade", [
    {"strategy": "PCS", "short_strike": 400, "long_strike": 399, "entry_credit": 0.50},
    {"strategy": "CCS", "short_strike": 420, "long_strike": 421, "entry_credit": 0.45},
    {"strategy": "IC", "short_strike": 400, "long_strike": 399, "call_short": 420,
     "call_long": 421, "entry_credit": 0.95},
])
def test_value_swing_is_what_reprice_swing_answers(trade):
    trade = {"symbol": "SPY", "expiration": EXP, "quantity": 1, **trade}
    chain = _chain(PUTS, CALLS)
    assert sr.reprice_swing(trade, _client(chain)) == sr.value_swing(trade, chain)


def test_a_missing_leg_quote_raises_in_the_half_and_is_no_mark_in_the_wrapper():
    trade = {"symbol": "SPY", "expiration": EXP, "strategy": "PCS",
             "short_strike": 400, "long_strike": 398, "entry_credit": 0.50}
    chain = _chain(PUTS)
    with pytest.raises(RuntimeError):
        sr.value_swing(trade, chain)
    assert sr.reprice_swing(trade, _client(chain))["current_value"] is None


def test_reprice_legs_on_a_CREDIT_row_values_nothing():
    """⚠ The trap the hourly Ledger exit pass fell into until 2026-11-24: a credit
    row keeps its strikes in ``short_strike`` / ``long_strike`` and has no
    ``legs``, so the legs repricer marks it at 0.00. Pinned so nobody routes a
    credit row back through it."""
    trade = {"symbol": "SPY", "expiration": EXP, "strategy": "PCS",
             "short_strike": 400, "long_strike": 399, "entry_credit": 0.50}
    rep = sr.value_legs(trade, _chain(PUTS))
    assert rep["current_value"] == 0.0 and rep["unrealized_pnl"] == 0.0
    assert sr.value_swing(trade, _chain(PUTS))["current_value"] > 0


def test_value_from_chains_is_what_structure_marks_reprice_answers():
    legs = [{"kind": "put", "side": "short", "strike": 400.0, "expiration": EXP, "qty": 1},
            {"kind": "put", "side": "long", "strike": 395.0, "expiration": EXP, "qty": 1}]
    row = {"symbol": "SPY", "expiration": EXP, "entry_credit": 0.80,
           "legs_json": json.dumps(legs)}
    chain = _chain(PUTS)
    assert sm.reprice(row, _client(chain)) == sm.value_from_chains(row, {EXP: chain})
    assert sm.value_from_chains(row, {EXP: chain})["current_value"] == 0.85


def test_value_from_chains_without_a_legs_chain_is_no_mark():
    legs = [{"kind": "put", "side": "short", "strike": 400.0, "expiration": EXP, "qty": 1}]
    row = {"symbol": "SPY", "entry_credit": 0.80, "legs_json": json.dumps(legs)}
    assert sm.value_from_chains(row, {})["error"] == "no chain"
    assert sm.value_from_chains(row, {EXP: _chain({})})["error"] == "missing leg quote"
