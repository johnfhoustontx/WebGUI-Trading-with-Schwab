"""Characterization + new-helper tests for shared/analysis_lib/technical.py.

These pin the numeric behavior of the indicators the trade service relies on, so
the vectorization refactor (EMA via ewm, single-pass MACD histogram, vectorized
volume profile) is proven equivalent. ``compute`` is imported first only to put
the standalone ``technical`` module on sys.path (same isolation the service uses).
"""
import numpy as np
import pandas as pd

from services.trade_svc import compute  # noqa: F401 — sets sys.path for `technical`
import technical


def test_calculate_ema_sma_seed_and_recurrence():
    df = pd.DataFrame({"close": [1, 2, 3, 4, 5, 6]})
    ema = technical.calculate_ema(df, 3)
    vals = ema.tolist()
    assert np.isnan(vals[0]) and np.isnan(vals[1])      # NaN before the seed
    assert vals[2:] == [2.0, 3.0, 4.0, 5.0]             # SMA seed then recurrence


def test_calculate_ema_too_short_returns_none():
    assert technical.calculate_ema(pd.DataFrame({"close": [1, 2]}), 3) is None


def test_volume_profile_matches_reference():
    rng = np.random.default_rng(11)
    n = 40
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame({"high": close + 0.7, "low": close - 0.7,
                       "close": close, "volume": rng.integers(100, 1000, n)})
    vp = technical.calculate_volume_profile(df)
    assert round(vp["poc"], 6) == 102.401128
    assert round(vp["vah"], 6) == 103.899749
    assert round(vp["val"], 6) == 95.407563


def test_macd_histogram_series_matches_double_compute():
    """The single-pass histogram series' last two values equal the old approach
    of calling calculate_macd on the full series and on series[:-1]."""
    rng = np.random.default_rng(11)
    df = pd.DataFrame({"close": 100 + np.cumsum(rng.normal(0, 1, 60))})
    series = technical.macd_histogram_series(df)
    assert round(float(series.iloc[-1]), 8) == 0.2611735
    assert round(float(series.iloc[-2]), 8) == 0.31880332


def test_macd_histogram_series_too_short_returns_none():
    assert technical.macd_histogram_series(pd.DataFrame({"close": [1, 2, 3]})) is None
