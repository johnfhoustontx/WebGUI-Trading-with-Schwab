"""Tests for scoring/rejection_defense.py — rejection/defense (AGGRESSION) scoring.

The bar factories GENUINELY build daily tapes with the described wick/close
behaviour (upper-wick exhaustion at highs, defended dips, dojis) so each test
proves the semantics, not reverse-engineered numbers.
"""
import dataclasses

import pytest

from scoring.rejection_defense import (
    score_rejection_defense,
    RejectionDefense,
    _clamp,
)


def _bar(o, h, l, c):
    return {"open": float(o), "high": float(h), "low": float(l), "close": float(c)}


def _upper_wick_at_highs(n=15):
    """Tape grinds up to new highs but each near-high bar prints a long UPPER
    wick and closes weak (in the lower part of its range) -> supply/exhaustion."""
    bars = []
    price = 100.0
    for _ in range(n):
        price += 1.0
        o = price
        l = price - 0.2
        h = price + 3.0            # long upper wick (rejected higher)
        c = price + 0.2            # closes weak, near the open, far below high
        bars.append(_bar(o, h, l, c))
    return bars


def _defended_dips(n=15):
    """Tape sells into lows each bar but prints long LOWER wicks and closes back
    in the upper half of its range -> buyers defend, resilience."""
    bars = []
    price = 100.0
    for _ in range(n):
        price -= 1.0
        o = price
        h = price + 0.2
        l = price - 3.0            # long lower wick (dip bought)
        c = price + 0.1            # closes back near the top of the range
        bars.append(_bar(o, h, l, c))
    return bars


def _dojis(n=15):
    """Balanced doji tape: symmetric wicks, closes mid-range -> ~neutral."""
    bars = []
    price = 100.0
    for i in range(n):
        price += 0.25 if i % 2 == 0 else -0.25
        o = price
        c = price
        h = price + 1.0
        l = price - 1.0
        bars.append(_bar(o, h, l, c))
    return bars


# --- structural -------------------------------------------------------------

def test_clamp_bounds():
    assert _clamp(2.0, -1, 1) == 1.0
    assert _clamp(-2.0, -1, 1) == -1.0


def test_result_is_frozen():
    r = score_rejection_defense(_defended_dips())
    assert isinstance(r, RejectionDefense)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.score = 1.0


def test_score_and_confidence_bounds():
    r = score_rejection_defense(_defended_dips())
    assert -1.0 <= r.score <= 1.0
    assert 0.0 <= r.confidence <= 1.0
    assert isinstance(r.score, float) and isinstance(r.confidence, float)


# --- the spec's cases -------------------------------------------------------

def test_upper_wicks_at_highs_is_negative():
    assert score_rejection_defense(_upper_wick_at_highs()).score < -0.2


def test_defended_dips_is_positive():
    assert score_rejection_defense(_defended_dips()).score > 0.2


def test_doji_tape_is_near_zero():
    assert abs(score_rejection_defense(_dojis()).score) < 0.2


def test_fewer_than_five_bars_zero_confidence():
    assert score_rejection_defense(_defended_dips(n=3)).confidence == 0.0


def test_empty_list_is_safe():
    r = score_rejection_defense([])
    assert r.score == 0.0 and r.confidence == 0.0


def test_high_equals_low_bar_skipped():
    bars = _defended_dips()
    bars[4] = {"open": 100, "high": 100, "low": 100, "close": 100}  # zero-range
    r = score_rejection_defense(bars)
    assert isinstance(r, RejectionDefense)
    assert -1.0 <= r.score <= 1.0


def test_missing_keys_do_not_raise():
    bars = _defended_dips()
    bars[2] = {"open": 100, "close": 101}   # missing high/low -> dropped
    assert isinstance(score_rejection_defense(bars), RejectionDefense)


def test_confidence_scales_with_bars():
    lo = score_rejection_defense(_defended_dips(n=7)).confidence
    hi = score_rejection_defense(_defended_dips(n=15)).confidence
    assert 0.0 < lo < hi <= 1.0


def test_num_rejects_non_finite_values():
    """`_num` is the parsing guard: a NaN or inf is not a usable number and must
    read as missing, so `_clean` drops the bar. Without this a single NaN field
    reached `_clamp`, which returns its HIGH bound for NaN -- one bad bar pinned
    the sub-signal to its maximum at UNCHANGED confidence. Mirrors the sibling
    guard in scoring/order_flow.py.
    """
    from math import inf, nan
    from scoring.rejection_defense import _num
    assert _num(nan) is None
    assert _num(inf) is None and _num(-inf) is None
    assert _num("3.5") == 3.5 and _num(2) == 2.0 and _num(None) is None
