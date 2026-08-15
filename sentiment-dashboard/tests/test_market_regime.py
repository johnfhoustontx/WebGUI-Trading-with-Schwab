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
            "vix_level": 15.0,
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
            "vix_level": 16.0,
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


def test_crisis_extreme_unfilled_gap_fires():
    # A genuinely crisis-grade unfilled gap (>= CRISIS_GAP_HI_PCT) → full crisis.
    ev = _quiet_range_day() | {"gap_open_pct": -5.5, "gap_filled": False}
    assert MR.score_regimes(ev).raw["crisis"] == 1.0


def test_crisis_routine_gap_does_not_fire():
    # THE reported bug: a routine ~1% opening gap must NOT pin crisis. It sits
    # below CRISIS_GAP_LO_PCT, so it contributes ZERO crisis intensity (the gap
    # tell is a ramp over extreme gaps, not a binary cliff at 1%).
    for g in (-1.1, 1.0, 1.8, 2.4):
        ev = _quiet_range_day() | {"gap_open_pct": g, "gap_filled": False}
        assert MR.score_regimes(ev).raw["crisis"] == 0.0, f"gap {g}% should not fire"


def test_crisis_gap_is_a_ramp_not_a_cliff():
    # Partial: a mid-range gap gives partial crisis, and NOT enough to trip the
    # crisis-attack force-commit (0.7) — so a single moderate gap can't lock the day.
    mid = (MR.CRISIS_GAP_LO_PCT + MR.CRISIS_GAP_HI_PCT) / 2.0
    c = MR.score_regimes(_quiet_range_day()
                         | {"gap_open_pct": -mid, "gap_filled": False}).raw["crisis"]
    assert 0.0 < c < MR.CRISIS_ATTACK


def test_crisis_filled_gap_does_not_fire():
    # Even an extreme gap that has been FILLED (market recovered) is not crisis.
    ev = _quiet_range_day() | {"gap_open_pct": -5.5, "gap_filled": True}
    assert MR.score_regimes(ev).raw["crisis"] == 0.0


def test_volatile_vix_level_is_the_primary_tell():
    # Absolute VIX drives the volatile/stress regime: a stressed VIX saturates it,
    # a normal VIX contributes nothing.
    assert MR.score_regimes(_quiet_range_day() | {"vix_level": 34.0}).raw["crisis"] == 1.0
    assert MR.score_regimes(_quiet_range_day() | {"vix_level": 18.0}).raw["crisis"] == 0.0
    mid = MR.score_regimes(_quiet_range_day() | {"vix_level": 28.0}).raw["crisis"]
    assert 0.0 < mid < 1.0


def test_high_vix_trips_the_force_commit():
    # A genuinely stressed VIX (~30+) must clear the fast-attack threshold so the
    # label can snap to Stressed.
    assert MR.crisis_attacked(
        MR.score_regimes(_quiet_range_day() | {"vix_level": 31.0}).raw["crisis"])


def test_normal_volatile_day_no_longer_reads_crisis():
    # THE reported bug: a wide-range but otherwise-calm session (VIX ~18, ATR at
    # the ~91st pctile, contango, no gap) used to read ~34% crisis off the ATR
    # tell alone. With VIX as primary + the raised ATR floor it now reads 0.
    ev = _quiet_range_day() | {"vix_level": 18.0, "atr_pctile": 0.91,
                               "adx": 30, "band_hug_frac": 0.67, "whipsaw_count": 9,
                               "or_failed_count": 3}
    s = MR.score_regimes(ev)
    assert s.raw["crisis"] == 0.0
    assert max(s.raw, key=s.raw.get) != "crisis"


def test_atr_floor_raised_only_extreme_range_contributes():
    # A merely-wide day (91st pctile) contributes 0; a near-record day (99th) fires.
    assert MR.score_regimes(_quiet_range_day() | {"atr_pctile": 0.91}).raw["crisis"] == 0.0
    assert MR.score_regimes(_quiet_range_day() | {"atr_pctile": 0.99}).raw["crisis"] >= 0.9


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


def test_evidence_detail_mirrors_evidence_and_names_the_source_regime():
    """The additive attribution: same strings, same order, each tagged with the
    regime whose scorer produced it. ``evidence`` must be untouched."""
    s = MR.score_regimes(_trend_day())
    assert [d["text"] for d in s.evidence_detail] == s.evidence
    assert all(d["regime"] in MR.REGIMES for d in s.evidence_detail)
    # trending is what a trend day scores, so its ADX line is attributed there.
    adx = [d for d in s.evidence_detail if "ADX" in d["text"]]
    assert adx and adx[0]["regime"] == "trending"


def test_evidence_detail_separates_adverse_lines_from_informational_ones():
    """The reason this field exists: on one sample, lines from DIFFERENT regimes
    are flattened into one list, and a renderer cannot tell them apart from the
    copy alone. A chop day scores mean_reversion AND choppy at once."""
    ev = _quiet_range_day() | {"or_failed_count": 3.0, "whipsaw_count": 11.0,
                               "atr_pctile": 0.8, "adx": 18.0}
    s = MR.score_regimes(ev)
    by_regime = {}
    for d in s.evidence_detail:
        by_regime.setdefault(d["regime"], []).append(d["text"])
    assert len(by_regime) >= 2, f"expected several contributors, got {by_regime}"
    choppy = by_regime.get("choppy") or []
    assert any("failed OR breaks" in t for t in choppy)
    assert any("EMA whipsaws" in t for t in choppy)
    # ... and those two are NOT attributed to the quiet-range regime.
    assert not any("whipsaw" in t for t in by_regime.get("mean_reversion", []))


def test_evidence_detail_is_empty_when_nothing_scores():
    s = MR.score_regimes({})
    assert s.evidence == [] and s.evidence_detail == []


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


# ---------------------------------------------------------------- temporal layer
# Task 3: wall-clock EMA smoothing, transition detection, label commit, crisis attack.


def _vec(**overrides):
    """A memberships dict — uniform 0.2 unless overridden."""
    v = {r: 0.2 for r in MR.REGIMES}
    v.update(overrides)
    return v


def _uniform():
    return _vec()


# -------- alpha


def test_alpha_from_half_life_wall_clock():
    # after exactly one half-life the old value's weight is 0.5
    a = MR.alpha(dt_sec=900, half_life_min=15)
    assert abs((1 - a) - 0.5) < 1e-9


def test_alpha_zero_or_negative_dt_is_zero():
    assert MR.alpha(dt_sec=0, half_life_min=15) == 0.0
    assert MR.alpha(dt_sec=-30, half_life_min=15) == 0.0


def test_alpha_scales_with_dt():
    # two half-lives -> old weight 0.25
    a = MR.alpha(dt_sec=1800, half_life_min=15)
    assert abs((1 - a) - 0.25) < 1e-9


def test_alpha_none_dt_is_zero():
    assert MR.alpha(dt_sec=None, half_life_min=15) == 0.0


def test_alpha_nonpositive_half_life_is_one():
    # a degenerate half-life means no smoothing: the sample fully replaces
    assert MR.alpha(dt_sec=300, half_life_min=0) == 1.0
    assert MR.alpha(dt_sec=300, half_life_min=-5) == 1.0


# -------- smooth


def test_smooth_cold_start_initializes_to_sample():
    sample = _vec(trending=0.6, mean_reversion=0.1, breakout=0.1, choppy=0.1, crisis=0.1)
    fast, slow = MR.smooth(None, None, sample, dt_sec=300)
    for r in MR.REGIMES:
        assert abs(fast[r] - sample[r]) < 1e-9
        assert abs(slow[r] - sample[r]) < 1e-9
    assert fast is not sample and slow is not sample and fast is not slow


def test_smooth_converges_and_fast_leads_slow():
    sample = _vec(trending=0.6, mean_reversion=0.1, breakout=0.1, choppy=0.1, crisis=0.1)
    fast, slow = _uniform(), _uniform()
    for _ in range(12):   # 1h of 5-min samples
        fast, slow = MR.smooth(fast, slow, sample, dt_sec=300)
    assert fast["trending"] > slow["trending"] > 0.2
    assert fast["trending"] < 0.6 + 1e-9      # converging, not overshooting
    assert abs(sum(fast.values()) - 1.0) < 1e-9
    assert abs(sum(slow.values()) - 1.0) < 1e-9


def test_smooth_does_not_mutate_inputs():
    sample = _vec(trending=0.6, mean_reversion=0.1, breakout=0.1, choppy=0.1, crisis=0.1)
    fast0, slow0 = _uniform(), _uniform()
    fast_copy, slow_copy, sample_copy = dict(fast0), dict(slow0), dict(sample)
    MR.smooth(fast0, slow0, sample, dt_sec=300)
    assert fast0 == fast_copy and slow0 == slow_copy and sample == sample_copy


def test_smooth_renormalizes_defensively():
    # all-zero sample -> uniform
    zero = {r: 0.0 for r in MR.REGIMES}
    fast, slow = MR.smooth(None, None, zero, dt_sec=300)
    assert all(abs(v - 0.2) < 1e-9 for v in fast.values())
    assert all(abs(v - 0.2) < 1e-9 for v in slow.values())
    # a junk sum (2.0) is normalized before the EMA
    junk = {r: 0.4 for r in MR.REGIMES}
    fast, slow = MR.smooth(_uniform(), _uniform(), junk, dt_sec=300)
    assert abs(sum(fast.values()) - 1.0) < 1e-9
    assert all(abs(v - 0.2) < 1e-9 for v in fast.values())


# -------- detect_transition


def test_transition_reports_from_to_progress():
    fast = _vec(mean_reversion=0.3, trending=0.5, breakout=0.1, choppy=0.05, crisis=0.05)
    slow = _vec(mean_reversion=0.5, trending=0.3, breakout=0.1, choppy=0.05, crisis=0.05)
    t = MR.detect_transition(fast, slow)
    assert t is not None
    assert t["from"] == "mean_reversion" and t["to"] == "trending"
    assert abs(t["progress"] - 0.2 / MR.TRANSITION_FULL) < 1e-9


def test_transition_stable_returns_none():
    assert MR.detect_transition(_uniform(), _uniform()) is None


def test_transition_below_floor_returns_none():
    fast = _vec(trending=0.23, mean_reversion=0.17)
    slow = _vec(trending=0.20, mean_reversion=0.20)
    assert MR.detect_transition(fast, slow) is None   # divergence 0.03 < floor


def test_transition_at_floor_reports():
    fast = _vec(trending=0.25, mean_reversion=0.15)
    slow = _vec(trending=0.20, mean_reversion=0.20)
    t = MR.detect_transition(fast, slow)   # divergence exactly TRANSITION_FLOOR
    assert t is not None and t["to"] == "trending"


def test_transition_progress_clamps_to_1():
    fast = _vec(trending=0.6, mean_reversion=0.0, breakout=0.2, choppy=0.1, crisis=0.1)
    slow = _vec(trending=0.1, mean_reversion=0.5, breakout=0.2, choppy=0.1, crisis=0.1)
    t = MR.detect_transition(fast, slow)   # divergence 0.5 > TRANSITION_FULL
    assert t is not None and t["progress"] == 1.0


# -------- commit_label


def test_commit_label_cold_start_commits_immediately():
    fast = _vec(trending=0.5, mean_reversion=0.2, breakout=0.1, choppy=0.1, crisis=0.1)
    st = MR.commit_label(fast, MR.CommitState())
    assert st.committed == "trending" and st.streak == 0


def test_commit_label_needs_margin_for_n_reads():
    fast = _vec(trending=0.45, mean_reversion=0.3, breakout=0.1, choppy=0.1, crisis=0.05)
    st0 = MR.CommitState(committed="mean_reversion", streak=0)
    st1 = MR.commit_label(fast, st0)           # first margin-clearing read
    assert st1.committed == "mean_reversion" and st1.streak == 1
    st2 = MR.commit_label(fast, st1)           # second consecutive -> flip
    assert st2.committed == "trending" and st2.streak == 0
    assert st0.committed == "mean_reversion" and st0.streak == 0   # no mutation


def test_commit_label_margin_break_resets_streak():
    margin = _vec(trending=0.45, mean_reversion=0.3, breakout=0.1, choppy=0.1, crisis=0.05)
    # challenger leads but by < COMMIT_MARGIN
    thin = _vec(trending=0.32, mean_reversion=0.28, breakout=0.15, choppy=0.15, crisis=0.1)
    st = MR.CommitState(committed="mean_reversion", streak=0)
    st = MR.commit_label(margin, st)
    assert st.streak == 1
    st = MR.commit_label(thin, st)             # margin broken -> streak resets, held
    assert st.committed == "mean_reversion" and st.streak == 0
    st = MR.commit_label(margin, st)           # margin again -> streak restarts at 1
    assert st.committed == "mean_reversion" and st.streak == 1


def test_commit_label_dominant_unchanged_resets_streak():
    fast = _vec(mean_reversion=0.5, trending=0.2, breakout=0.1, choppy=0.1, crisis=0.1)
    st = MR.commit_label(fast, MR.CommitState(committed="mean_reversion", streak=1))
    assert st.committed == "mean_reversion" and st.streak == 0


def test_commit_label_different_challenger_restarts_streak():
    # Hysteresis is per-challenger: a streak built by one challenger must NOT be
    # inherited by a different one leading the next read.
    trend = _vec(trending=0.45, mean_reversion=0.25, breakout=0.1, choppy=0.1, crisis=0.1)
    brk = _vec(breakout=0.45, mean_reversion=0.25, trending=0.1, choppy=0.1, crisis=0.1)
    st = MR.CommitState(committed="mean_reversion", streak=0)
    st = MR.commit_label(trend, st)            # trending leads by margin -> streak 1
    assert st.committed == "mean_reversion" and st.streak == 1 and st.challenger == "trending"
    st = MR.commit_label(brk, st)              # DIFFERENT challenger -> streak restarts, HOLDS
    assert st.committed == "mean_reversion" and st.streak == 1 and st.challenger == "breakout"
    st = MR.commit_label(brk, st)              # same challenger again -> flips
    assert st.committed == "breakout" and st.streak == 0


# -------- crisis attack


def test_crisis_attack_bypasses_smoothing():
    out = MR.apply_crisis_attack(_uniform(), raw_crisis=0.85)
    assert abs(out["crisis"] - 0.85) < 1e-9          # jumps to the raw value
    assert max(out, key=out.get) == "crisis"
    assert abs(sum(out.values()) - 1.0) < 1e-9
    # the other regimes keep their relative proportions in the remainder
    others = [out[r] for r in MR.REGIMES if r != "crisis"]
    assert all(abs(v - others[0]) < 1e-9 for v in others)


def test_crisis_attack_keeps_higher_smoothed_crisis():
    fast = _vec(crisis=0.9, trending=0.025, mean_reversion=0.025,
                breakout=0.025, choppy=0.025)
    out = MR.apply_crisis_attack(fast, raw_crisis=0.7)
    assert abs(out["crisis"] - 0.9) < 1e-9   # max(fast, raw), never lowered


def test_crisis_attack_below_threshold_noop():
    fast = _uniform()
    out = MR.apply_crisis_attack(fast, raw_crisis=0.69)
    assert out is fast
    assert MR.apply_crisis_attack(fast, raw_crisis=None) is fast


def test_crisis_attack_does_not_mutate_input():
    fast = _uniform()
    copy = dict(fast)
    MR.apply_crisis_attack(fast, raw_crisis=0.85)
    assert fast == copy


def test_crisis_attack_degenerate_others_zero():
    # No mass in the other regimes (or crisis already 1.0) -> a pure crisis vector,
    # still valid and summing to 1.
    fast = _vec(crisis=1.0, trending=0.0, mean_reversion=0.0, breakout=0.0, choppy=0.0)
    out = MR.apply_crisis_attack(fast, raw_crisis=0.9)
    assert out["crisis"] == 1.0
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert all(out[r] == 0.0 for r in MR.REGIMES if r != "crisis")


def test_crisis_attacked_predicate():
    assert MR.crisis_attacked(None) is False
    assert MR.crisis_attacked(0.69) is False
    assert MR.crisis_attacked(0.7) is True
    assert MR.crisis_attacked(0.95) is True


# ------------------------------------------------- direction (display adornment)


def test_direction_sign_requires_both_reads_to_agree():
    # Slope up + composite direction up -> up. Slope up while the composite
    # direction is DOWN -> neutral: the two reads disagree, so no word is claimed.
    assert MR.direction_sign(0.20, 62.0) == 1
    assert MR.direction_sign(-0.20, 38.0) == -1
    assert MR.direction_sign(0.20, 38.0) == 0
    assert MR.direction_sign(-0.20, 62.0) == 0


def test_direction_sign_deadbands():
    # Inside either deadband -> neutral, so a score hovering at 50 or a slope
    # below the trending ramp's own floor cannot name a direction.
    assert MR.direction_sign(0.20, 51.0) == 0
    assert MR.direction_sign(0.01, 62.0) == 0
    assert MR.direction_sign(MR.DIRECTION_SLOPE_DEADBAND,
                             50.0 + MR.DIRECTION_TREND_DEADBAND) == 1


def test_direction_sign_missing_inputs_are_neutral():
    assert MR.direction_sign(None, 62.0) == 0
    assert MR.direction_sign(0.20, None) == 0
    assert MR.direction_sign(None, None) == 0
    assert MR.direction_sign(float("nan"), 62.0) == 0
    assert MR.direction_sign("up", 62.0) == 0


def test_direction_strong_splits_rally_from_firming():
    assert MR.direction_strong(0.30) is True
    assert MR.direction_strong(-0.30) is True
    assert MR.direction_strong(0.08) is False
    assert MR.direction_strong(None) is False


def test_commit_direction_needs_two_reads_to_claim_a_direction():
    state = MR.DirectionState()
    state = MR.commit_direction(1, state)
    assert state.committed == 0      # first up read: not yet claimed
    state = MR.commit_direction(1, state)
    assert state.committed == 1      # second consecutive read commits


def test_commit_direction_drops_to_neutral_immediately():
    # Asymmetric by design: claiming a direction takes two reads, ABANDONING one
    # takes a single read — never assert a direction the evidence stopped backing.
    state = MR.DirectionState(committed=1)
    state = MR.commit_direction(0, state)
    assert state.committed == 0


def test_commit_direction_flip_restarts_the_streak():
    state = MR.DirectionState()
    state = MR.commit_direction(1, state)
    state = MR.commit_direction(-1, state)
    assert state.committed == 0      # the up streak does not carry into down
    state = MR.commit_direction(-1, state)
    assert state.committed == -1


def test_commit_direction_does_not_mutate_input():
    state = MR.DirectionState(committed=0, streak=1, challenger=1)
    MR.commit_direction(1, state)
    assert (state.committed, state.streak, state.challenger) == (0, 1, 1)


def test_regime_label_base_names():
    assert MR.regime_label("mean_reversion") == "Balanced"
    assert MR.regime_label("trending") == "Trending"
    assert MR.regime_label("breakout") == "Breakout"
    assert MR.regime_label("choppy") == "Whipsaw"
    assert MR.regime_label("crisis") == "Stressed"


def test_regime_label_trending_takes_a_direction():
    assert MR.regime_label("trending", 1, strong=True) == "Rallying"
    assert MR.regime_label("trending", 1, strong=False) == "Firming"
    assert MR.regime_label("trending", -1, strong=True) == "Retreating"
    assert MR.regime_label("trending", -1, strong=False) == "Softening"
    assert MR.regime_label("trending", 0, strong=True) == "Trending"


def test_regime_label_breakout_inverts_to_breakdown():
    assert MR.regime_label("breakout", -1, strong=True) == "Breakdown"
    assert MR.regime_label("breakout", 1, strong=True) == "Breakout"
    assert MR.regime_label("breakout", 0) == "Breakout"


def test_regime_label_directionless_regimes_ignore_the_sign():
    # A balanced auction has no direction and a stress read is about fear, not
    # sign — a stray direction must not reword either.
    for key in ("mean_reversion", "choppy", "crisis"):
        base = MR.regime_label(key)
        assert MR.regime_label(key, 1, strong=True) == base
        assert MR.regime_label(key, -1, strong=True) == base


def test_regime_label_unknown_key_is_unclear():
    assert MR.regime_label("") == "Unclear"
    assert MR.regime_label(None) == "Unclear"
    assert MR.regime_label("nonsense") == "Unclear"
