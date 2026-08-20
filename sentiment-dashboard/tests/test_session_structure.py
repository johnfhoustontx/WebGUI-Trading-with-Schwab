"""Tests for scoring/session_structure.py — session-structure (DIRECTION) scoring.

The bar factories GENUINELY produce intraday tapes exhibiting each described
condition (holding above VWAP + OR break, pinned below, chop) so each test
proves the semantics, not reverse-engineered numbers.
"""
import dataclasses

import pytest

from scoring.session_structure import (
    score_session_structure,
    SessionStructure,
    _clamp,
)


def _bar(h, l, c, v=100.0):
    return {"high": float(h), "low": float(l), "close": float(c), "volume": float(v)}


def _rising_above_vwap(n=30):
    """Steady rally: each bar closes at its high, price climbing, so close is
    above the lagging cumulative VWAP and the latest close clears the OR high."""
    bars = []
    price = 100.0
    for _ in range(n):
        low = price
        high = price + 1.0
        close = high            # close at the high -> above VWAP, strong
        bars.append(_bar(high, low, close))
        price += 1.0
    return bars


def _falling_below_vwap(n=30):
    """Steady selloff: each bar closes at its low, price falling, so close is
    below the lagging cumulative VWAP and the latest close is under the OR low."""
    bars = []
    price = 200.0
    for _ in range(n):
        high = price
        low = price - 1.0
        close = low             # close at the low -> below VWAP, weak
        bars.append(_bar(high, low, close))
        price -= 1.0
    return bars


def _chop_around_vwap(n=30, or_bars=6):
    """Range chop: price oscillates in a tight band around a flat VWAP and the
    latest close sits inside the opening range -> ~neutral structure."""
    bars = []
    base = 100.0
    for i in range(n):
        c = base + (0.5 if i % 2 == 0 else -0.5)
        bars.append(_bar(base + 1.0, base - 1.0, c))
    return bars


# --- structural -------------------------------------------------------------

def test_clamp_bounds():
    assert _clamp(2.0, -1, 1) == 1.0
    assert _clamp(-2.0, -1, 1) == -1.0
    assert _clamp(0.3, -1, 1) == 0.3


def test_result_is_frozen():
    r = score_session_structure(_rising_above_vwap())
    assert isinstance(r, SessionStructure)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.score = 1.0


def test_score_and_confidence_bounds():
    r = score_session_structure(_rising_above_vwap())
    assert -1.0 <= r.score <= 1.0
    assert 0.0 <= r.confidence <= 1.0
    assert isinstance(r.score, float) and isinstance(r.confidence, float)


# --- the spec's cases -------------------------------------------------------

def test_above_vwap_and_broke_or_high_is_strongly_positive():
    r = score_session_structure(_rising_above_vwap())
    assert r.score > 0.7
    assert r.confidence == 1.0


def test_below_vwap_and_broke_or_low_is_strongly_negative():
    r = score_session_structure(_falling_below_vwap())
    assert r.score < -0.7
    assert r.confidence == 1.0


def test_chop_around_vwap_inside_or_is_near_zero():
    r = score_session_structure(_chop_around_vwap())
    assert abs(r.score) < 0.25


def test_fewer_than_or_bars_returns_zero_zero():
    r = score_session_structure(_rising_above_vwap(n=4), or_bars=6)
    assert r.score == 0.0 and r.confidence == 0.0


def test_empty_list_is_safe():
    r = score_session_structure([])
    assert r.score == 0.0 and r.confidence == 0.0


def test_zero_volume_bars_do_not_crash():
    bars = _rising_above_vwap()
    for b in bars:
        b["volume"] = 0.0
    r = score_session_structure(bars)     # must not raise / divide-by-zero
    assert isinstance(r, SessionStructure)
    assert -1.0 <= r.score <= 1.0


def test_confidence_scales_with_bars():
    lo = score_session_structure(_rising_above_vwap(n=15)).confidence
    hi = score_session_structure(_rising_above_vwap(n=30)).confidence
    assert 0.0 < lo < hi <= 1.0


def test_missing_keys_do_not_raise():
    bars = _rising_above_vwap()
    bars[3] = {"high": 100.0, "close": 100.5}   # missing low/volume -> dropped
    assert isinstance(score_session_structure(bars), SessionStructure)


def test_num_rejects_non_finite_values():
    """`_num` is the parsing guard: a NaN or inf is not a usable number and must
    read as missing, so `_clean` drops the bar. Without this a single NaN field
    reached `_clamp`, which returns its HIGH bound for NaN -- one bad bar pinned
    the sub-signal to its maximum at UNCHANGED confidence. Mirrors the sibling
    guard in scoring/order_flow.py.
    """
    from math import inf, nan
    from scoring.session_structure import _num
    assert _num(nan) is None
    assert _num(inf) is None and _num(-inf) is None
    assert _num("3.5") == 3.5 and _num(2) == 2.0 and _num(None) is None
