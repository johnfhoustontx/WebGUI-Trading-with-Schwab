"""Tests for max pain, pin risk, and the zero-DTE magnet composite.

Pure functions over a synthetic Schwab option chain. A single far-future
expiry key ("2099-12-31:5") is used so the nearest-expiry picker always
selects it and the suite stays date-independent.
"""
import pytest

from gamma_tool import (
    calc_max_pain_from_chain,
    pin_risk,
    zero_dte_magnet,
)

EXP = "2099-12-31:5"


def _chain(spot, calls, puts):
    """Build a minimal chain. calls/puts: {strike: open_interest}."""
    def _side(oi_by_strike):
        return {
            EXP: {
                f"{k:.1f}": [{"openInterest": oi}]
                for k, oi in oi_by_strike.items()
            }
        }
    return {
        "underlyingPrice": spot,
        "callExpDateMap": _side(calls),
        "putExpDateMap": _side(puts),
    }


# ── calc_max_pain_from_chain ──

def test_max_pain_none_on_empty_chain():
    assert calc_max_pain_from_chain(None) is None
    assert calc_max_pain_from_chain({}) is None


def test_max_pain_picks_min_loss_strike():
    # Calls concentrated at 100 (hurt writers as price rises),
    # puts concentrated at 110 (hurt writers as price falls).
    # Writer pain is minimised between the two OI walls.
    chain = _chain(
        spot=105.0,
        calls={100.0: 1000, 105.0: 10, 110.0: 10},
        puts={100.0: 10, 105.0: 10, 110.0: 1000},
    )
    res = calc_max_pain_from_chain(chain)
    assert res is not None
    # Brute-force the expected argmin over the same listed strikes.
    strikes = [100.0, 105.0, 110.0]
    def pain(K):
        c = sum(oi * max(K - k, 0) for k, oi in
                {100.0: 1000, 105.0: 10, 110.0: 10}.items())
        p = sum(oi * max(k - K, 0) for k, oi in
                {100.0: 10, 105.0: 10, 110.0: 1000}.items())
        return c + p
    expected = min(strikes, key=pain)
    assert res["max_pain"] == expected
    assert set(res["pain_curve"].keys()) == set(strikes)
    assert res["total_call_oi"] == 1020
    assert res["total_put_oi"] == 1020


def test_max_pain_handles_missing_oi():
    chain = _chain(spot=100.0, calls={100.0: 5}, puts={100.0: 5})
    # blow away one OI field to simulate a sparse contract
    chain["callExpDateMap"][EXP]["100.0"][0].pop("openInterest")
    res = calc_max_pain_from_chain(chain)
    assert res is not None
    assert res["max_pain"] == 100.0


# ── pin_risk ──

def test_pin_risk_on_max_pain_is_one():
    assert pin_risk(spot=100.0, max_pain=100.0, expected_move=5.0) == 1.0


def test_pin_risk_one_em_away_is_zero():
    assert pin_risk(spot=105.0, max_pain=100.0, expected_move=5.0) == 0.0


def test_pin_risk_halfway():
    assert pin_risk(spot=102.5, max_pain=100.0, expected_move=5.0) == pytest.approx(0.5)


def test_pin_risk_beyond_em_clamps_to_zero():
    assert pin_risk(spot=120.0, max_pain=100.0, expected_move=5.0) == 0.0


def test_pin_risk_none_without_em():
    assert pin_risk(spot=100.0, max_pain=100.0, expected_move=None) is None
    assert pin_risk(spot=100.0, max_pain=100.0, expected_move=0) is None


# ── zero_dte_magnet ──

def test_magnet_agrees_when_close():
    m = zero_dte_magnet(spot=100.0, max_pain=100.0, key_gamma_strike=100.05)
    assert m["agree"] is True
    assert m["level"] == pytest.approx(100.025)
    assert m["confidence"] >= 0.66


def test_magnet_disagrees_when_far():
    m = zero_dte_magnet(spot=100.0, max_pain=100.0, key_gamma_strike=103.0)
    assert m["agree"] is False
    assert m["max_pain"] == 100.0
    assert m["key_gamma"] == 103.0
    assert m["confidence"] < 0.66


def test_magnet_tolerates_none():
    m = zero_dte_magnet(spot=100.0, max_pain=None, key_gamma_strike=100.0)
    assert m["agree"] is False
    assert m["level"] == 100.0  # falls back to the available strike
    m2 = zero_dte_magnet(spot=100.0, max_pain=None, key_gamma_strike=None)
    assert m2["level"] is None


# ── build_analysis_dict wiring (Task 4) ──

def test_analysis_dict_includes_max_pain_block():
    from gamma_tool import build_analysis_dict, GammaEngine
    chain = {
        "underlyingPrice": 5800.0,
        "callExpDateMap": {"2099-12-31:1": {
            "5800.0": [{"delta": 0.5, "gamma": 0.05, "openInterest": 1000,
                        "totalVolume": 0, "strikePrice": 5800}],
        }},
        "putExpDateMap": {"2099-12-31:1": {
            "5800.0": [{"delta": -0.5, "gamma": 0.05, "openInterest": 1000,
                        "totalVolume": 0, "strikePrice": 5800}],
        }},
    }
    gex = GammaEngine().calc_from_chain(chain)
    result = build_analysis_dict(gex, "gex", "SPX", dte=0,
                                 expected_move=40.0, chain=chain)
    assert "max_pain" in result
    mp = result["max_pain"]
    assert mp is not None
    assert set(mp.keys()) >= {"max_pain", "pin_risk", "magnet"}
    assert mp["max_pain"] == 5800.0


def test_analysis_dict_max_pain_none_without_chain():
    from gamma_tool import build_analysis_dict, GammaEngine
    chain = {
        "underlyingPrice": 5800.0,
        "callExpDateMap": {"2099-12-31:1": {"5800.0": [
            {"delta": 0.5, "gamma": 0.05, "openInterest": 1000,
             "totalVolume": 0, "strikePrice": 5800}]}},
        "putExpDateMap": {"2099-12-31:1": {"5800.0": [
            {"delta": -0.5, "gamma": 0.05, "openInterest": 1000,
             "totalVolume": 0, "strikePrice": 5800}]}},
    }
    gex = GammaEngine().calc_from_chain(chain)
    result = build_analysis_dict(gex, "gex", "SPX", dte=0, expected_move=40.0)
    assert result["max_pain"] is None
