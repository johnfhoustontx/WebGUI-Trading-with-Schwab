from scoring.market_state import classify_market_state, STATE_LABELS


def test_bullish():
    assert classify_market_state(75, 0.6).state == "bullish"


def test_lack_of_bullishness_up_but_hollow():
    assert classify_market_state(72, -0.5).state == "lack_of_bullishness"


def test_lack_of_bullishness_up_but_weak_effort():
    assert classify_market_state(72, 0.0).state == "lack_of_bullishness"


def test_neutral_balance():
    assert classify_market_state(50, 0.0).state == "neutral"


def test_lack_of_bearishness_down_but_no_followthrough():
    assert classify_market_state(35, 0.4).state == "lack_of_bearishness"


def test_lack_of_bearishness_down_but_no_urgency():
    assert classify_market_state(35, 0.0).state == "lack_of_bearishness"


def test_bearish():
    assert classify_market_state(22, -0.6).state == "bearish"


def test_neutral_with_selling_is_lack_of_bullishness():
    assert classify_market_state(50, -0.5).state == "lack_of_bullishness"


def test_evidence_passthrough_and_labels():
    s = classify_market_state(75, 0.6, evidence=["up-volume +40% vs 20d"])
    assert s.label == STATE_LABELS["bullish"] and "up-volume +40% vs 20d" in s.evidence


def test_five_labels_exact():
    assert set(STATE_LABELS) == {
        "bullish", "lack_of_bullishness", "neutral", "lack_of_bearishness", "bearish"}


def test_boundary_bull_min_inclusive():
    assert classify_market_state(60, 0.5).state == "bullish"     # >= 60 is bullish


def test_defensive_none_inputs():
    # must not raise; None dir/agg -> neutral-ish
    classify_market_state(None, None)
