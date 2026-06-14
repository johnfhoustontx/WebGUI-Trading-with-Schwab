"""Tests for the IV-percentile and realized-vol-trend helpers (pure)."""
import pytest

from iv_percentile import percentile_rank, realized_vol_trend


def test_percentile_rank_latest_at_top():
    assert percentile_rank([10, 12, 14, 16, 20], 20) == pytest.approx(100.0)


def test_percentile_rank_counts_at_or_below():
    # 3 of 4 values (10,12,14) are <= 14 -> 75%.
    assert percentile_rank([10, 12, 14, 16], 14) == pytest.approx(75.0)


def test_percentile_rank_empty_is_none():
    assert percentile_rank([], 5) is None


def _candles(closes):
    return [{"datetime": i, "close": c} for i, c in enumerate(closes)]


def test_realized_vol_trend_falling():
    # Calm, then a volatile patch, then calm into the END -> rolling RV is
    # falling at the last reading vs a few sessions earlier.
    calm = [100 + 0.1 * i for i in range(30)]
    volatile = [130, 80, 135, 85, 140, 90]
    calm_tail = [110 + 0.1 * i for i in range(8)]
    trend = realized_vol_trend(_candles(calm + volatile + calm_tail),
                               window=5, lookback=3)
    assert trend["falling"] is True
    assert trend["value"] is not None


def test_realized_vol_trend_rising():
    # Calm, then a volatile patch at the END -> rolling RV is rising.
    calm = [100 + 0.1 * i for i in range(40)]
    volatile = [100, 130, 90, 140, 80, 150]
    trend = realized_vol_trend(_candles(calm + volatile), window=5, lookback=3)
    assert trend["falling"] is False


def test_realized_vol_trend_too_short():
    trend = realized_vol_trend(_candles([100, 101, 102]), window=5, lookback=3)
    assert trend == {"value": None, "falling": None}
