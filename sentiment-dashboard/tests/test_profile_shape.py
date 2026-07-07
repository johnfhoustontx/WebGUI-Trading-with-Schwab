"""Tests for scoring/profile_shape.py — volume-by-price shape classifier (NEUTRAL).

The distribution factories GENUINELY place volume-by-price (a central bell, a
one-sided ramp, two separated nodes) so each test proves the classification,
not reverse-engineered numbers.
"""
import math

import dataclasses

import pytest

from scoring.profile_shape import classify_profile_shape, ProfileShape


def _bar(price, vol):
    """A tight bar whose typical price ((h+l+c)/3) equals `price`."""
    return {"high": price + 0.1, "low": price - 0.1, "close": price,
            "volume": float(vol)}


def _bell(center=100.0, sigma=2.2, span=10):
    """Single central bell: volume peaks at `center` and falls off symmetrically."""
    bars = []
    for off in range(-span, span + 1):
        vol = 100.0 * math.exp(-(off * off) / (2 * sigma * sigma))
        bars.append(_bar(center + off, vol))
    return bars


def _one_sided_ramp():
    """Volume climbs monotonically toward the high extreme -> trend/one-sided."""
    bars = []
    for i in range(21):
        price = 90.0 + i
        bars.append(_bar(price, (i + 1) * 10.0))
    return bars


def _double_node():
    """Two separated high-volume nodes (~95 and ~105) with a low valley at 100."""
    bars = []
    for off in range(-10, 11):
        price = 100.0 + off
        g1 = 100.0 * math.exp(-((off + 5) ** 2) / (2 * 1.6 ** 2))
        g2 = 100.0 * math.exp(-((off - 5) ** 2) / (2 * 1.6 ** 2))
        vol = max(4.0, g1 + g2)     # small floor so no empty bins
        bars.append(_bar(price, vol))
    return bars


# --- structural -------------------------------------------------------------

def test_result_is_frozen():
    r = classify_profile_shape(_bell())
    assert isinstance(r, ProfileShape)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.shape = "trend"


def test_fields_types_and_bounds():
    r = classify_profile_shape(_bell())
    assert r.shape in ("balance", "trend", "double")
    assert 0.0 <= r.balance_strength <= 1.0
    assert r.neutral_signal == r.balance_strength
    assert isinstance(r.balance_strength, float)


# --- the spec's cases -------------------------------------------------------

def test_central_bell_is_balance_with_high_strength():
    r = classify_profile_shape(_bell())
    assert r.shape == "balance"
    assert r.balance_strength > 0.5


def test_one_sided_ramp_is_not_balance_low_strength():
    r = classify_profile_shape(_one_sided_ramp())
    assert r.shape != "balance"
    assert r.balance_strength < 0.3


def test_two_nodes_classified_double():
    r = classify_profile_shape(_double_node())
    assert r.shape == "double"
    assert r.shape != "balance"


def test_bell_beats_ramp_on_strength():
    assert (classify_profile_shape(_bell()).balance_strength
            > classify_profile_shape(_one_sided_ramp()).balance_strength)


# --- edges ------------------------------------------------------------------

def test_empty_is_default_zero_strength():
    r = classify_profile_shape([])
    assert r.shape == "trend"
    assert r.balance_strength == 0.0
    assert r.neutral_signal == 0.0


def test_zero_total_volume_is_default():
    bars = [_bar(100 + i, 0.0) for i in range(10)]
    r = classify_profile_shape(bars)
    assert r.shape == "trend"
    assert r.balance_strength == 0.0


def test_fewer_than_two_distinct_prices_is_default():
    bars = [_bar(100.0, 50.0) for _ in range(10)]   # all same typical price
    r = classify_profile_shape(bars)
    assert r.shape == "trend"
    assert r.balance_strength == 0.0


def test_missing_keys_do_not_raise():
    bars = _bell()
    bars[3] = {"high": 100.0}       # missing low/close/volume -> dropped
    assert isinstance(classify_profile_shape(bars), ProfileShape)


def test_never_raises_on_garbage():
    assert isinstance(classify_profile_shape([{}, {"volume": "x"}, 5]), ProfileShape)
