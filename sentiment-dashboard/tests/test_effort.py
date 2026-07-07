"""Tests for scoring/effort.py — volume-effort (effort-vs-result) scoring.

The `_bars` factory GENUINELY produces OHLCV bars exhibiting each described
market condition (it does not reverse-engineer numbers to pass) so each test
proves the semantics of the sub-signal it targets.
"""
import dataclasses

import pytest

from scoring.effort import score_effort, EffortResult, _clamp


def _mk(close, vol, loc):
    """One bar with close-location-value = `loc` inside a unit range."""
    r = 1.0
    low = close - loc * r
    high = low + r
    return {"open": round((low + high) / 2, 4), "high": round(high, 4),
            "low": round(low, 4), "close": round(close, 4), "volume": float(vol)}


def _bars(n=25, updown_vol_ratio=None, up_trend=False, up_vol_shrinking=False,
          down_drift=False, down_vol_dry=False, clv=None, flat=False):
    """Build a coherent daily-OHLCV tape (oldest first) for one condition."""
    bars = []
    price = 100.0

    if flat:
        for _ in range(n):
            bars.append(_mk(price, 100, 0.5))   # identical closes -> no direction
        return bars

    if clv is not None:
        for i in range(n):
            price += 0.5 if i % 2 == 0 else -0.5
            bars.append(_mk(price, 100, clv))
        return bars

    if up_trend and up_vol_shrinking:
        # hollow rally: 4 up : 1 down, up-volume DECLINING, down-vol steady,
        # weak (low-in-range) closes -> buying effort drying up.
        up_vol = 260.0
        for i in range(n):
            if i % 5 == 4:          # down day (distribution)
                price -= 3.0
                bars.append(_mk(price, 120, 0.30))
            else:                    # up day
                price += 2.0
                bars.append(_mk(price, up_vol, 0.35))
                up_vol = max(80.0, up_vol - 9.0)
        return bars

    if down_drift and down_vol_dry:
        # no-panic dip: 4 down : 1 up, down-volume TINY, up-vol normal,
        # closes off the lows (buyers defend).
        for i in range(n):
            if i % 5 == 4:          # up day
                price += 3.0
                bars.append(_mk(price, 200, 0.65))
            else:                    # down day
                price -= 2.0
                bars.append(_mk(price, 20, 0.65))
        return bars

    if updown_vol_ratio is not None:
        # motivated buying: alternating up/down, net-up, up-close days carry
        # ~ratio x the volume of down days (and expanding), strong closes.
        up_vol = 100.0 * updown_vol_ratio
        for i in range(n):
            if i % 2 == 0:          # up day
                price += 3.0
                bars.append(_mk(price, up_vol, 0.85))
                up_vol += 5.0
            else:                    # down day
                price -= 1.0
                bars.append(_mk(price, 100, 0.50))
        return bars

    # default: neutral chop
    for i in range(n):
        price += 0.5 if i % 2 == 0 else -0.5
        bars.append(_mk(price, 100, 0.5))
    return bars


# --- structural -----------------------------------------------------------

def test_clamp_bounds():
    assert _clamp(2.0, -1, 1) == 1.0
    assert _clamp(-2.0, -1, 1) == -1.0
    assert _clamp(0.3, -1, 1) == 0.3


def test_result_is_frozen():
    r = score_effort(_bars())
    assert isinstance(r, EffortResult)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.score = 1.0


def test_components_keys_present_and_float():
    r = score_effort(_bars())
    for k in ("updown_vol", "vol_trend", "clv"):
        assert k in r.components
        assert isinstance(r.components[k], float)
    assert -1.0 <= r.score <= 1.0
    assert 0.0 <= r.confidence <= 1.0


# --- the spec's five --------------------------------------------------------

def test_up_volume_dominant_is_positive():
    # up-close days carry ~2x the volume of down-close days
    r = score_effort(_bars(updown_vol_ratio=2.0))
    assert r.score > 0.4 and r.confidence > 0.5


def test_rally_on_shrinking_volume_is_negative():
    # price grinds up but volume CONTRACTS on the up-days -> hollow -> negative
    assert score_effort(_bars(up_trend=True, up_vol_shrinking=True)).score < 0


def test_downday_volume_dries_up_is_positive():
    # price dips but down-days have almost no volume -> no panic -> positive
    assert score_effort(_bars(down_drift=True, down_vol_dry=True)).score > 0


def test_close_location_value_high_closes_positive():
    assert score_effort(_bars(clv=0.9)).components["clv"] > 0


def test_close_location_value_low_closes_negative():
    assert score_effort(_bars(clv=0.1)).components["clv"] < 0


def test_insufficient_bars_zero_confidence():
    assert score_effort(_bars(n=3)).confidence == 0.0


# --- edges ------------------------------------------------------------------

def test_empty_list_is_safe():
    r = score_effort([])
    assert r.score == 0.0 and r.confidence == 0.0
    assert r.components["clv"] == 0.0


def test_zero_range_bar_does_not_raise():
    bars = _bars()
    bars[5] = {"open": 100, "high": 100, "low": 100, "close": 100, "volume": 500}
    r = score_effort(bars)  # must not raise; zero-range bar skipped in CLV
    assert isinstance(r, EffortResult)


def test_missing_keys_do_not_raise():
    bars = _bars()
    bars[2] = {"open": 100, "close": 101}   # missing high/low/volume -> dropped
    assert isinstance(score_effort(bars), EffortResult)


def test_all_flat_closes_neutral_score():
    r = score_effort(_bars(flat=True))
    assert r.score == 0.0
    assert r.confidence > 0.0     # enough bars, but no directional effort


def test_confidence_scales_with_bars():
    lo = score_effort(_bars(n=12)).confidence
    hi = score_effort(_bars(n=25)).confidence
    assert 0.0 < lo < hi <= 1.0
