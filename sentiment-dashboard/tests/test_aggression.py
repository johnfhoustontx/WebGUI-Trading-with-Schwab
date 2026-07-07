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
    # only skew's weight participates -> aggregate confidence == its weight
    assert c == round(AGG_WEIGHTS["skew"], 3)


def test_weights_need_not_sum_to_one():
    # blend is confidence-weighted (num/den), so weights are relative importances
    # and need NOT sum to 1 — every declared component just carries a weight.
    assert all(w > 0 for w in AGG_WEIGHTS.values())
    assert "rejection" in AGG_WEIGHTS


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
