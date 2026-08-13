"""Momentum component math — one test per breakpoint in the plan."""
import math

import pytest

from scoring import momentum


def _exp_series(n, daily_rate, start=100.0):
    return [start * (1.0 + daily_rate) ** i for i in range(n)]


# --- weights invariant ------------------------------------------------------

def test_momentum_weights_sum_to_one():
    assert abs(sum(momentum.MOMENTUM_WEIGHTS.values()) - 1.0) < 1e-9


# --- trend_strength ---------------------------------------------------------

def test_trend_strength_recovers_annualized_rate_on_clean_exponential():
    daily = 0.001
    closes = _exp_series(120, daily)
    expected = (1.0 + daily) ** 252 - 1.0

    got = momentum.trend_strength(closes, n=90)

    # R^2 is 1.0 on a noiseless log-linear series, so the product is the rate.
    assert got == pytest.approx(expected, rel=1e-6)


def test_trend_strength_penalizes_noise_but_keeps_sign():
    daily = 0.001
    clean = _exp_series(120, daily)
    noisy = [c * (1.0 + (0.05 if i % 2 else -0.05)) for i, c in enumerate(clean)]

    clean_score = momentum.trend_strength(clean, n=90)
    noisy_score = momentum.trend_strength(noisy, n=90)

    assert noisy_score > 0
    assert noisy_score < clean_score * 0.9


def test_trend_strength_returns_none_when_too_few_bars():
    assert momentum.trend_strength(_exp_series(89, 0.001), n=90) is None


# --- relative_strength ------------------------------------------------------

def test_relative_strength_against_itself_is_flat():
    closes = _exp_series(80, 0.002)

    excess, slope = momentum.relative_strength(closes, closes, n=63)

    assert excess == pytest.approx(0.0, abs=1e-12)
    assert slope == pytest.approx(0.0, abs=1e-9)


def test_relative_strength_positive_when_outrunning_benchmark():
    symbol = _exp_series(80, 0.003)
    bench = _exp_series(80, 0.001)

    excess, slope = momentum.relative_strength(symbol, bench, n=63)

    assert excess > 0
    assert slope > 0


def test_relative_strength_returns_none_pair_when_short():
    assert momentum.relative_strength(_exp_series(10, 0.001),
                                      _exp_series(10, 0.001), n=63) == (None, None)


# --- acceleration -----------------------------------------------------------

def test_acceleration_negative_when_recent_leg_goes_flat():
    # Strong 63d advance, then the last 21 bars stall — the decelerating case.
    rising = _exp_series(64, 0.004)
    closes = rising + [rising[-1]] * 21

    assert momentum.acceleration(closes) < 0


def test_acceleration_positive_when_recent_leg_leads():
    flat = [100.0] * 64
    closes = flat + _exp_series(21, 0.01, start=100.0)

    assert momentum.acceleration(closes) > 0


def test_acceleration_returns_none_when_too_few_bars():
    assert momentum.acceleration([100.0] * 60) is None


# --- path_quality -----------------------------------------------------------

def test_path_quality_prefers_monotonic_riser_over_sawtooth():
    smooth = _exp_series(64, 0.002)
    # Same start and end, reached by alternating up/down legs.
    total = smooth[-1] / smooth[0]
    saw = []
    for i in range(64):
        base = smooth[0] * total ** (i / 63.0)
        saw.append(base * (1.03 if i % 2 else 0.97))
    saw[0], saw[-1] = smooth[0], smooth[-1]

    assert momentum.path_quality(smooth, n=63) > momentum.path_quality(saw, n=63)


def test_path_quality_returns_none_when_too_few_bars():
    assert momentum.path_quality([100.0] * 10, n=63) is None


# --- participation ----------------------------------------------------------

def test_participation_is_fraction_above_own_50_dma():
    above = _exp_series(60, 0.004)                    # rising -> above its 50 DMA
    below = _exp_series(60, -0.004)                   # falling -> below
    closes = [above, above, above, below, below]

    assert momentum.participation(closes) == pytest.approx(0.6)


def test_participation_returns_none_on_empty_input():
    assert momentum.participation([]) is None


def test_participation_skips_constituents_without_enough_history():
    above = _exp_series(60, 0.004)
    assert momentum.participation([above, [100.0] * 5]) == pytest.approx(1.0)


# --- zscore_within_level ----------------------------------------------------

def test_zscore_keeps_none_slots_and_scores_the_rest():
    out = momentum.zscore_within_level([1.0, None, 3.0])

    assert out[1] is None
    assert out[0] == pytest.approx(-1.0)
    assert out[2] == pytest.approx(1.0)


def test_zscore_of_identical_values_is_zero_not_a_divide_by_zero():
    assert momentum.zscore_within_level([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]


def test_zscore_clips_to_three_sigma():
    values = [0.0] * 20 + [1000.0]

    assert max(v for v in momentum.zscore_within_level(values)) == pytest.approx(3.0)


# --- blend ------------------------------------------------------------------

def test_blend_renormalizes_over_present_components():
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}

    got = momentum.blend({"a": 2.0, "b": 1.0}, weights)

    assert got == pytest.approx((0.5 * 2.0 + 0.3 * 1.0) / 0.8)


def test_blend_ignores_none_components():
    weights = {"a": 0.5, "b": 0.5}

    assert momentum.blend({"a": 2.0, "b": None}, weights) == pytest.approx(2.0)


def test_blend_returns_none_when_nothing_present():
    assert momentum.blend({"a": None}, {"a": 1.0}) is None


# --- percentile_rank --------------------------------------------------------

def test_percentile_rank_orders_within_level():
    out = momentum.percentile_rank([10.0, 20.0, 30.0, 40.0])

    assert out == sorted(out)
    assert all(0.0 <= p <= 100.0 for p in out)
    assert out[0] < 50.0 < out[-1]


def test_percentile_rank_keeps_none_slots():
    out = momentum.percentile_rank([1.0, None, 2.0])

    assert out[1] is None
    assert out[0] < out[2]


def test_percentile_rank_of_ties_is_the_same_value():
    out = momentum.percentile_rank([7.0, 7.0, 7.0])

    assert out[0] == out[1] == out[2] == pytest.approx(50.0)


def test_every_component_returns_none_never_raises_on_garbage():
    # Degrade, never raise — the house rule for every compute in this stack.
    assert momentum.trend_strength([], n=90) is None
    assert momentum.acceleration([]) is None
    assert momentum.path_quality([], n=63) is None
    assert momentum.participation([[]]) is None
    assert momentum.zscore_within_level([]) == []
    assert momentum.percentile_rank([]) == []
    assert not math.isnan(momentum.blend({"a": 1.0}, {"a": 1.0}))
