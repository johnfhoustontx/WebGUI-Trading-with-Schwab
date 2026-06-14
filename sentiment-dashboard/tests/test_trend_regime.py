"""Tests for scoring.trend_regime."""
from scoring import trend_regime


def test_state_catalog_has_all_five_states():
    """The five contract state strings each have a label + description."""
    expected = {"bull_trend", "pullback_in_bull", "range",
                "bear_rally", "bear_trend"}
    assert set(trend_regime.STATE_LABELS.keys()) == expected
    assert set(trend_regime.STATE_DESCRIPTIONS.keys()) == expected
    # No empty strings.
    assert all(trend_regime.STATE_LABELS.values())
    assert all(trend_regime.STATE_DESCRIPTIONS.values())


def test_result_dataclass_is_frozen():
    """TrendRegimeResult is immutable per package invariant."""
    import dataclasses
    assert dataclasses.is_dataclass(trend_regime.TrendRegimeResult)
    r = trend_regime.TrendRegimeResult(
        state="range", label="Range", description="x",
        spy_close=0.0, sma_50=0.0, sma_200=0.0,
        sma_200_slope_pct=0.0, drawdown_pct=0.0, confidence=0.0)
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.state = "bull_trend"


def test_classify_empty_history():
    r = trend_regime.classify([])
    assert r.state == "range"
    assert r.confidence == 0.0


def test_classify_below_partial_threshold():
    r = trend_regime.classify([100.0] * 30)
    assert r.state == "range"
    assert r.confidence == 0.0


def test_classify_partial_history():
    """50 ≤ bars < 200 → range with 0.5 confidence (can't trust 200-DMA)."""
    r = trend_regime.classify([100.0] * 100)
    assert r.state == "range"
    assert r.confidence == 0.5


def _make_series(values, length=210):
    """Pad ``values`` (latest-last) to ``length`` by prepending the head."""
    if len(values) >= length:
        return list(values)
    pad = [values[0]] * (length - len(values))
    return pad + list(values)


def test_bull_trend_healthy_uptrend():
    # Smooth uptrend. Need >=220 bars so the 20-bar SMA200 slope lookback
    # has data: 220 ramping bars + 10 climbing tail = 230 total.
    base = [540.0 + 0.14 * i for i in range(220)]
    tail = [595.0 + 1.7 * i for i in range(10)]
    closes = base + tail
    r = trend_regime.classify(closes)
    assert r.state == "bull_trend"
    assert r.confidence == 1.0
    assert r.spy_close == closes[-1]
    assert r.sma_200_slope_pct > trend_regime.SLOPE_BULL_MIN


def test_pullback_in_bull():
    # Rising 200-DMA, but recent dip put close below sma50.
    # 215 ramp + 10-bar dip = 225 bars (>=220 for slope window).
    base = [540.0 + 0.14 * i for i in range(215)]
    dip = [605.0, 595.0, 585.0, 575.0, 565.0,
           560.0, 555.0, 552.0, 550.0, 548.0]
    closes = base + dip
    r = trend_regime.classify(closes)
    assert r.state == "pullback_in_bull"


def test_range():
    # Flat-ish: slope200 near zero, no MA stacking, moderate dd.
    rng = []
    val = 500.0
    import math
    for i in range(210):
        val += math.sin(i / 7) * 1.5
        rng.append(val)
    r = trend_regime.classify(rng)
    assert r.state == "range"
    assert abs(r.sma_200_slope_pct) < trend_regime.SLOPE_BULL_MIN


def test_bear_trend():
    # Long downtrend: close < sma50 < sma200, slope falling.
    # 230 bars so slope window has lookback data.
    closes = [600.0 - 0.25 * i for i in range(230)]
    r = trend_regime.classify(closes)
    assert r.state == "bear_trend"
    assert r.sma_200_slope_pct < trend_regime.SLOPE_BEAR_MAX


def test_bear_rally():
    # Falling slope200 but recent bounce put close above sma50, dd still deep.
    # 220 declining + 10 bounce = 230 bars.
    decline = [600.0 - 0.5 * i for i in range(220)]
    bounce = [502.0, 508.0, 514.0, 520.0, 525.0,
              528.0, 530.0, 532.0, 534.0, 536.0]
    closes = decline + bounce
    r = trend_regime.classify(closes)
    assert r.state == "bear_rally"
    assert r.drawdown_pct < trend_regime.BEAR_RALLY_DD_MIN


def test_result_carries_label_and_description():
    closes = [540.0 + 0.14 * i for i in range(220)] + \
             [595.0 + 1.7 * i for i in range(10)]
    r = trend_regime.classify(closes)
    assert r.label == trend_regime.STATE_LABELS[r.state]
    assert r.description == trend_regime.STATE_DESCRIPTIONS[r.state]


def test_commit_cold_start():
    """Empty history → committed = raw, no warmup gate."""
    committed, hist = trend_regime.commit_state(
        raw="bull_trend", history=[], prev_committed=None)
    assert committed == "bull_trend"
    assert hist == ["bull_trend"]


def test_commit_one_day_flip_does_not_commit():
    committed, hist = trend_regime.commit_state(
        raw="range", history=["bull_trend"], prev_committed="bull_trend")
    assert committed == "bull_trend"
    assert hist == ["bull_trend", "range"]


def test_commit_two_day_flip_commits():
    """Two consecutive matching raws differ from prev → commit."""
    committed, hist = trend_regime.commit_state(
        raw="range",
        history=["bull_trend", "range"],
        prev_committed="bull_trend")
    assert committed == "range"
    assert hist == ["bull_trend", "range", "range"][-trend_regime.HYSTERESIS_DAYS:]


def test_commit_two_day_disagreement_does_not_commit():
    committed, hist = trend_regime.commit_state(
        raw="bear_trend",
        history=["bull_trend", "range"],
        prev_committed="bull_trend")
    assert committed == "bull_trend"


def test_history_trims_to_hysteresis_days():
    committed, hist = trend_regime.commit_state(
        raw="bull_trend",
        history=["range", "range", "bull_trend", "bull_trend"],
        prev_committed="range")
    assert len(hist) == trend_regime.HYSTERESIS_DAYS
    assert hist[-1] == "bull_trend"
