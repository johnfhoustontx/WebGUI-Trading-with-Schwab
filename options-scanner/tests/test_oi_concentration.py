"""Tests for the OI concentration (Herfindahl) scalar."""
import pytest

from gamma_tool import calc_oi_concentration, build_analysis_dict, build_explain_text, GammaEngine

EXP = "2099-12-31:5"


def _chain(spot, calls, puts):
    def _side(d):
        return {EXP: {f"{k:.1f}": [{"openInterest": oi}] for k, oi in d.items()}}
    return {
        "underlyingPrice": spot,
        "callExpDateMap": _side(calls),
        "putExpDateMap": _side(puts),
    }


def test_oi_concentration_empty():
    assert calc_oi_concentration(None) == {"hhi": None, "n_strikes": 0}
    assert calc_oi_concentration({}) == {"hhi": None, "n_strikes": 0}


def test_oi_concentration_single_strike_is_one():
    chain = _chain(spot=100.0, calls={100.0: 500}, puts={})
    r = calc_oi_concentration(chain)
    assert r["hhi"] == pytest.approx(1.0)
    assert r["n_strikes"] == 1


def test_oi_concentration_even_spread():
    # 4 strikes, equal combined OI → HHI = 4*(1/4)^2 = 0.25
    chain = _chain(
        spot=100.0,
        calls={100.0: 100, 101.0: 100},
        puts={102.0: 100, 103.0: 100},
    )
    r = calc_oi_concentration(chain)
    assert r["hhi"] == pytest.approx(0.25)
    assert r["n_strikes"] == 4


def test_oi_concentration_combines_call_and_put_on_same_strike():
    # One strike with both call and put OI → still a single strike, HHI 1.0
    chain = _chain(spot=100.0, calls={100.0: 200}, puts={100.0: 300})
    r = calc_oi_concentration(chain)
    assert r["n_strikes"] == 1
    assert r["hhi"] == pytest.approx(1.0)


def test_analysis_dict_includes_oi_concentration():
    chain = {
        "underlyingPrice": 5800.0,
        "callExpDateMap": {EXP: {"5800.0": [
            {"delta": 0.5, "gamma": 0.05, "openInterest": 100,
             "totalVolume": 0, "strikePrice": 5800}]}},
        "putExpDateMap": {EXP: {"5800.0": [
            {"delta": -0.5, "gamma": 0.05, "openInterest": 100,
             "totalVolume": 0, "strikePrice": 5800}]}},
    }
    gex = GammaEngine().calc_from_chain(chain)
    result = build_analysis_dict(gex, "gex", "SPX", dte=0,
                                 expected_move=40.0, chain=chain)
    assert "oi_concentration" in result
    assert result["oi_concentration"]["hhi"] == pytest.approx(1.0)


def test_explain_gex_shows_oi_concentration():
    ctx = {
        "symbol": "SPX", "spot": 5805.0, "dte": 0,
        "vix_now": None, "vix_delta": None,
        "gex_summary": {"spot": 5805.0, "flip": 5800.0,
                        "top_pos_strike": 5850.0, "top_neg_strike": 5750.0,
                        "net_total": 1.0e9},
        "oi_concentration": {"hhi": 0.22, "n_strikes": 12},
        "sentiment": {"active": False},
    }
    text = build_explain_text("gex", ctx)
    assert "OI concentration" in text
