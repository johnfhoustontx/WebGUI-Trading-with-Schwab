"""A zero bid is a market; a -999 Greek is not a reading.

AC-08: a leg whose bid is 0 (a far out-of-the-money long near expiry) made the
whole position unmarkable, so a winning spread got no mark, no target and no
profit lock exactly when it had won.

AC-09: Schwab's ``-999`` placeholder was read as a gamma and as a delta.
"""
import datetime
import math

import pytest

import gamma_tool
import signal_recommender
import signal_repricer

EXP = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()


def _ctr(bid, ask, delta=-0.2, **extra):
    return [{"bid": bid, "ask": ask, "delta": delta, "gamma": 0.01, "theta": -0.02,
             "vega": 0.05, **extra}]


def _put_chain(short, long_):
    return {"putExpDateMap": {f"{EXP}:30": {"100.0": short, "95.0": long_}},
            "callExpDateMap": {}, "underlyingPrice": 110.0}


class _Resp:
    status_code = 200

    def __init__(self, chain):
        self._chain = chain

    def json(self):
        return self._chain


class _Client:
    class Options:
        class ContractType:
            ALL = "ALL"

    def __init__(self, chain):
        self._chain = chain

    def get_option_chain(self, symbol, **kw):
        return _Resp(self._chain)


def _pcs():
    return {"symbol": "XYZ", "expiration": EXP, "strategy": "PCS",
            "short_strike": 100.0, "long_strike": 95.0, "entry_credit": 1.00}


# ------------------------------------------------------------------ AC-08

def test_a_zero_bid_leg_still_has_a_market():
    leg_map = {f"{EXP}:30": {"95.0": _ctr(0.0, 0.05)}}
    assert signal_repricer._leg_bid_ask(leg_map, 95.0)[:2] == (0.0, 0.05)
    mid, _ = signal_repricer._leg_mid(leg_map, 95.0)
    assert mid == pytest.approx(0.025)


@pytest.mark.parametrize("bid,ask", [(0.0, 0.0), (0.10, 0.0), (-0.05, 0.10),
                                     (float("nan"), 0.10), (0.05, float("nan"))])
def test_a_leg_with_no_offer_or_a_bad_quote_has_none(bid, ask):
    leg_map = {f"{EXP}:30": {"95.0": _ctr(bid, ask)}}
    assert signal_repricer._leg_bid_ask(leg_map, 95.0) == (None, None, None)
    assert signal_repricer._leg_mid(leg_map, 95.0) == (None, None)


def test_a_winning_spread_whose_long_leg_has_no_bid_is_still_marked():
    """The short is offered at 0.10, the long is 0.00 x 0.05: the spread has
    nearly expired worthless. It must come back with a mark and a profit."""
    signal_repricer.clear_chain_cache()
    chain = _put_chain(_ctr(0.05, 0.10), _ctr(0.0, 0.05, delta=-0.03))
    out = signal_repricer.reprice_swing(_pcs(), _Client(chain))
    assert out["error"] is None
    assert out["current_value"] is not None and 0 <= out["current_value"] <= 0.10
    assert out["unrealized_pnl"] > 0


def test_a_zero_bid_under_a_large_offer_is_a_broken_quote():
    """0.00 x 1.20 is not a worthless option. Its midpoint would be fiction."""
    leg_map = {f"{EXP}:30": {"95.0": _ctr(0.0, 1.20)}}
    assert signal_repricer._leg_bid_ask(leg_map, 95.0) == (None, None, None)


def test_the_bound_is_the_configured_one(monkeypatch):
    leg_map = {f"{EXP}:30": {"95.0": _ctr(0.0, 0.25)}}
    assert signal_repricer._leg_bid_ask(leg_map, 95.0)[:2] == (0.0, 0.25)
    monkeypatch.setattr(signal_repricer._paper_limits, "zero_bid_max_ask", lambda: 0.10)
    assert signal_repricer._leg_bid_ask(leg_map, 95.0) == (None, None, None)
    monkeypatch.setattr(signal_repricer._paper_limits, "zero_bid_max_ask", lambda: 0.0)
    small = {f"{EXP}:30": {"95.0": _ctr(0.0, 0.05)}}
    assert signal_repricer._leg_bid_ask(small, 95.0) == (None, None, None)


def test_a_two_sided_market_is_never_bounded():
    leg_map = {f"{EXP}:30": {"95.0": _ctr(4.00, 5.20)}}
    assert signal_repricer._leg_bid_ask(leg_map, 95.0)[:2] == (4.00, 5.20)


# ------------------------------------------------------------------ AC-09

def test_the_repricer_does_not_hand_back_a_placeholder_delta():
    signal_repricer.clear_chain_cache()
    chain = _put_chain(_ctr(0.50, 0.60, delta=-999.0), _ctr(0.10, 0.15))
    out = signal_repricer.reprice_swing(_pcs(), _Client(chain))
    assert out["error"] is None
    assert out["current_short_delta"] is None


def test_position_greeks_do_not_sum_a_placeholder():
    chain = _put_chain(_ctr(0.50, 0.60, delta=-999.0, gamma=-999.0),
                       _ctr(0.10, 0.15, delta=-0.05))
    g = signal_repricer.position_greeks(_pcs(), chain)
    assert g["net_delta"] is None and g["net_gamma"] is None
    assert g["net_theta"] is not None          # the other Greeks are still read


def _ctx(**kw):
    base = {"entry_credit": 1.0, "unrealized_pnl": 0.0, "dte_remaining": 20,
            "current_short_delta": None, "entry_short_delta": 0.20,
            "current_score": 60, "entry_score": 55}
    base.update(kw)
    return base


@pytest.mark.parametrize("bad", [-999.0, float("nan"), 5.0])
def test_a_placeholder_delta_never_fires_the_delta_stop(bad):
    out = signal_recommender.recommend(_ctx(current_short_delta=bad))
    assert out["code"] != "DELTA_STOP", out


def test_a_real_delta_breach_still_fires():
    """Power check: the guard must not have switched the rule off."""
    out = signal_recommender.recommend(
        _ctx(current_short_delta=-0.60, dte_remaining=2))
    assert out["code"] == "DELTA_STOP", out


def _gamma_chain(gamma, delta):
    today = datetime.datetime.now(gamma_tool.TZ).strftime("%Y-%m-%d")
    key = f"{today}:0"

    def side(g, d):
        return {key: {"100.0": [{"gamma": g, "delta": d, "openInterest": 100,
                                 "totalVolume": 100, "volatility": 20.0,
                                 "daysToExpiration": 0}]}}
    return {"underlyingPrice": 100.0, "callExpDateMap": side(gamma, delta),
            "putExpDateMap": side(0.02, -0.5)}


def test_a_placeholder_gamma_adds_no_exposure():
    engine = gamma_tool.GammaEngine()
    gex, _c, _d, _v = engine.calc_all_from_chain(_gamma_chain(-999.0, 0.5))
    assert gex["gex"][100.0]["call"] == 0.0
    assert gex["gex"][100.0]["put"] < 0            # the real put is still counted
    alone = gamma_tool.GammaEngine().calc_from_chain(_gamma_chain(-999.0, 0.5))
    assert alone["gex"][100.0]["call"] == 0.0


def test_a_placeholder_delta_falls_back_to_the_model():
    engine = gamma_tool.GammaEngine()
    _g, _c, dex, _v = engine.calc_all_from_chain(_gamma_chain(0.02, -999.0))
    call = dex["gex"][100.0]["call"]
    assert math.isfinite(call) and 0 < call < 100 * 1.0 * 100 * 100.0
