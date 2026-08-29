import pytest

from scoring import aggression
from scoring.aggression import blend_aggression, AGG_WEIGHTS


def test_all_positive_blends_positive():
    s, c = blend_aggression({"effort": 0.8, "skew": 0.6, "flow": 0.4},
                            {"effort": 1.0, "skew": 1.0, "flow": 1.0})
    assert s > 0.4 and 0.0 < c <= 1.0


def test_missing_order_flow_drops_out():
    # order_flow absent (Phase 1-3) -> blends on the REST signals only, no crash
    s, c = blend_aggression({"effort": -0.5}, {"effort": 1.0})
    assert s < 0


def test_low_confidence_cannot_dominate():
    # a huge negative signal at near-zero confidence barely moves the blend
    s, _ = blend_aggression({"effort": 0.5, "skew": -1.0},
                            {"effort": 1.0, "skew": 0.01})
    assert s > 0


def test_all_missing_is_neutral_zero_conf():
    assert blend_aggression({}, {}) == (0.0, 0.0)


def test_score_clamped_to_unit_range():
    s, _ = blend_aggression({"effort": 5.0}, {"effort": 1.0})  # out-of-range input
    assert -1.0 <= s <= 1.0


def test_none_values_are_neutral_zero_conf():
    # explicit None component value and None confidence both drop out safely
    s, c = blend_aggression({"effort": None, "skew": 0.5},
                            {"effort": None, "skew": 1.0})
    assert s > 0 and 0.0 < c <= 1.0


def test_single_component_present():
    s, c = blend_aggression({"skew": 0.4}, {"skew": 1.0})
    assert s > 0
    # Only skew's weight participates -> the aggregate confidence is skew's SHARE
    # of the total weight. This asserted the raw weight (0.30) until 2026-08-20,
    # which is where the out-of-range 1.3 came from: AGG_WEIGHTS sums to 1.30, so
    # the raw sum is not a confidence.
    assert c == round(AGG_WEIGHTS["skew"] / sum(AGG_WEIGHTS.values()), 3)


def test_weights_need_not_sum_to_one():
    # blend is confidence-weighted (num/den), so weights are relative importances
    # and need NOT sum to 1 — every declared component just carries a weight.
    assert all(w > 0 for w in AGG_WEIGHTS.values())
    assert "rejection" in AGG_WEIGHTS
    # every declared aggression sub-signal carries a weight (pins the vocabulary).
    assert set(AGG_WEIGHTS) == {"effort", "skew", "flow", "order_flow",
                                "rejection", "option_flow"}
    assert AGG_WEIGHTS["option_flow"] == 0.10


def test_rejection_component_participates():
    # present + confident -> a negative rejection reading drags the blend down.
    with_rej, _ = blend_aggression(
        {"effort": 0.5, "rejection": -0.8},
        {"effort": 1.0, "rejection": 1.0})
    without_rej, _ = blend_aggression({"effort": 0.5}, {"effort": 1.0})
    assert with_rej < without_rej


def test_rejection_drops_out_at_zero_confidence():
    # absent / conf 0 -> rejection contributes nothing (blend == effort-only).
    dropped, _ = blend_aggression(
        {"effort": 0.5, "rejection": -0.8},
        {"effort": 1.0, "rejection": 0.0})
    effort_only, _ = blend_aggression({"effort": 0.5}, {"effort": 1.0})
    assert dropped == effort_only


# ── the aggregate confidence must be a confidence (2026-08-20) ──────────────

def test_blend_aggression_confidence_never_exceeds_one():
    """AGG_WEIGHTS sums to 1.30 -- `rejection` and `option_flow` were added
    without rebalancing -- and the returned "aggregate confidence" was the raw
    weighted sum, so a fully-confident read published 1.3. It is stored in
    market_state_history_db and consumed as a [0,1] confidence everywhere else.

    The SCORE was always fine (it divides by the same sum); only the confidence
    escaped its range.
    """
    names = list(aggression.AGG_WEIGHTS)
    full = aggression.blend_aggression({n: 1.0 for n in names},
                                       {n: 1.0 for n in names})
    assert full[1] == 1.0


def test_blend_aggression_confidence_is_the_present_weight_share():
    """Half the weight present at full confidence -> ~half confidence."""
    conf = {"effort": 1.0, "skew": 1.0}          # 0.35 + 0.30 of 1.30
    score, c = aggression.blend_aggression({"effort": 1.0, "skew": 1.0}, conf)
    assert c == pytest.approx((0.35 + 0.30) / 1.30, abs=5e-4)
    assert score == pytest.approx(1.0)           # score unchanged by the fix


def test_blend_aggression_score_is_unchanged_by_the_confidence_fix():
    """Regression pin: normalizing the confidence must not move the score, which
    already divided by the same weight sum."""
    comps = {"effort": 0.8, "skew": -0.4, "flow": 0.2, "order_flow": 1.0,
             "rejection": -0.6, "option_flow": 0.5}
    confs = {"effort": 1.0, "skew": 0.5, "flow": 0.8, "order_flow": 0.3,
             "rejection": 1.0, "option_flow": 0.6}
    num = sum(aggression.AGG_WEIGHTS[k] * comps[k] * confs[k] for k in comps)
    den = sum(aggression.AGG_WEIGHTS[k] * confs[k] for k in comps)
    assert aggression.blend_aggression(comps, confs)[0] == pytest.approx(
        round(num / den, 3), abs=1e-9)


# --- non-finite inputs must be MISSING, never maximum ------------------------
# `float(nan or 0.0)` is nan (NaN is truthy), so a single broken sub-signal
# propagated into num/den; `den <= 0` does not catch it (nan <= 0 is False); and
# `clamp(nan, -1, 1)` returns +1.0 - the documented pins-the-bound class. Measured
# before the fix: a NaN component gave (1.0, 0.5) and a NaN CONFIDENCE gave
# (1.0, 1.0) - maximum bullish aggression at maximum confidence, from no data.
#
# This value is stored in market_state_history_db, feeds the five-state
# classifier's aggression axis, and drives the state-transition phone alert.

NAN = float("nan")


def test_a_non_finite_component_drops_out_rather_than_pinning_the_max():
    score, conf = blend_aggression({"effort": NAN, "skew": -0.5},
                                   {"effort": 1.0, "skew": 1.0})
    assert score < 0, f"skew was the only real reading and it was negative; got {score}"
    assert score == blend_aggression({"skew": -0.5}, {"skew": 1.0})[0]


def test_a_non_finite_confidence_drops_out():
    assert blend_aggression({"effort": 0.5}, {"effort": NAN}) == (0.0, 0.0)


def test_an_infinite_component_is_missing_not_maximum():
    assert blend_aggression({"effort": float("inf")}, {"effort": 1.0}) == (0.0, 0.0)
    assert blend_aggression({"effort": float("-inf")}, {"effort": 1.0}) == (0.0, 0.0)


def test_a_broken_signal_does_not_consume_weight():
    """Dropping out means the confidence reflects what actually reported - a
    broken sub-signal must not inflate the aggregate confidence."""
    _, conf_broken = blend_aggression({"effort": NAN, "skew": 0.5},
                                      {"effort": 1.0, "skew": 1.0})
    _, conf_clean = blend_aggression({"skew": 0.5}, {"skew": 1.0})
    assert conf_broken == conf_clean


def test_every_input_broken_is_neutral_at_zero_confidence():
    assert blend_aggression({"effort": NAN, "skew": NAN},
                            {"effort": NAN, "skew": NAN}) == (0.0, 0.0)


def test_clean_inputs_are_unchanged():
    """Power check: the fix must not move a normal read."""
    assert blend_aggression({"effort": 0.5}, {"effort": 1.0}) == (0.5, 0.269)
