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
    # POC unchanged (highest-volume bin). VAH/VAL now bound the CONTIGUOUS value
    # area grown outward from the POC (annexing the larger adjacent bin until 70%
    # of volume is captured) — the standard Market-Profile construction — rather
    # than the max/min of a volume-sorted, possibly-disjoint top-70% set.
    assert round(vp["poc"], 6) == 102.401128
    assert round(vp["vah"], 6) == 104.39929
    assert round(vp["val"], 6) == 97.905265


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


def test_rsi_matches_wilder_stockcharts_reference():
    """Canonical StockCharts RSI-14 worked example: with Wilder's smoothing the
    15th bar's RSI is 70.53 and the 16th is 66.32. A simple rolling mean does NOT
    reproduce these (that was the bug)."""
    closes = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955,
              45.4245, 45.8433, 46.0826, 45.8931, 46.0328, 45.6140, 46.2820,
              46.2820, 46.0028]
    df = pd.DataFrame({"close": closes[:15]})
    assert round(technical.calculate_rsi(df, 14), 2) == 70.53
    df2 = pd.DataFrame({"close": closes[:16]})
    assert round(technical.calculate_rsi(df2, 14), 2) == 66.32


def test_adx_uses_wilder_smoothing():
    """ADX/DI must be Wilder-smoothed. Pin the value on a deterministic random
    walk so a regression back to simple rolling means is caught."""
    rng = np.random.default_rng(7)
    n = 60
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame({"high": close + rng.uniform(0.2, 1.5, n),
                       "low": close - rng.uniform(0.2, 1.5, n), "close": close})
    adx = technical.calculate_adx(df, 14)
    assert round(adx, 4) == 47.0052


def test_vwap_resets_per_session():
    """Session-anchored VWAP resets each day. Two identical sessions must yield a
    VWAP equal to a single session's VWAP (a cumulative-over-all-days VWAP would
    NOT — it would be dragged toward the earlier session)."""
    # Day 1: rising prices; Day 2: identical pattern.
    day1 = pd.date_range("2026-06-29 09:30", periods=3, freq="30min")
    day2 = pd.date_range("2026-06-30 09:30", periods=3, freq="30min")
    rows = {
        "datetime": list(day1) + list(day2),
        "high": [10, 12, 14, 10, 12, 14],
        "low": [10, 12, 14, 10, 12, 14],
        "close": [10, 12, 14, 10, 12, 14],
        "volume": [100, 100, 100, 100, 100, 100],
    }
    df = pd.DataFrame(rows)
    vwap = technical.calculate_vwap(df)
    # Second session in isolation: (10+12+14)/3 = 12.0 with equal volume.
    assert round(vwap, 6) == 12.0


def test_relative_strength_parity_stable_in_down_market():
    """Parity-preserving RS: 100 = parity. When BOTH fall but the stock falls
    less, RS must be > 100 (outperformance) — the old return/return quotient
    sign-inverted here (negative/negative gave a spurious positive ratio of the
    wrong magnitude)."""
    # 6-bar frames; period=5 => compare iloc[-1] vs iloc[-6].
    stock = pd.DataFrame({"close": [100, 100, 100, 100, 100, 95]})   # -5%
    bench = pd.DataFrame({"close": [100, 100, 100, 100, 100, 90]})   # -10%
    rs = technical.calculate_relative_strength(stock, bench, periods=[5])
    # 100*(1-0.05)/(1-0.10) = 100*0.95/0.90 = 105.5556 -> outperformance
    assert round(rs["1W"], 4) == 105.5556
