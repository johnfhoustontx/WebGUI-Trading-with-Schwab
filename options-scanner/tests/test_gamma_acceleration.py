"""Tests for the 0DTE/7DTE gamma-acceleration ratio."""
import pytest

from gamma_tool import calc_gamma_acceleration, build_analysis_dict, GammaEngine


def _exp(gamma_oi_pairs):
    """Build one expiry's strike map from (gamma, oi) pairs."""
    return {
        f"{100 + i}.0": [{"gamma": g, "openInterest": oi}]
        for i, (g, oi) in enumerate(gamma_oi_pairs)
    }


def test_gamma_accel_none_on_empty():
    assert calc_gamma_acceleration(None)["ratio"] is None


def test_gamma_accel_basic_ratio():
    # Near expiry (:0): Σ gamma·OI = 0.1*1000 = 100 across calls+puts split.
    # Far expiry (:7):  Σ gamma·OI = 0.05*500 = 25.
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {
            "2099-12-31:0": _exp([(0.1, 500)]),
            "2099-12-31:7": _exp([(0.05, 250)]),
        },
        "putExpDateMap": {
            "2099-12-31:0": _exp([(0.1, 500)]),
            "2099-12-31:7": _exp([(0.05, 250)]),
        },
    }
    r = calc_gamma_acceleration(chain)
    # near = 0.1*500 + 0.1*500 = 100 ; far = 0.05*250 + 0.05*250 = 25 ; ratio 4
    assert r["ratio"] == pytest.approx(4.0)
    assert r["dte_near"] == 0
    assert r["dte_far"] == 7


def test_gamma_accel_single_expiry_is_none():
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {"2099-12-31:0": _exp([(0.1, 500)])},
        "putExpDateMap": {"2099-12-31:0": _exp([(0.1, 500)])},
    }
    assert calc_gamma_acceleration(chain)["ratio"] is None


def test_gamma_accel_picks_closest_to_seven():
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {
            "2099-12-31:0": _exp([(0.2, 500)]),   # near, G=100
            "2099-12-31:5": _exp([(0.1, 500)]),   # |5-7|=2
            "2099-12-31:8": _exp([(0.04, 500)]),  # |8-7|=1 -> chosen, G=20
        },
        "putExpDateMap": {},
    }
    r = calc_gamma_acceleration(chain)
    assert r["dte_far"] == 8
    assert r["ratio"] == pytest.approx(100.0 / 20.0)


def test_analysis_dict_includes_gamma_acceleration():
    chain = {
        "underlyingPrice": 5800.0,
        "callExpDateMap": {
            "2099-12-31:0": {"5800.0": [
                {"delta": 0.5, "gamma": 0.1, "openInterest": 1000,
                 "totalVolume": 0, "strikePrice": 5800}]},
            "2099-12-31:7": {"5800.0": [
                {"delta": 0.5, "gamma": 0.05, "openInterest": 1000,
                 "totalVolume": 0, "strikePrice": 5800}]},
        },
        "putExpDateMap": {
            "2099-12-31:0": {"5800.0": [
                {"delta": -0.5, "gamma": 0.1, "openInterest": 1000,
                 "totalVolume": 0, "strikePrice": 5800}]},
            "2099-12-31:7": {"5800.0": [
                {"delta": -0.5, "gamma": 0.05, "openInterest": 1000,
                 "totalVolume": 0, "strikePrice": 5800}]},
        },
    }
    gex = GammaEngine().calc_from_chain(chain)
    result = build_analysis_dict(gex, "gex", "SPX", dte=0,
                                 expected_move=40.0, chain=chain)
    assert "gamma_acceleration" in result
    assert result["gamma_acceleration"]["ratio"] == pytest.approx(2.0)
