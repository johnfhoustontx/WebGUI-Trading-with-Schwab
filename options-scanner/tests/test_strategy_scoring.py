"""
test_strategy_scoring.py - Unit B tests for the Multi-Strategy Swing Scanner.

Covers strategy_scoring.py:
  - infer_market_view (Task 8)
  - fit/quality normalizers (Task 9)
  - score_strategy / score_all (Task 10)
"""

import datetime as _dt

import strategy_scanner as ss
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


# The NAKED reward bar is ANNUALISED (Task 2.6), so every naked-short fixture
# below carries a ``dte`` -- without one the metric is None and the gate fails
# for want of a horizon rather than for the reason the test names.
def _naked(stype="SHORT_PUT", max_profit=160.70, capital=9439.0, dte=35, pop=74.1):
    """A 35-DTE cash-secured put off a Black-Scholes chain (spot 100, IV 0.28).

    1.70% over 35 days == 17.8%/yr: an ordinary CSP, and the exact trade that
    was graded Weak-and-cut while the bar was per-trade.
    """
    return {"type": stype, "rr": None, "max_profit": max_profit, "capital": capital,
            "dte": dte, "pop_pct": pop,
            "legs": [{"bid": 1.60, "ask": 1.64, "mark": 1.62, "volume": 500, "oi": 2000}]}


def test_reward_metric_naked_is_return_on_capital_per_year():
    # 160.70 / 9439 = 1.702% over 35 days -> x365/35 = 17.75%/yr.
    assert abs(sc._reward_metric(_naked(), "NAKED") - 0.1775) < 0.001


def test_gates_naked_healthy_cash_secured_put_passes_min():
    """A 17.8%/yr CSP clears the 10%/yr bar. Was Weak-and-cut when the bar was
    10% PER TRADE regardless of horizon -- the Task 2.6 bug."""
    g = sc.evaluate_gates(_naked())
    assert g["passed_min"] and not g["reasons"]


def test_gates_naked_healthy_short_call_passes_min():
    # Same chain, 35 DTE: 143.70 / 2001 = 7.18% over 35 days = 74.9%/yr.
    g = sc.evaluate_gates(_naked("SHORT_CALL", max_profit=143.70, capital=2001.0, pop=80.4))
    assert g["passed_min"] and not g["reasons"]


def test_gates_naked_zero_dte_cannot_be_annualised_and_fails():
    """dte <= 0 must not divide to infinity and sail through: an expired or
    same-day horizon is unjudgeable, and unjudgeable means do not pass."""
    for dte in (0, -1):
        # Assert the METRIC, not just the gate: this fixture's per-trade capeff
        # (1.7%) is under the bar anyway, so a gate-only assertion would stay
        # green against a `dte or 1` style fallback that silently annualises a
        # 0-DTE signal by 365x.
        assert sc._reward_metric(_naked(dte=dte), "NAKED") is None, dte
        # Assert the exact reason set, not just the boolean: this fixture's
        # liquidity and PoP both pass, so "capital efficiency" alone is the
        # discriminating outcome -- a gate that started failing for a second
        # dimension would otherwise still read as green here.
        g = sc.evaluate_gates(_naked(dte=dte))
        assert not g["passed_min"], dte
        assert g["reasons"] == ["capital efficiency"], (dte, g["reasons"])


# --- Task 2.7: the annualisation-horizon floor ------------------------------
#
# `_reward_metric` divides by `max(dte, MIN_ANNUALISE_DTE)`. Every test below is
# written against the CONSTANT rather than against 5, so tuning it re-runs the
# same properties instead of turning them red -- except the two that assert the
# floor's own bounds, which is where a tune SHOULD have to argue.


def _capeff(dte, per_trade=0.0170):
    """Per-trade return `per_trade` at `dte`, through the production metric."""
    return sc._reward_metric(
        _naked(max_profit=per_trade * 10000.0, capital=10000.0, dte=dte), "NAKED")


def test_reward_metric_floors_the_annualisation_horizon_below_the_threshold():
    """Under the floor, the divisor is the floor -- not the trade's own dte.

    Asserted as an EQUALITY against the floored expression and an INEQUALITY
    against the unfloored one, because "smaller than 365x" alone would also hold
    for any other divisor someone substituted.
    """
    f = sc.MIN_ANNUALISE_DTE
    pt = 0.0170
    for dte in range(1, f):
        got = _capeff(dte, pt)
        assert abs(got - pt * 365.0 / f) < 1e-9, dte
        assert got < pt * 365.0 / dte, (dte, got)
    # Non-vacuity: the loop must have run. A floor of 1 would make it empty and
    # every assertion above free.
    assert f > 1, "MIN_ANNUALISE_DTE <= 1 floors nothing -- the loop is vacuous"


def test_reward_metric_does_not_floor_at_or_above_the_threshold():
    """At and above the floor the metric is the plain annualised rate, so the
    horizons this task is not about are untouched."""
    f = sc.MIN_ANNUALISE_DTE
    pt = 0.0170
    for dte in (f, f + 1, 7, 21, 35, 45, 60):
        assert abs(_capeff(dte, pt) - pt * 365.0 / dte) < 1e-9, dte


def test_the_floor_is_continuous_at_the_threshold():
    """No cliff of its own: dte == MIN_ANNUALISE_DTE reads the same either way.

    A floor implemented as `dte < F and F or dte`-style branching, or applied
    one off the boundary, would step here.
    """
    f = sc.MIN_ANNUALISE_DTE
    assert abs(_capeff(f) - _capeff(f - 1)) < 1e-9


def test_zero_dte_is_still_none_the_floor_is_on_the_divisor_not_the_guard():
    """The floor caps the annualisation; it does not make a missing horizon
    judgeable. `max(0, MIN_ANNUALISE_DTE)` is a perfectly good divisor -- the
    point is that it is never reached, because dte <= 0 fails the guard first.
    """
    for dte in (0, -1, -sc.MIN_ANNUALISE_DTE):
        assert sc._reward_metric(_naked(dte=dte), "NAKED") is None, dte


def test_one_dte_naked_short_reward_is_bounded_by_the_floor_not_by_365():
    """The bug this task fixes: a one-day credit annualised 365x cleared the bar
    on ~0.03% per trade.

    `per_trade` is chosen to sit between the two thresholds -- comfortably over
    the unfloored requirement (bar x 1/365) and comfortably under the floored one
    (bar x F/365) -- so this test fails BOTH if the floor is removed and if it is
    applied at the wrong end.
    """
    bar = sc.GATE_BARS["NAKED"]["min"]["capeff"]
    f = sc.MIN_ANNUALISE_DTE
    unfloored_needs = bar / 365.0
    floored_needs = bar * f / 365.0
    assert floored_needs > unfloored_needs * 2, (
        "MIN_ANNUALISE_DTE is too small for this test to discriminate")
    per_trade = (unfloored_needs + floored_needs) / 2.0

    reward = _capeff(1, per_trade)
    assert reward < bar, (
        f"a 1-DTE short returning {per_trade * 100:.3f}% per trade still clears "
        f"the {bar}/yr bar at {reward:.4f} -- the horizon floor is not applied")
    # ...and the gate cuts it, on the reward dimension alone.
    sig = _naked(max_profit=per_trade * 10000.0, capital=10000.0, dte=1)
    g = sc.evaluate_gates(sig)
    assert not g["passed_min"]
    assert g["reasons"] == ["capital efficiency"], g["reasons"]
    # Non-vacuity, and the whole point: unfloored, this same row PASSED.
    assert per_trade * 365.0 >= bar, (
        "fixture no longer clears the bar under the old 365x metric -- this test "
        "would pass even with the annualisation reverted")


def test_the_floor_does_not_reach_into_the_swing_scan_window():
    """MIN_ANNUALISE_DTE's upper bound, made executable.

    5 was chosen as the LARGEST floor that rescales only horizons the 0-DTE scan
    window owns; 7 discounts 5-6 DTE swing candidates, 10 discounts 5-9. Read
    from `run_full_scan`'s source because the windows are locals there, following
    `test_scanner_engine.test_run_full_scan_applies_gex_gate`. If the scrape stops
    matching, the window was RENAMED, not removed -- fix the pattern, do not
    delete the test.
    """
    import inspect
    import re

    import scanner_engine

    src = inspect.getsource(scanner_engine.run_full_scan)
    m = re.search(r"^\s*swing_min_dte\s*=\s*(\d+)\s*$", src, re.M)
    assert m is not None, (
        "could not read swing_min_dte out of run_full_scan -- the local was "
        "renamed; re-point this pattern rather than dropping the bound")
    swing_min = int(m.group(1))
    assert sc.MIN_ANNUALISE_DTE <= swing_min, (
        f"MIN_ANNUALISE_DTE {sc.MIN_ANNUALISE_DTE} rescales "
        f"{[d for d in range(swing_min, sc.MIN_ANNUALISE_DTE)]} DTE, which the "
        f"SWING window ({swing_min}+) owns -- it would discount genuine swing "
        "candidates, which is not what the floor is for. Re-run "
        "`tools/sweep_naked_capeff.py --floors` before raising it.")


def _same_day_chain(spot=100.0):
    """A chain whose only expiration is TODAY -> `_dte_for` yields dte == 0.

    Deliberately RICH: the 0.28-delta short call carries a 3.05 mark, so its
    un-annualised max_profit/capital is 0.152 -- comfortably over the 0.10 bar,
    and over the per-TRADE bar that stood before Task 2.6. No real 0-DTE chain
    prices like this; that is the point. It removes "the reward was thin" as an
    explanation for the cut, leaving only the horizon guard.
    """
    today = _dt.date.today().isoformat()

    def c(strike, delta, mark):
        return {"delta": delta, "mark": mark, "bid": mark - 0.02, "ask": mark + 0.02,
                "theta": -0.20, "vega": 0.02, "gamma": 0.03, "volatility": 28.0,
                "totalVolume": 5000, "openInterest": 12000}

    return {
        "underlyingPrice": spot,
        "callExpDateMap": {f"{today}:0": {
            "100.0": [c(100.0, 0.50, 4.00)],
            "101.0": [c(101.0, 0.28, 3.05)],
            "102.0": [c(102.0, 0.12, 2.00)]}},
        "putExpDateMap": {f"{today}:0": {
            "100.0": [c(100.0, -0.50, 4.00)],
            "99.0": [c(99.0, -0.28, 3.05)],
            "98.0": [c(98.0, -0.12, 2.00)]}},
    }


def test_same_day_naked_short_is_cut_for_the_horizon_not_for_thin_reward():
    """The 0-DTE naked-short class is unreachable for the NAKED reward gate.

    Driven from the BUILDER, not from `_reward_metric` -- this repo's own
    `signal_band` lesson is that a consumer-side guard proves nothing until a
    test drives it from the producer. `build_directional` really does emit naked
    shorts at dte == 0 (`_dte_for` clamps with max(0, ...), `zerodte_min_dte` is
    0, the Strategy Finder's DTE-min input defaults to 0), so this is a live
    population and not a hypothetical.

    Asserts the REASON, not just the cut: the un-annualised capital efficiency
    clears the bar, so a future change that starts failing this row for thin
    reward -- or one that floors dte at 1 and lets every 0-DTE naked short
    through on a 365x rescale -- is visible here rather than silent.
    """
    sigs = ss.build_directional(_same_day_chain(), "SPY", spot=100.0, atm_iv=0.28,
                                dte_min=0, dte_max=1)
    short_call = next(s for s in sigs if s["type"] == "SHORT_CALL")

    # The population: the builder itself produced a same-day horizon.
    assert short_call["dte"] == 0, "fixture no longer builds a same-day contract"

    # Not thin: un-annualised, this reward clears the 0.10 min bar outright.
    per_trade = short_call["max_profit"] / short_call["capital"]
    assert per_trade > sc.GATE_BARS["NAKED"]["min"]["capeff"], (
        f"fixture reward {per_trade:.3f} is thin on its own -- the cut below "
        "would no longer be attributable to the horizon guard")

    # The horizon guard: the reward was never computed, not computed and judged.
    assert sc._reward_metric(short_call, "NAKED") is None

    # ...and the row is cut, on the reward dimension alone (liquidity + PoP pass).
    gates = sc.evaluate_gates(short_call)
    assert not gates["passed_min"]
    assert gates["reasons"] == ["capital efficiency"]

    scored = sc.score_strategy(dict(short_call), sc.infer_market_view({}, {}),
                               atm_iv=0.28, em_1sd=1.5)
    assert scored["grade"] == "Weak"
    assert scored["grade_reason"] == "Fails: capital efficiency"


def test_gates_naked_missing_dte_fails_rather_than_passing():
    """Absence means "cannot judge", and unjudgeable does not pass -- the same
    contract evaluate_gates already applies to an unknown R:R."""
    for absent in ({}, {"dte": None}, {"dte": "35"}, {"dte": True}):
        sig = _naked()
        del sig["dte"]
        sig.update(absent)
        assert sc._reward_metric(sig, "NAKED") is None, absent
        g = sc.evaluate_gates(sig)
        assert not g["passed_min"], absent
        assert g["reasons"] == ["capital efficiency"], (absent, g["reasons"])


def test_gates_naked_low_capital_efficiency_fails_reward():
    # UPDATED (Task 2.6): the old fixture carried NO dte and a per-trade capeff
    # of 3.5/90 = 3.9%, which annualises to 40%/yr at 35 DTE -- it would now pass
    # on reward and only "fail" because the horizon was missing. Re-stated as a
    # genuinely thin naked short: 3.5/900 over 35 days = 4.1%/yr, under the bar.
    g = sc.evaluate_gates({"type": "SHORT_CALL", "rr": None, "net_credit": 3.5,
        "pop_pct": 70, "max_profit": 3.5, "capital": 900.0, "dte": 35,
        "legs": [{"bid": 3.4, "ask": 3.6, "mark": 3.5, "volume": 300, "oi": 800}]})
    assert not g["passed_min"] and "capital efficiency" in " ".join(g["reasons"])


def test_gates_naked_reward_failure_is_labelled_capital_efficiency_not_rr():
    """A naked short has no R:R -- its reward gate IS capital efficiency, so
    reporting "R:R" names a dimension the profile does not even compare."""
    g = sc.evaluate_gates(_naked(max_profit=1.0))
    assert g["reasons"] == ["capital efficiency"]


def test_gates_non_naked_reward_failure_is_still_labelled_rr():
    g = sc.evaluate_gates({"type": "BULL_CALL", "rr": 0.1, "pop_pct": 40,
        "legs": [{"bid": 1.0, "ask": 1.02, "mark": 1.01, "volume": 500, "oi": 1000}]})
    assert "R:R" in g["reasons"] and "capital efficiency" not in g["reasons"]


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
