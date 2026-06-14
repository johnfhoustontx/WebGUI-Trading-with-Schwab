"""Tests for build_eod_probabilities — touch-probability approximation
using |delta| × 2."""
from gamma_tool import build_eod_probabilities


def _stub_chain(strikes_with_delta):
    """Build a minimal chain where each strike has a known call & put delta."""
    call_map = {"2099-12-31:1": {}}
    put_map = {"2099-12-31:1": {}}
    for strike, (call_delta, put_delta) in strikes_with_delta.items():
        call_map["2099-12-31:1"][f"{strike}.0"] = [
            {"delta": call_delta, "strikePrice": strike}]
        put_map["2099-12-31:1"][f"{strike}.0"] = [
            {"delta": put_delta, "strikePrice": strike}]
    return {"callExpDateMap": call_map, "putExpDateMap": put_map,
            "underlyingPrice": 5800.0}


def test_touch_em_upper_uses_call_delta():
    """For an upside target, use the OTM call's delta."""
    chain = _stub_chain({5840: (0.21, -0.79)})
    probs = build_eod_probabilities(
        chain,
        em_upper=5840.0, em_lower=5760.0,
        pos_wall_strike=5840.0, neg_wall_strike=5760.0)
    assert abs(probs["touch_em_upper"] - 0.42) < 0.001


def test_touch_em_lower_uses_put_delta():
    """For a downside target, use the OTM put's |delta|."""
    chain = _stub_chain({5760: (0.81, -0.19)})
    probs = build_eod_probabilities(
        chain,
        em_upper=5840.0, em_lower=5760.0,
        pos_wall_strike=5840.0, neg_wall_strike=5760.0)
    assert abs(probs["touch_em_lower"] - 0.38) < 0.001  # 2 * 0.19


def test_clamps_at_one():
    """Touch probability is capped at 100%."""
    chain = _stub_chain({5800: (0.60, -0.40)})
    probs = build_eod_probabilities(
        chain,
        em_upper=5800.0, em_lower=5700.0,
        pos_wall_strike=5800.0, neg_wall_strike=5700.0)
    assert probs["touch_em_upper"] == 1.0


def test_missing_strike_returns_none_for_that_prob():
    """If the chain has no contract at the target, return None for that key."""
    chain = _stub_chain({5840: (0.21, -0.79)})
    probs = build_eod_probabilities(
        chain,
        em_upper=5840.0, em_lower=5760.0,
        pos_wall_strike=5840.0, neg_wall_strike=5760.0)
    assert probs["touch_em_upper"] is not None
    assert probs["touch_em_lower"] is None


def test_all_four_keys_present():
    chain = _stub_chain({5840: (0.21, -0.79), 5760: (0.80, -0.20)})
    probs = build_eod_probabilities(
        chain,
        em_upper=5840.0, em_lower=5760.0,
        pos_wall_strike=5840.0, neg_wall_strike=5760.0)
    assert set(probs.keys()) == {
        "touch_em_upper", "touch_em_lower",
        "reach_pos_wall", "reach_neg_wall"}


def test_build_analysis_dict_includes_eod_probabilities():
    """When chain is passed, build_analysis_dict should embed eod_probabilities."""
    from gamma_tool import build_analysis_dict, GammaEngine
    # Build a chain rich enough for calc_from_chain to produce output
    # AND with delta values at the wall + EM-bound strikes.
    chain = {
        "underlyingPrice": 5800.0,
        "callExpDateMap": {"2099-12-31:1": {
            "5800.0": [{"delta": 0.5, "gamma": 0.05, "openInterest": 1000,
                        "totalVolume": 0, "strikePrice": 5800}],
            "5840.0": [{"delta": 0.21, "gamma": 0.03, "openInterest": 1000,
                        "totalVolume": 0, "strikePrice": 5840}],
        }},
        "putExpDateMap": {"2099-12-31:1": {
            "5800.0": [{"delta": -0.5, "gamma": 0.05, "openInterest": 1000,
                        "totalVolume": 0, "strikePrice": 5800}],
            "5760.0": [{"delta": -0.19, "gamma": 0.03, "openInterest": 1000,
                        "totalVolume": 0, "strikePrice": 5760}],
        }},
    }
    engine = GammaEngine()
    gex = engine.calc_from_chain(chain)
    result = build_analysis_dict(gex, "gex", "SPX", dte=0,
                                 expected_move=40.0, chain=chain)
    assert "eod_probabilities" in result
    eod = result["eod_probabilities"]
    assert eod is not None
    assert set(eod.keys()) == {
        "touch_em_upper", "touch_em_lower",
        "reach_pos_wall", "reach_neg_wall"}


def test_build_analysis_dict_eod_probs_none_when_chain_not_provided():
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
    engine = GammaEngine()
    gex = engine.calc_from_chain(chain)
    # No chain= kwarg.
    result = build_analysis_dict(gex, "gex", "SPX", dte=0, expected_move=40.0)
    assert result["eod_probabilities"] is None
