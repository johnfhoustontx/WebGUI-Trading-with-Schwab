"""Tests for chain-native put/call OI and volume ratios."""
import pytest

from gamma_tool import calc_pc_ratios, build_analysis_dict, GammaEngine

EXP = "2099-12-31:5"


def _chain(spot, calls, puts):
    """calls/puts: {strike: (oi, volume)}."""
    def _side(d):
        return {EXP: {f"{k:.1f}": [{"openInterest": oi, "totalVolume": vol}]
                      for k, (oi, vol) in d.items()}}
    return {
        "underlyingPrice": spot,
        "callExpDateMap": _side(calls),
        "putExpDateMap": _side(puts),
    }


def test_pc_ratios_none_on_empty():
    assert calc_pc_ratios(None) == {"pc_oi": None, "pc_volume": None}
    assert calc_pc_ratios({}) == {"pc_oi": None, "pc_volume": None}


def test_pc_ratios_basic():
    chain = _chain(
        spot=100.0,
        calls={100.0: (100, 200)},
        puts={100.0: (300, 50)},
    )
    r = calc_pc_ratios(chain)
    assert r["pc_oi"] == pytest.approx(3.0)
    assert r["pc_volume"] == pytest.approx(0.25)


def test_pc_ratios_zero_call_denominator_is_none():
    chain = _chain(spot=100.0, calls={100.0: (0, 0)}, puts={100.0: (300, 50)})
    r = calc_pc_ratios(chain)
    assert r["pc_oi"] is None
    assert r["pc_volume"] is None


def test_analysis_dict_includes_pc_ratios():
    chain = {
        "underlyingPrice": 5800.0,
        "callExpDateMap": {EXP: {"5800.0": [
            {"delta": 0.5, "gamma": 0.05, "openInterest": 100,
             "totalVolume": 200, "strikePrice": 5800}]}},
        "putExpDateMap": {EXP: {"5800.0": [
            {"delta": -0.5, "gamma": 0.05, "openInterest": 300,
             "totalVolume": 50, "strikePrice": 5800}]}},
    }
    gex = GammaEngine().calc_from_chain(chain)
    result = build_analysis_dict(gex, "gex", "SPX", dte=0,
                                 expected_move=40.0, chain=chain)
    assert "pc_ratios" in result
    assert result["pc_ratios"]["pc_oi"] == pytest.approx(3.0)
