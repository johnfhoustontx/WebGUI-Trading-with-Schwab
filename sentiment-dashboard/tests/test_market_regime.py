"""Tests for scoring.market_regime — evidence ramps -> raw intensities + memberships."""
from scoring import market_regime as MR


# ---------------------------------------------------------------- ramp


def test_ramp_edges():
    assert MR.ramp(17, 18, 30) == 0.0
    assert MR.ramp(18, 18, 30) == 0.0
    assert MR.ramp(30, 18, 30) == 1.0
    assert MR.ramp(35, 18, 30) == 1.0
    assert abs(MR.ramp(24, 18, 30) - 0.5) < 1e-9


def test_ramp_inverted():
    # lo > hi -> inverted ramp (high x -> 0, low x -> 1)
    assert MR.ramp(25, 25, 15) == 0.0
    assert MR.ramp(15, 25, 15) == 1.0
    assert abs(MR.ramp(20, 25, 15) - 0.5) < 1e-9
    assert MR.ramp(30, 25, 15) == 0.0   # clamped
    assert MR.ramp(10, 25, 15) == 1.0   # clamped


# ---------------------------------------------------------------- fixtures


def _quiet_range_day():
    return {"adx": 14, "adx_rising": False, "ema_slope_atr": 0.02,
            "bb_width_pctile": 0.45, "bb_width_expansion": 1.0,
            "band_hug_frac": 0.1, "vwap_hold_frac": 0.55, "or_break_state": "none",
            "or_failed_count": 0, "wick_two_sided": 0.1, "whipsaw_count": 2,
            "profile_balance": 0.9, "rel_vol": 0.9, "atr_pctile": 0.3,
            "vix1d_spike_pct": -2.0, "term_inversion": 0.0,
            "gap_open_pct": 0.1, "gap_filled": True,
            "above_flip": True, "below_flip_deep": 0.0}


def _trend_day():
    return _quiet_range_day() | {"adx": 32, "adx_rising": True, "ema_slope_atr": 0.4,
                                 "band_hug_frac": 0.8, "vwap_hold_frac": 0.95,
                                 "or_break_state": "held", "profile_balance": 0.2}


def _breakout_day():
    return _quiet_range_day() | {"bb_width_pctile": 0.05, "bb_width_expansion": 1.9,
                                 "rel_vol": 2.2, "or_break_state": "held"}


def _choppy_day():
    return _quiet_range_day() | {"wick_two_sided": 0.9, "or_failed_count": 3,
                                 "whipsaw_count": 8, "atr_pctile": 0.8, "adx": 16,
                                 "profile_balance": 0.3, "above_flip": False}


def _weak_everything_day():
    return {"adx": 17, "adx_rising": False, "ema_slope_atr": 0.2,
            "bb_width_pctile": 0.75, "bb_width_expansion": 1.0,
            "band_hug_frac": 0.2, "vwap_hold_frac": 0.55, "or_break_state": "none",
            "or_failed_count": 0, "wick_two_sided": 0.2, "whipsaw_count": 2,
            "profile_balance": 0.05, "rel_vol": 1.0, "atr_pctile": 0.5,
            "vix1d_spike_pct": 0.0, "term_inversion": 0.05,
            "gap_open_pct": 0.0, "gap_filled": True,
            "above_flip": False, "below_flip_deep": 0.1}


def test_fixture_keys_match_contract():
    assert set(_quiet_range_day()) == set(MR.EVIDENCE_KEYS)
    assert MR.REGIMES == ("mean_reversion", "trending", "breakout", "choppy", "crisis")


# ---------------------------------------------------------------- archetype days


def test_quiet_range_day_scores_mean_reversion_dominant():
    s = MR.score_regimes(_quiet_range_day())
    assert max(s.raw, key=s.raw.get) == "mean_reversion"
    assert s.raw["crisis"] < 0.1 and s.raw["trending"] < 0.35
    assert s.unclear is False    # strong mean-reversion evidence must not read Unclear


def test_trend_day_scores_trending_dominant():
    s = MR.score_regimes(_trend_day())
    assert max(s.raw, key=s.raw.get) == "trending"
    assert s.raw["trending"] > 0.7


def test_breakout_day_scores_breakout_dominant():
    s = MR.score_regimes(_breakout_day())
    assert max(s.raw, key=s.raw.get) == "breakout"
    assert s.raw["breakout"] > 0.9


def test_choppy_day_scores_choppy_dominant():
    s = MR.score_regimes(_choppy_day())
    assert max(s.raw, key=s.raw.get) == "choppy"
    assert s.raw["choppy"] > 0.7


# ---------------------------------------------------------------- crisis semantics


def test_single_crisis_tell_is_sufficient():
    ev = _quiet_range_day() | {"vix1d_spike_pct": 40.0}
    assert MR.score_regimes(ev).raw["crisis"] >= 0.9


def test_crisis_is_max_not_average():
    # one strong tell among quiet others must not be diluted
    ev = _quiet_range_day() | {"term_inversion": 0.95}
    s = MR.score_regimes(ev)
    assert s.raw["crisis"] >= 0.95


def test_crisis_unfilled_gap():
    ev = _quiet_range_day() | {"gap_open_pct": 1.8, "gap_filled": False}
    assert MR.score_regimes(ev).raw["crisis"] == 1.0


def test_crisis_filled_gap_does_not_fire():
    ev = _quiet_range_day() | {"gap_open_pct": 1.8, "gap_filled": True}
    assert MR.score_regimes(ev).raw["crisis"] == 0.0


# ---------------------------------------------------------------- missing inputs


def test_missing_inputs_drop_out_not_default():
    ev = {k: None for k in _quiet_range_day()}
    s = MR.score_regimes(ev)
    assert all(v == 0.0 for v in s.raw.values())
    assert s.unclear is True
    assert all(abs(m - 0.2) < 1e-9 for m in s.memberships.values())


def test_empty_evidence_dict_is_safe():
    s = MR.score_regimes({})
    assert all(v == 0.0 for v in s.raw.values()) and s.unclear is True


def test_partial_missing_inputs_still_score():
    ev = _quiet_range_day() | {"profile_balance": None, "bb_width_pctile": None}
    s = MR.score_regimes(ev)
    assert max(s.raw, key=s.raw.get) == "mean_reversion"
    assert s.raw["mean_reversion"] > 0.5


def test_breakout_missing_leg_zeroes_it():
    ev = _breakout_day() | {"rel_vol": None}
    assert MR.score_regimes(ev).raw["breakout"] == 0.0


# ---------------------------------------------------------------- aggregate shape


def test_memberships_normalize_and_confidence_is_max_raw():
    s = MR.score_regimes(_quiet_range_day())
    assert abs(sum(s.memberships.values()) - 1.0) < 1e-9
    assert s.confidence == max(s.raw.values())


def test_memberships_are_raw_proportional():
    s = MR.score_regimes(_trend_day())
    total = sum(s.raw.values())
    for r in MR.REGIMES:
        assert abs(s.memberships[r] - s.raw[r] / total) < 1e-9


def test_unclear_floor():
    s = MR.score_regimes(_weak_everything_day())
    assert s.confidence < MR.UNCLEAR_FLOOR
    assert s.unclear is True
    # still publishes a raw-proportional vector (not uniform)
    assert abs(sum(s.memberships.values()) - 1.0) < 1e-9


def test_evidence_strings_present():
    s = MR.score_regimes(_trend_day())
    assert s.evidence and all(isinstance(e, str) and e for e in s.evidence)
    assert any("ADX" in e for e in s.evidence)


def test_crisis_evidence_string():
    ev = _quiet_range_day() | {"vix1d_spike_pct": 40.0}
    s = MR.score_regimes(ev)
    assert any("VIX1D" in e for e in s.evidence)


# ---------------------------------------------------------------- malformed inputs
# NaN/bool-string/unknown-enum evidence must degrade to ABSENT, never fabricate
# intensity (a NaN warm-up value is routine in upstream indicator series).


def test_nan_input_drops_out():
    ev = _quiet_range_day() | {"adx": float("nan")}
    s = MR.score_regimes(ev)
    # the ADX term drops out; the regime still scores from its remaining inputs
    assert max(s.raw, key=s.raw.get) == "mean_reversion"
    assert s.raw["mean_reversion"] > 0.5
    # and matches the explicit-None behavior exactly
    assert s.raw == MR.score_regimes(_quiet_range_day() | {"adx": None}).raw


def test_all_nan_inputs_score_nothing():
    ev = {k: float("nan") for k in _quiet_range_day()}
    s = MR.score_regimes(ev)
    assert all(v == 0.0 for v in s.raw.values())
    assert s.unclear is True


def test_inf_input_drops_out():
    # on a breakout day an inf rel_vol would otherwise score the leg at 1.0
    ev = _breakout_day() | {"rel_vol": float("inf")}
    assert MR.score_regimes(ev).raw["breakout"] == 0.0


def test_bool_string_is_absent():
    # a string "false" is NOT truthy evidence — it must behave as absent
    base = MR.score_regimes(_quiet_range_day() | {"above_flip": None}).raw
    assert MR.score_regimes(_quiet_range_day() | {"above_flip": "false"}).raw == base
    assert MR.score_regimes(_quiet_range_day() | {"gap_filled": "false",
                                                  "gap_open_pct": 1.8}).raw["crisis"] == 0.0


def test_or_break_state_unknown_is_absent():
    # a typo'd enum value must be absent, not score as an active "failed" 0.0
    base = MR.score_regimes(_trend_day() | {"or_break_state": None}).raw
    assert MR.score_regimes(_trend_day() | {"or_break_state": "HELD"}).raw == base
    # breakout requires ALL legs — an unknown OR state means the OR leg is MISSING
    assert MR.score_regimes(_breakout_day() | {"or_break_state": "HELD"}).raw["breakout"] == 0.0


# ---------------------------------------------------------------- branch pins


def test_adx_rising_none_means_no_discount():
    raw_none = MR.score_regimes(_trend_day() | {"adx_rising": None}).raw["trending"]
    raw_false = MR.score_regimes(_trend_day() | {"adx_rising": False}).raw["trending"]
    raw_true = MR.score_regimes(_trend_day()).raw["trending"]
    assert raw_none == raw_true          # None -> multiplier 1.0
    assert raw_false < raw_none          # False -> 0.7 discount visible in the raw
    # discount magnitude: 0.3 of the (saturated) ADX term's 0.30 weight
    assert abs((raw_none - raw_false) - 0.3 * 0.30) < 1e-9


def test_breakout_or_none_factor():
    held = MR.score_regimes(_breakout_day()).raw["breakout"]
    none_ = MR.score_regimes(_breakout_day() | {"or_break_state": "none"}).raw["breakout"]
    assert abs(none_ - MR.OR_NONE_FACTOR * held) < 1e-9
    assert MR.score_regimes(_breakout_day() | {"or_break_state": "failed"}
                            ).raw["breakout"] == 0.0
