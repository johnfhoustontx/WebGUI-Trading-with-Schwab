"""A NaN is "no reading" in the Strategy Finder, never the top score.

Audit AC-10. ``max(0, min(100, nan))`` is 100, and ``isinstance(nan, float)`` is
True, so every normaliser that checked only the TYPE scored a NaN input as its
best possible value: a composite of 45.5 became 60.0. Each normaliser states
what a missing input means; a NaN (or a bool) now takes that same branch.
"""
import math

import pytest

import options_calculator as oc
import strategy_scanner
import strategy_scoring as ss

NAN = float("nan")
VIEW = {"direction": "bullish", "conviction": 0.6, "vol_regime": "mid"}


def test_fit_directional_reads_a_nan_delta_as_no_delta():
    assert ss.fit_directional(NAN, VIEW) == ss.fit_directional(None, VIEW) == 50.0


def test_fit_directional_reads_a_nan_conviction_as_none():
    view = {"direction": "bullish", "conviction": NAN}
    assert ss.fit_directional(0.4, view) == 50.0


def test_fit_vol_reads_a_nan_vega_as_no_vega():
    assert ss.fit_vol(NAN, "low") == ss.fit_vol(None, "low") == 50.0


def test_q_rr_scores_a_nan_ratio_as_unusable():
    assert ss.q_rr({"rr": NAN}) == 0.0
    assert ss.q_rr({"rr": True}) == 0.0


def test_q_rr_fallback_ignores_a_nan_probability():
    assert ss.q_rr({"rr": None, "pop_pct": NAN}) == 50.0


@pytest.mark.parametrize("signal", [
    {"max_profit": NAN, "capital": 1000.0},
    {"max_profit": 100.0, "capital": NAN},
    {"max_profit": True, "capital": 1000.0},
])
def test_q_capital_eff_falls_back_when_an_input_is_not_a_number(signal):
    assert ss.q_capital_eff(signal) == 50.0
    assert ss.q_capital_eff({**signal, "pop_pct": 62.0}) == 62.0
    assert ss.q_capital_eff({**signal, "pop_pct": NAN}) == 50.0


def test_q_breakeven_is_neutral_without_a_usable_move_or_spot():
    sig = {"breakevens": [95.0], "underlying_price": 100.0, "family": "DIRECTIONAL"}
    assert ss.q_breakeven_vs_em(sig, NAN) == 50.0
    assert ss.q_breakeven_vs_em({**sig, "underlying_price": NAN}, 5.0) == 50.0
    assert ss.q_breakeven_vs_em({**sig, "breakevens": [NAN]}, 5.0) == 50.0


def test_q_breakeven_uses_the_breakevens_that_are_numbers():
    sig = {"breakevens": [NAN, 98.0], "underlying_price": 100.0, "family": "DIRECTIONAL"}
    assert ss.q_breakeven_vs_em(sig, 5.0) == pytest.approx(60.0)


def test_q_pop_reads_a_nan_as_no_reading():
    assert ss.q_pop({"pop_pct": NAN}) == 50.0
    assert ss.q_pop({"pop_pct": True}) == 50.0


def test_a_nan_input_cannot_raise_the_composite():
    """The audit's reproduction: the same trade scored higher with a NaN in it."""
    base = {"type": "BULL_CALL", "family": "DIRECTIONAL", "net_delta": 0.3,
            "net_vega": 0.02, "rr": 0.4, "max_profit": 120.0, "capital": 300.0,
            "pop_pct": 40.0, "breakevens": [103.0], "underlying_price": 100.0,
            "legs": []}
    clean = ss.score_strategy(dict(base), VIEW, 0.25, 5.0)["composite_score"]
    for key in ("rr", "pop_pct", "net_delta", "net_vega", "max_profit"):
        dirty = ss.score_strategy({**base, key: NAN}, VIEW, 0.25, 5.0)
        assert math.isfinite(dirty["composite_score"])
        assert dirty["composite_score"] <= clean + 1e-9, (key, dirty["composite_score"], clean)


# ------------------------------------------------------------ strike selection

def _strikes(deltas):
    return {float(k): {"strike": float(k), "delta": d} for k, d in deltas.items()}


def test_a_nan_delta_strike_never_wins_the_selection():
    """``min`` keeps a NaN key forever once it holds it: every later
    ``x < nan`` is False."""
    strikes = _strikes({90: NAN, 95: -0.20, 100: -0.50})
    assert strategy_scanner.nearest_by_delta(strikes, 0.25)["strike"] == 95.0


def test_a_placeholder_or_missing_delta_is_skipped():
    strikes = _strikes({90: -999.0, 95: None, 100: -0.45})
    assert strategy_scanner.nearest_by_delta(strikes, 0.25)["strike"] == 100.0


def test_no_usable_delta_selects_nothing():
    assert strategy_scanner.nearest_by_delta(_strikes({90: NAN}), 0.25) is None
    assert strategy_scanner.nearest_by_delta({}, 0.25) is None


# ---------------------------------------------------------------- implied_vol

@pytest.mark.parametrize("kw", [
    dict(price=NAN), dict(S=NAN), dict(K=NAN), dict(T=NAN), dict(r=NAN),
    dict(price=float("inf")),
])
def test_implied_vol_of_a_non_finite_input_is_none(kw):
    args = dict(price=2.0, S=100.0, K=100.0, T=0.1, r=0.045)
    args.update(kw)
    assert oc.implied_vol(args["price"], args["S"], args["K"], args["T"],
                          args["r"], "call") is None


def test_implied_vol_still_solves_a_real_price():
    price = oc.bs_price(100.0, 100.0, 0.1, 0.045, 0.30, "call")
    assert oc.implied_vol(price, 100.0, 100.0, 0.1, 0.045, "call") == pytest.approx(0.30, abs=1e-4)
