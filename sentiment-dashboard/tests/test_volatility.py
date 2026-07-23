"""Tests for scoring/volatility.py — pure ATR + Bollinger-width helpers."""
import numpy as np

from scoring import volatility as V


def test_atr_matches_wilder_hand_calc():
    h = [12, 12.5, 13, 12.8, 13.2]
    l = [11, 11.5, 12, 12.2, 12.6]  # noqa: E741 - conventional OHLC name
    c = [11.5, 12.2, 12.5, 12.6, 13.0]
    # TR = [1.0, 1.0, 1.0, 0.6, 0.6]; ATR-3: seed mean(1,1,1)=1.0;
    # (1.0*2+0.6)/3=0.86667; (0.86667*2+0.6)/3=0.77778
    atr = V.atr(h, l, c, n=3)
    assert abs(atr - 0.77778) < 1e-4


def test_atr_insufficient_bars_returns_none():
    assert V.atr([1, 2], [1, 2], [1, 2], n=14) is None


def test_atr_accepts_ndarrays():
    h = np.array([12, 12.5, 13, 12.8, 13.2])
    l = np.array([11, 11.5, 12, 12.2, 12.6])  # noqa: E741
    c = np.array([11.5, 12.2, 12.5, 12.6, 13.0])
    assert V.atr(h, l, c, n=3) is not None


def test_bollinger_width_pct_zero_variance_is_zero():
    assert V.bollinger_width_pct([100.0] * 20, n=20) == 0.0


def test_bollinger_width_pct_positive_for_dispersed_closes():
    closes = list(np.linspace(95, 105, 20))
    assert V.bollinger_width_pct(closes, n=20) > 0


def test_bollinger_width_pct_thin_returns_none():
    assert V.bollinger_width_pct([100.0] * 5, n=20) is None


def test_percentile_of_last():
    hist = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10.0]
    assert V.percentile_of_last(hist + [10.5]) == 1.0
    assert abs(V.percentile_of_last(hist + [5.5]) - 0.5) < 0.1
    assert V.percentile_of_last([1.0]) is None


def test_percentile_of_last_filters_none():
    vals = [1, None, 2, 3, None, 4, 5, 6, 7, 8, 9, 10.0, 5.5]
    p = V.percentile_of_last(vals)
    assert p is not None and 0.3 < p < 0.7
