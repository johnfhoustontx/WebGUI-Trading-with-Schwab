"""Tests for the Dealer Pinch detector (pure)."""
import pytest

from dealer_pinch import (
    dominant_oi_node,
    classify_pinch_regime,
    build_pinch_playbook,
    evaluate_dealer_pinch,
)

EXP = "2099-12-31:3"


def _chain(spot, calls, puts):
    """calls/puts: {strike: open_interest}."""
    def _side(d):
        return {EXP: {f"{k:.1f}": [{"openInterest": oi}] for k, oi in d.items()}}
    return {
        "underlyingPrice": spot,
        "callExpDateMap": _side(calls),
        "putExpDateMap": _side(puts),
    }


# ── Task 1: dominant_oi_node ──

def test_dominant_node_picks_highest_combined_oi():
    chain = _chain(
        spot=5805.0,
        calls={5800.0: 4000, 5850.0: 500},
        puts={5800.0: 3000, 5750.0: 1000},
    )
    res = dominant_oi_node(chain)
    assert res["node"] == 5800.0           # 4000+3000 combined dominates
    assert res["secondary"] == 5750.0      # next-largest combined (1000)
    assert 0 < res["dominance"] <= 1
    assert res["dominance"] == pytest.approx(7000 / 8500)


def test_dominant_node_empty_chain():
    res = dominant_oi_node({})
    assert res == {"node": None, "secondary": None, "dominance": 0.0}


# ── Task 3: classify_pinch_regime ──

def test_regime_pin_when_armed_and_inside_band():
    r = classify_pinch_regime(armed=True, spot=5802.0, node=5800.0,
                              secondary=5750.0, gex_flip=5790.0)
    assert r["regime"] == "PIN"
    assert r["levels"]["pin_target"] == 5800.0


def test_regime_break_when_spot_beyond_trigger():
    # Spot well beyond the node + buffer -> BREAK toward secondary.
    r = classify_pinch_regime(armed=True, spot=5760.0, node=5800.0,
                              secondary=5750.0, gex_flip=5790.0)
    assert r["regime"] == "BREAK"
    assert r["levels"]["pin_target"] == 5800.0
    assert r["levels"]["break_trigger"] is not None


def test_regime_watching_when_not_armed():
    r = classify_pinch_regime(armed=False, spot=5802.0, node=5800.0,
                              secondary=5750.0, gex_flip=5790.0)
    assert r["regime"] == "WATCHING"


def test_playbook_text_matches_regime():
    assert "fade" in build_pinch_playbook("PIN").lower() or \
           "premium" in build_pinch_playbook("PIN").lower()
    assert "momentum" in build_pinch_playbook("BREAK").lower() or \
           "squeeze" in build_pinch_playbook("BREAK").lower()
    assert "watch" in build_pinch_playbook("WATCHING").lower()


# ── Task 4: evaluate_dealer_pinch ──

def _armed_chain():
    # spot on the dominant node, near expiry.
    return _chain(spot=5801.0,
                  calls={5800.0: 5000, 5850.0: 400},
                  puts={5800.0: 4000, 5750.0: 800})


def test_evaluate_armed_all_conditions():
    state = evaluate_dealer_pinch(
        symbol="$SPX", chain=_armed_chain(), spot=5801.0, dte=2,
        iv_pctile=85.0, rv_trend={"value": 11.0, "falling": True},
        gex_flip=5790.0, pin_risk_score=0.8, forced_hedge_dir="down",
    )
    assert state["armed"] is True
    assert state["regime"] == "PIN"
    c = state["conditions"]
    assert c["c1"] and c["c2"] and c["c3a"] and c["c3b"]
    assert state["node"]["strike"] == 5800.0
    assert state["levels"]["pin_target"] == 5800.0
    assert 0 <= state["confidence"] <= 100


def test_evaluate_three_of_four_not_armed():
    # IV not elevated -> c3a fails -> not armed, WATCHING, miss visible.
    state = evaluate_dealer_pinch(
        symbol="$SPX", chain=_armed_chain(), spot=5801.0, dte=2,
        iv_pctile=50.0, rv_trend={"value": 11.0, "falling": True},
        gex_flip=5790.0, pin_risk_score=0.8, forced_hedge_dir="down",
    )
    assert state["armed"] is False
    assert state["conditions"]["c3a"] is False
    assert state["regime"] == "WATCHING"


def test_evaluate_missing_iv_is_na_not_error():
    state = evaluate_dealer_pinch(
        symbol="$SPX", chain=_armed_chain(), spot=5801.0, dte=2,
        iv_pctile=None, rv_trend=None,
        gex_flip=5790.0, pin_risk_score=0.8, forced_hedge_dir=None,
    )
    assert state["armed"] is False
    assert state["conditions"]["c3a"] is None
    assert state["conditions"]["c3b"] is None
    assert state["regime"] == "WATCHING"


def test_evaluate_confidence_monotonic_in_pin_risk():
    def conf(pr):
        return evaluate_dealer_pinch(
            symbol="$SPX", chain=_armed_chain(), spot=5801.0, dte=2,
            iv_pctile=85.0, rv_trend={"value": 11.0, "falling": True},
            gex_flip=5790.0, pin_risk_score=pr, forced_hedge_dir="down",
        )["confidence"]
    assert conf(0.9) > conf(0.3)


def test_evaluate_dte_too_far_fails_c1():
    state = evaluate_dealer_pinch(
        symbol="$SPX", chain=_armed_chain(), spot=5801.0, dte=9,
        iv_pctile=85.0, rv_trend={"value": 11.0, "falling": True},
        gex_flip=5790.0, pin_risk_score=0.8, forced_hedge_dir="down",
    )
    assert state["conditions"]["c1"] is False
    assert state["armed"] is False


# ── build_analysis_dict wiring ──

def test_build_analysis_dict_populates_dealer_pinch():
    from gamma_tool import build_analysis_dict, GammaEngine
    chain = {
        "underlyingPrice": 5800.0,
        "callExpDateMap": {"2099-12-31:2": {
            "5800.0": [{"delta": 0.5, "gamma": 0.05, "openInterest": 5000,
                        "totalVolume": 0, "strikePrice": 5800}],
        }},
        "putExpDateMap": {"2099-12-31:2": {
            "5800.0": [{"delta": -0.5, "gamma": 0.05, "openInterest": 4000,
                        "totalVolume": 0, "strikePrice": 5800}],
        }},
    }
    gex = GammaEngine().calc_from_chain(chain)
    result = build_analysis_dict(
        gex, "gex", "$SPX", dte=2, expected_move=40.0, chain=chain,
        iv_pctile=85.0, rv_trend={"value": 11.0, "falling": True})
    assert "dealer_pinch" in result
    p = result["dealer_pinch"]
    assert p is not None
    assert p["node"]["strike"] == 5800.0
    assert p["regime"] in ("PIN", "BREAK", "WATCHING")


def test_build_analysis_dict_pinch_none_without_chain():
    from gamma_tool import build_analysis_dict, GammaEngine
    chain = {
        "underlyingPrice": 5800.0,
        "callExpDateMap": {"2099-12-31:2": {"5800.0": [
            {"delta": 0.5, "gamma": 0.05, "openInterest": 100,
             "totalVolume": 0, "strikePrice": 5800}]}},
        "putExpDateMap": {"2099-12-31:2": {"5800.0": [
            {"delta": -0.5, "gamma": 0.05, "openInterest": 100,
             "totalVolume": 0, "strikePrice": 5800}]}},
    }
    gex = GammaEngine().calc_from_chain(chain)
    result = build_analysis_dict(gex, "gex", "$SPX", dte=2, expected_move=40.0)
    assert result["dealer_pinch"] is None


# ── pinch status-flag formatter (pure, gamma_tool) ──

def test_pinch_flag_text_armed():
    from gamma_tool import pinch_flag_text
    state = {
        "armed": True, "regime": "PIN", "confidence": 72.0,
        "node": {"strike": 5800.0},
        "conditions": {"c1": True, "c2": True, "c3a": True, "c3b": True},
    }
    text, _color = pinch_flag_text(state)
    assert "PIN" in text and "5,800" in text and "72%" in text


def test_pinch_flag_text_watching_counts():
    from gamma_tool import pinch_flag_text
    state = {
        "armed": False, "regime": "WATCHING", "confidence": 0.0,
        "node": {"strike": 5800.0},
        "conditions": {"c1": True, "c2": True, "c3a": False, "c3b": None},
    }
    text, _color = pinch_flag_text(state)
    assert "watching" in text.lower() and "2/4" in text


def test_pinch_flag_text_none():
    from gamma_tool import pinch_flag_text
    assert pinch_flag_text(None) == ("", None)
