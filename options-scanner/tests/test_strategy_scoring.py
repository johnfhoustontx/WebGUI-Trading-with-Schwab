"""
test_strategy_scoring.py - Unit B tests for the Multi-Strategy Swing Scanner.

Covers strategy_scoring.py:
  - infer_market_view (Task 8)
  - fit/quality normalizers (Task 9)
  - score_strategy / score_all (Task 10)
"""

import strategy_scoring as sc
from strategy_scoring import state_family_tilt, STATE_TILT_MAX


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
    # A real long call ALWAYS carries a net_debit (payoff_metrics sets it), which
    # triggers the unbounded-profit reward auto-pass; legs are liquid + pop >= 30
    # so this exercises the real GATE-PASSING path, not a cap artifact.
    return {"type": "LONG_CALL", "family": "DIRECTIONAL", "bias": "bullish",
            "net_delta": 0.55, "net_vega": 0.40, "net_theta": -0.03, "pop_pct": 35,
            "rr": None, "net_debit": 6.0, "max_profit": None, "max_loss": 6.0,
            "capital": 6.0, "breakevens": [456.0], "underlying_price": 450.0,
            "legs": [{"bid": 5.98, "ask": 6.02, "mark": 6.0,
                      "volume": 800, "oi": 2000}]}


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
    # both PASS the gates (a real long call: net_debit -> reward auto-pass, liquid,
    # pop >= 30) so neither is capped at 39 -> the comparison is meaningful, not a
    # cap artifact.
    assert bull["grade"] != "Weak" and bear["grade"] != "Weak"
    assert bull["composite_score"] > bear["composite_score"]
    assert bull["composite_score"] > 39 and bear["composite_score"] > 39


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


# ---- E1 Task 4: quality-dominant, gated grade ----
def test_score_strategy_gate_fail_weak_with_reason():
    out = sc.score_strategy(_credit(pop=40),
                            {"direction": "bullish", "conviction": 0.8, "vol_regime": "low"}, 0.18, 8.0)
    assert out["grade"] == "Weak" and "PoP" in out["grade_reason"] and out["composite_score"] <= 39


def test_score_strategy_passes_gates_not_weak():
    out = sc.score_strategy(_credit(rr=0.3, pop=72),
                            {"direction": "bullish", "conviction": 0.8, "vol_regime": "high"}, 0.18, 8.0)
    assert out["grade"] != "Weak" and "grade_reason" in out


def test_score_strategy_quality_dominant_fit_cannot_force_weak():
    # a structurally-fine bullish PCS scored under a bearish view: gates pass -> not Weak
    out = sc.score_strategy(_credit(rr=0.35, pop=75),
                            {"direction": "bearish", "conviction": 0.9, "vol_regime": "high"}, 0.18, 8.0)
    assert out["grade"] != "Weak"


def test_score_strategy_defensive_signal_has_grade_reason():
    out = sc.score_strategy({"net_delta": None},
                            {"direction": "bullish", "conviction": 0.8, "vol_regime": "low"}, 0.18, 8.0)
    assert "grade_reason" in out


def test_score_strategy_excellent_earns_strong():
    # A CREDIT PCS clearing the EXCELLENT bars (liq/rr/pop) with strong fit
    # (bullish PCS in a high-conviction bull view, short-vega in high IV) reaches
    # composite >= STRONG_MIN -> Strong. Guards that the top band is reachable.
    sig = {"type": "PCS", "family": "VERTICAL", "net_delta": 0.30, "net_vega": -0.30,
           "pop_pct": 80, "rr": 0.9, "max_profit": 1.7, "capital": 1.9,
           "underlying_price": 450, "breakevens": [448.0],
           "legs": [{"bid": 1.00, "ask": 1.005, "mark": 1.002, "volume": 900, "oi": 5000}]}
    out = sc.score_strategy(sig, {"direction": "bullish", "conviction": 0.9,
                                  "vol_regime": "high"}, 0.18, 8.0)
    assert out["composite_score"] >= sc.STRONG_MIN
    assert out["grade"] == "Strong"
    assert out["grade_reason"] == "Excellent on all quality gates"


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


#############################################
# Task 1 — market-state family tilt
#############################################

def test_tilt_bounded():
    for st in ("bullish", "lack_of_bullishness", "neutral", "lack_of_bearishness", "bearish"):
        for t in ("PCS", "CCS", "IC", "LONG_CALL", "LONG_PUT", "BULL_CALL", "BEAR_PUT"):
            assert -STATE_TILT_MAX <= state_family_tilt(st, t) <= STATE_TILT_MAX


def test_middle_states_lean_correctly():
    assert state_family_tilt("lack_of_bearishness", "PCS") > 0
    assert state_family_tilt("lack_of_bullishness", "CCS") > 0
    assert state_family_tilt("lack_of_bullishness", "LONG_CALL") < 0
    assert state_family_tilt("neutral", "IC") > 0


def test_unknown_no_tilt():
    assert state_family_tilt(None, "PCS") == 0.0
    assert state_family_tilt("garbage", "PCS") == 0.0
    assert state_family_tilt("neutral", "UNKNOWN") == 0.0


def test_score_strategy_market_state_tilts_composite():
    # a PCS scored under lack_of_bearishness gets a positive, bounded ranking nudge.
    base = sc.score_strategy(_credit(rr=0.3, pop=72),
                             {"direction": "bullish", "conviction": 0.8, "vol_regime": "high"},
                             0.18, 8.0)
    tilted = sc.score_strategy(_credit(rr=0.3, pop=72),
                               {"direction": "bullish", "conviction": 0.8, "vol_regime": "high"},
                               0.18, 8.0, market_state="lack_of_bearishness")
    assert tilted["composite_score"] > base["composite_score"]
    assert tilted["composite_score"] - base["composite_score"] <= STATE_TILT_MAX + 0.01
    assert tilted["state_tilt"] > 0
    assert base["state_tilt"] == 0.0


def test_score_strategy_tilt_cannot_flip_hard_gate():
    # a hard-gated Weak trade (fails PoP) stays Weak even with a positive tilt.
    out = sc.score_strategy(_credit(pop=40),
                            {"direction": "bullish", "conviction": 0.8, "vol_regime": "low"},
                            0.18, 8.0, market_state="lack_of_bearishness")
    assert out["grade"] == "Weak"
    assert "PoP" in out["grade_reason"]
    assert out["state_tilt"] > 0  # tilt applied
    # composite nudged but grade unchanged (tilt is a ranking-only nudge)


def test_score_all_threads_market_state():
    a = _credit(rr=0.3, pop=72)
    view = {"direction": "bullish", "conviction": 0.8, "vol_regime": "high"}
    plain = sc.score_all([_credit(rr=0.3, pop=72)], view, 0.18, 8.0)
    tilted = sc.score_all([a], view, 0.18, 8.0, market_state="lack_of_bearishness")
    assert tilted[0]["composite_score"] > plain[0]["composite_score"]


#############################################
# Tick-aware liquidity — real-market regression
#############################################

# LIVE RTH quotes captured 2026-07-15 12:14 CT (market open) from the running
# options service. These are real, unambiguously fillable markets on some of the
# most liquid equity/ETF options that trade. The index-calibrated
# percent-of-mark band (100 at <=1%, hard 0 at >=5%) scored EVERY one of them
# 0.0, which tripped the hard liquidity gate and forced every candidate to Weak.
# (bid, ask, mark, volume, oi)
_AAPL_325C = {"bid": 4.50, "ask": 4.75, "mark": 4.63, "volume": 3144, "oi": 633}
_AAPL_332_5C = {"bid": 1.50, "ask": 1.61, "mark": 1.56, "volume": 1165, "oi": 92}
_MSFT_392_5C = {"bid": 7.55, "ask": 7.95, "mark": 7.75, "volume": 234, "oi": 473}
_MSFT_405C = {"bid": 2.64, "ask": 2.77, "mark": 2.71, "volume": 546, "oi": 228}
_MSFT_395C = {"bid": 6.25, "ask": 6.60, "mark": 6.43, "volume": 1242, "oi": 488}
_IWM_294C = {"bid": 2.99, "ask": 3.10, "mark": 3.05, "volume": 76, "oi": 426}
_IWM_298C = {"bid": 0.92, "ask": 0.97, "mark": 0.95, "volume": 216, "oi": 406}
# SPY — penny-wide index market; the band was calibrated on these. Must not regress.
_SPY_750C = {"bid": 4.84, "ask": 4.93, "mark": 4.89, "volume": 2319, "oi": 1902}
_SPY_756C = {"bid": 1.66, "ask": 1.68, "mark": 1.67, "volume": 1991, "oi": 1665}
# ZM — a GENUINELY wide market ($0.60 wide on a $2.24 mark = 27%). Must still fail.
_ZM_91C = {"bid": 1.94, "ask": 2.54, "mark": 2.24, "volume": 179, "oi": 290}
_ZM_94C = {"bid": 0.60, "ask": 1.00, "mark": 0.80, "volume": 34, "oi": 213}


def _sig(legs, **kw):
    base = {"type": "BULL_CALL", "legs": legs, "pop_pct": 45.0, "rr": 1.0}
    base.update(kw)
    return base


def test_q_liq_real_liquid_equity_legs_are_not_scored_zero():
    """A real, fillable AAPL/MSFT/IWM market must not score 0 liquidity."""
    for leg in (_AAPL_325C, _AAPL_332_5C, _MSFT_392_5C, _MSFT_405C,
                _MSFT_395C, _IWM_294C, _IWM_298C):
        assert sc.q_liq(_sig([leg])) > 0.0, leg


def test_q_liq_real_liquid_equity_structures_clear_the_min_gate_bar():
    """These markets must clear the DEBIT/LONG min liquidity bar (45/40)."""
    assert sc.q_liq(_sig([_AAPL_325C, _AAPL_332_5C])) >= 45   # AAPL bull call
    assert sc.q_liq(_sig([_MSFT_392_5C, _MSFT_405C])) >= 45   # MSFT bull call
    assert sc.q_liq(_sig([_IWM_294C, _IWM_298C])) >= 45       # IWM bull call
    assert sc.q_liq(_sig([_MSFT_395C])) >= 40                 # MSFT long call


def test_liquidity_gate_passes_for_real_liquid_equity_structures():
    """The hard gate must not report 'liquidity' for genuinely liquid markets."""
    for legs in ([_AAPL_325C, _AAPL_332_5C],
                 [_MSFT_392_5C, _MSFT_405C],
                 [_IWM_294C, _IWM_298C]):
        reasons = sc.evaluate_gates(_sig(legs))["reasons"]
        assert "liquidity" not in reasons, (legs, reasons)


def test_genuinely_wide_market_still_fails_the_liquidity_gate():
    """Discrimination: a 27%-wide ZM market is NOT fillable and must still fail."""
    reasons = sc.evaluate_gates(_sig([_ZM_91C, _ZM_94C]))["reasons"]
    assert "liquidity" in reasons


def test_one_tick_wide_market_scores_top_regardless_of_cheap_mark():
    """A market cannot be tighter than the tick; a cheap option must not be
    penalized for the tick floor eating a large % of its premium."""
    penny_wide_cheap = {"bid": 0.09, "ask": 0.10, "mark": 0.095}   # 1 tick = 10.5% of mark
    assert sc.q_liq(_sig([penny_wide_cheap])) >= 95


def test_index_penny_markets_do_not_regress():
    """SPY (what the band was calibrated on) must stay top-scored."""
    assert sc.q_liq(_sig([_SPY_750C, _SPY_756C])) >= 80


#############################################
# Leg-dilution asymmetry — unknowns must not be averaged into knowns
#############################################

# The credit/IC adapters deliberately carry liquidity ONLY on the SHORT legs
# ("do NOT fabricate"), so a protective long wing arrives with no bid/ask and
# scores a NEUTRAL 50. Averaging that placeholder into the real measurement
# compresses every credit structure into [25, 75]: a perfect market scores 75
# and an unfillable one is floored at 25. Since the CREDIT/NEUTRAL `excellent`
# liq bar IS 75, that made Strong effectively unreachable for those families.
# Live SPY CCS captured 2026-07-15: short 776C 2.50/2.52 -> 100, long 778C wing
# with no quotes -> 50, q_liq reported 75.0.
_SPY_CCS_SHORT = {"bid": 2.50, "ask": 2.52, "mark": 2.51, "volume": 155, "oi": 500}
_SPY_CCS_LONG_WING = {"mark": 2.09}            # protective wing — no bid/ask
_WIDE_SHORT = {"bid": 1.94, "ask": 2.54, "mark": 2.24, "volume": 179, "oi": 290}


def test_q_liq_does_not_dilute_a_real_measurement_with_the_neutral_placeholder():
    """A credit spread's real short-leg market must not be averaged with the
    unknown wing's neutral 50."""
    val = sc.q_liq(_sig([_SPY_CCS_SHORT, _SPY_CCS_LONG_WING], type="CCS"))
    assert val >= 95, val          # was 75.0 = avg(100, 50)


def test_credit_spread_can_reach_the_excellent_liquidity_bar():
    """Dilution capped a 2-leg credit spread at 75, so the excellent bar (75)
    demanded a literally perfect short-leg market. A real, good AAPL-grade
    market must be able to clear it."""
    sig = _sig([_AAPL_325C, _SPY_CCS_LONG_WING], type="PCS", rr=0.4, pop_pct=75.0)
    assert sc.evaluate_gates(sig)["passed_excellent"] is True


def test_unfillable_credit_spread_is_not_floored_by_the_placeholder():
    """A 27%-wide short leg must score near 0, not be lifted to 25 by the wing."""
    val = sc.q_liq(_sig([_WIDE_SHORT, _SPY_CCS_LONG_WING], type="CCS"))
    assert val < 10, val           # was 25.0 = avg(0, 50)
