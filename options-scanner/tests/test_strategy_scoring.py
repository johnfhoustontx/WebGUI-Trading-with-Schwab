"""
test_strategy_scoring.py - Unit B tests for the Multi-Strategy Swing Scanner.

Covers strategy_scoring.py:
  - infer_market_view (Task 8)
  - fit/quality normalizers (Task 9)
  - score_strategy / score_all (Task 10)
"""

import strategy_scoring as sc


#############################################
# Task 8 — infer_market_view
#############################################

def test_infer_view_bullish_trend_low_iv():
    tech = {"trend": "BULLISH", "rsi14": 62, "price": 455, "sma20": 450, "sma50": 445}
    iv = {"iv_rank": 20, "current_iv": 0.18, "hv_current": 0.20}
    v = sc.infer_market_view(tech, iv)
    assert v["direction"] == "bullish" and v["conviction"] > 0.4
    assert v["vol_regime"] == "low"


def test_infer_view_neutral_high_iv():
    v = sc.infer_market_view({"trend": "NEUTRAL", "rsi14": 50},
                             {"iv_rank": 75, "current_iv": 0.30, "hv_current": 0.20})
    assert v["direction"] == "neutral" and v["conviction"] < 0.4
    assert v["vol_regime"] == "high"


def test_infer_view_defensive_on_empty():
    v = sc.infer_market_view({}, {})
    assert v["direction"] == "neutral" and v["vol_regime"] == "mid"


def test_infer_view_recovering_is_bullish():
    v = sc.infer_market_view({"trend": "RECOVERING"}, {"iv_rank": 50})
    assert v["direction"] == "bullish"
    assert v["vol_regime"] == "mid"


def test_infer_view_weakening_is_bearish():
    v = sc.infer_market_view({"trend": "WEAKENING"}, {"iv_rank": None})
    assert v["direction"] == "bearish"
    assert v["vol_regime"] == "mid"  # None iv_rank -> mid


def test_infer_view_bearish_trend():
    v = sc.infer_market_view({"trend": "BEARISH", "rsi14": 38}, {"iv_rank": 40})
    assert v["direction"] == "bearish" and v["conviction"] > 0.4


def test_infer_view_conviction_clamped():
    v = sc.infer_market_view({"trend": "BULLISH", "rsi14": 95, "price": 600, "sma20": 400},
                             {"iv_rank": 10})
    assert 0.0 <= v["conviction"] <= 1.0


def test_vol_regime_from_iv_hv_when_rank_missing_high():
    v = sc.infer_market_view({"trend": "NEUTRAL"},
                             {"iv_rank": None, "current_iv": 0.30, "hv_current": 0.20})  # iv_hv=1.5
    assert v["vol_regime"] == "high"


def test_vol_regime_from_iv_hv_when_rank_missing_low():
    v = sc.infer_market_view({"trend": "NEUTRAL"},
                             {"iv_rank": None, "current_iv": 0.16, "hv_current": 0.20})  # iv_hv=0.8
    assert v["vol_regime"] == "low"


def test_vol_regime_mid_when_no_iv_signal():
    v = sc.infer_market_view({"trend": "NEUTRAL"}, {"iv_rank": None})
    assert v["vol_regime"] == "mid"


#############################################
# Task 9 — fit normalizers
#############################################

def test_fit_directional_bullish_structure_in_bull_view_high():
    v = {"direction": "bullish", "conviction": 0.8}
    assert sc.fit_directional(+0.5, v) > 70
    assert sc.fit_directional(-0.5, v) < 30


def test_fit_directional_bearish_view_rewards_bearish_structure():
    v = {"direction": "bearish", "conviction": 0.8}
    assert sc.fit_directional(-0.5, v) > 70
    assert sc.fit_directional(+0.5, v) < 30


def test_fit_directional_neutral_structure_high_only_low_conviction():
    assert sc.fit_directional(0.0, {"direction": "neutral", "conviction": 0.1}) > 60


def test_fit_directional_neutral_high_conviction_not_rewarded():
    # high conviction neutral view should NOT strongly reward a delta-neutral structure
    assert sc.fit_directional(0.0, {"direction": "neutral", "conviction": 0.9}) < 60


def test_fit_directional_clamped():
    v = {"direction": "bullish", "conviction": 1.0}
    assert 0.0 <= sc.fit_directional(+5.0, v) <= 100.0
    assert 0.0 <= sc.fit_directional(-5.0, v) <= 100.0


def test_fit_vol_long_vega_fits_low_iv():
    assert sc.fit_vol(+0.5, "low") > 70
    assert sc.fit_vol(+0.5, "high") < 40
    assert sc.fit_vol(-0.5, "high") > 70


def test_fit_vol_mid_is_neutral():
    assert 40 <= sc.fit_vol(+0.5, "mid") <= 60
    assert 40 <= sc.fit_vol(-0.5, "mid") <= 60


def test_fit_vol_clamped():
    assert 0.0 <= sc.fit_vol(+5.0, "low") <= 100.0
    assert 0.0 <= sc.fit_vol(-5.0, "high") <= 100.0


#############################################
# Task 9 — quality normalizers
#############################################

def test_q_rr_bounded():
    assert sc.q_rr({"rr": 0.0}) < 10
    assert sc.q_rr({"rr": 1.0}) > 90
    assert sc.q_rr({"rr": 2.0}) <= 100  # capped


def test_q_rr_unbounded_fallback():
    # rr is None (unbounded-profit long) -> no crash, sane value
    val = sc.q_rr({"rr": None, "pop_pct": 40})
    assert 0.0 <= val <= 100.0


def test_q_capital_eff():
    val = sc.q_capital_eff({"max_profit": 1.0, "capital": 5.0})
    assert 0.0 <= val <= 100.0
    # higher max_profit/capital scores higher
    lo = sc.q_capital_eff({"max_profit": 0.5, "capital": 5.0})
    hi = sc.q_capital_eff({"max_profit": 2.0, "capital": 5.0})
    assert hi > lo


def test_q_capital_eff_none_max_profit():
    val = sc.q_capital_eff({"max_profit": None, "capital": 5.0, "pop_pct": 35})
    assert 0.0 <= val <= 100.0


def test_q_pop_passthrough():
    assert sc.q_pop({"pop_pct": 70}) == 70
    assert sc.q_pop({"pop_pct": None}) == 50
    assert sc.q_pop({"pop_pct": 150}) == 100  # clamped


def test_q_liq_averages_legs():
    sig = {"legs": [{"bid": 5.9, "ask": 6.1, "mark": 6.0},
                    {"bid": 1.0, "ask": 1.1, "mark": 1.05}]}
    val = sc.q_liq(sig)
    assert 0.0 <= val <= 100.0


def test_q_liq_missing_bidask_is_neutral():
    sig = {"legs": [{"mark": 6.0}, {"mark": 1.0}]}
    assert sc.q_liq(sig) == 50.0


def test_q_breakeven_directional_within_em_higher():
    # directional family: breakeven within EM reach scores higher than far away
    near = sc.q_breakeven_vs_em(
        {"family": "DIRECTIONAL", "breakevens": [452.0], "underlying_price": 450.0}, em_1sd=8.0)
    far = sc.q_breakeven_vs_em(
        {"family": "DIRECTIONAL", "breakevens": [470.0], "underlying_price": 450.0}, em_1sd=8.0)
    assert near > far


def test_q_breakeven_neutral_wide_zone_higher():
    wide = sc.q_breakeven_vs_em(
        {"family": "NEUTRAL", "breakevens": [430.0, 470.0], "underlying_price": 450.0}, em_1sd=8.0)
    tight = sc.q_breakeven_vs_em(
        {"family": "NEUTRAL", "breakevens": [448.0, 452.0], "underlying_price": 450.0}, em_1sd=8.0)
    assert wide > tight


def test_q_breakeven_defensive_no_em():
    assert sc.q_breakeven_vs_em(
        {"family": "DIRECTIONAL", "breakevens": [452.0], "underlying_price": 450.0}, em_1sd=0) == 50.0


#############################################
# E1 Task 2 — gate_profile
#############################################

def test_gate_profile_maps_types():
    assert sc.gate_profile({"type": "LONG_CALL"}) == "LONG"
    assert sc.gate_profile({"type": "LONG_PUT"}) == "LONG"
    assert sc.gate_profile({"type": "SHORT_PUT"}) == "NAKED"
    assert sc.gate_profile({"type": "SHORT_CALL"}) == "NAKED"
    assert sc.gate_profile({"type": "BULL_CALL"}) == "DEBIT"
    assert sc.gate_profile({"type": "BEAR_PUT"}) == "DEBIT"
    assert sc.gate_profile({"type": "PCS"}) == "CREDIT"
    assert sc.gate_profile({"type": "CCS"}) == "CREDIT"
    assert sc.gate_profile({"type": "IRON_CONDOR"}) == "NEUTRAL"
    assert sc.gate_profile({"type": "???"}) == "DEBIT"   # safe default


def test_gate_bars_have_min_and_excellent_levels():
    for profile in ("LONG", "NAKED", "DEBIT", "CREDIT", "NEUTRAL"):
        bars = sc.GATE_BARS[profile]
        assert "min" in bars and "excellent" in bars
        assert set(bars["min"]) == set(bars["excellent"])


#############################################
# E1 Task 3 — evaluate_gates
#############################################

def _credit(rr=0.3, pop=70, legs=None):
    return {"type": "PCS", "family": "VERTICAL", "rr": rr, "pop_pct": pop,
            "max_profit": 1.7, "capital": 3.3, "underlying_price": 450,
            "legs": legs if legs is not None else
                    [{"bid": 1.0, "ask": 1.008, "mark": 1.004, "volume": 500, "oi": 1000}]}


def test_gates_credit_passes_min():
    g = sc.evaluate_gates(_credit(rr=0.3, pop=70))
    assert g["passed_min"] and not g["reasons"]


def test_gates_credit_fails_low_pop():
    g = sc.evaluate_gates(_credit(pop=40))
    assert not g["passed_min"] and "PoP" in " ".join(g["reasons"])


def test_gates_long_unbounded_profit_passes_reward():
    g = sc.evaluate_gates({"type": "LONG_CALL", "rr": None, "net_debit": 6.0, "pop_pct": 35,
        "max_profit": None, "capital": 6.0,
        "legs": [{"bid": 5.9, "ask": 6.0, "mark": 5.95, "volume": 800, "oi": 2000}]})
    assert g["passed_min"]


def test_gates_naked_low_capital_efficiency_fails_reward():
    g = sc.evaluate_gates({"type": "SHORT_CALL", "rr": None, "net_credit": 3.5, "pop_pct": 70,
        "max_profit": 3.5, "capital": 90.0,
        "legs": [{"bid": 3.4, "ask": 3.6, "mark": 3.5, "volume": 300, "oi": 800}]})
    assert not g["passed_min"] and "R:R" in " ".join(g["reasons"])


def test_gates_fail_illiquid():
    g = sc.evaluate_gates(_credit(legs=[{"bid": 1.0, "ask": 1.9, "mark": 1.4, "volume": 1, "oi": 5}]))
    assert not g["passed_min"] and "liquidity" in " ".join(g["reasons"])


def test_gates_excellent_flag():
    g = sc.evaluate_gates(_credit(rr=0.4, pop=75,
        legs=[{"bid": 1.0, "ask": 1.01, "mark": 1.005, "volume": 900, "oi": 5000}]))
    assert g["passed_excellent"]


def test_gates_missing_liquidity_fields_not_false_fail():
    # legs lack oi/volume entirely -> those floors are SKIPPED, not failed
    g = sc.evaluate_gates(_credit(rr=0.3, pop=70,
        legs=[{"bid": 1.0, "ask": 1.02, "mark": 1.01}]))
    assert g["passed_min"] and "liquidity" not in " ".join(g["reasons"])


def test_gates_debit_missing_rr_fails():
    g = sc.evaluate_gates({"type": "BULL_CALL", "rr": None, "pop_pct": 40,
        "legs": [{"bid": 1.0, "ask": 1.02, "mark": 1.01, "volume": 500, "oi": 1000}]})
    assert not g["passed_min"] and "R:R" in " ".join(g["reasons"])


#############################################
# Task 10 — score_strategy / score_all
#############################################

def _long_call_sig():
    return {"type": "LONG_CALL", "family": "DIRECTIONAL", "bias": "bullish",
            "net_delta": 0.55, "net_vega": 0.40, "net_theta": -0.03, "pop_pct": 35,
            "rr": None, "max_profit": None, "max_loss": 6.0, "capital": 6.0,
            "breakevens": [456.0], "underlying_price": 450.0,
            "legs": [{"bid": 5.9, "ask": 6.1, "mark": 6.0}]}


def test_score_strategy_adds_composite_and_grade():
    out = sc.score_strategy(_long_call_sig(),
                            {"direction": "bullish", "conviction": 0.8, "vol_regime": "low"},
                            atm_iv=0.18, em_1sd=8.0)
    assert 0 <= out["composite_score"] <= 100
    assert out["grade"] in ("Strong", "Good", "Marginal", "Weak")
    assert "fit_dir" in out["factor_scores"] and "q_liq" in out["factor_scores"]


def test_score_strategy_has_all_factors():
    out = sc.score_strategy(_long_call_sig(),
                            {"direction": "bullish", "conviction": 0.8, "vol_regime": "low"},
                            atm_iv=0.18, em_1sd=8.0)
    for k in ("fit_dir", "fit_vol", "q_liq", "q_rr", "q_be", "q_pop"):
        assert k in out["factor_scores"]
    assert "fit_score" in out and "quality_score" in out


def test_score_strategy_better_in_aligned_view():
    bull = sc.score_strategy(_long_call_sig(),
                             {"direction": "bullish", "conviction": 0.8, "vol_regime": "low"}, 0.18, 8.0)
    bear = sc.score_strategy(_long_call_sig(),
                             {"direction": "bearish", "conviction": 0.8, "vol_regime": "low"}, 0.18, 8.0)
    assert bull["composite_score"] > bear["composite_score"]


def test_score_strategy_defensive_on_bad_signal():
    # a garbage signal should not raise; gets a neutral/0 composite
    out = sc.score_strategy({"net_delta": None}, {"direction": "bullish", "conviction": 0.8,
                            "vol_regime": "low"}, 0.18, 8.0)
    assert 0 <= out["composite_score"] <= 100


def test_score_strategy_defensive_on_non_dict():
    # a non-dict signal must NOT raise (the except handler can't assume dict-ness)
    for bad in (None, "x", 42):
        out = sc.score_strategy(bad, {"direction": "bullish", "conviction": 0.8,
                                "vol_regime": "low"}, 0.18, 8.0)
        assert out["composite_score"] == 0.0
        assert out["grade"] == "Weak"


def test_score_all_sorts_desc():
    a = _long_call_sig()
    b = _long_call_sig()
    b["net_delta"] = -0.55  # bearish structure
    view = {"direction": "bullish", "conviction": 0.8, "vol_regime": "low"}
    out = sc.score_all([b, a], view, 0.18, 8.0)
    assert out[0]["composite_score"] >= out[1]["composite_score"]


def test_score_all_handles_bad_signal():
    good = _long_call_sig()
    bad = {"net_delta": None, "legs": "not-a-list"}
    view = {"direction": "bullish", "conviction": 0.8, "vol_regime": "low"}
    out = sc.score_all([good, bad], view, 0.18, 8.0)
    assert len(out) == 2
    for s in out:
        assert 0 <= s["composite_score"] <= 100
