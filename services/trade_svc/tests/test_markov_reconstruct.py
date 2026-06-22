"""Run from the repo root with the repo venv:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_markov_reconstruct.py -v
(never `pytest services` over all services — cross-app module-name collisions.)"""
import numpy as np
import pandas as pd

# match the import style used by the other tests in this folder:
from services.trade_svc import compute


def _synthetic_daily(n=300, start=100.0, drift=0.3, seed=1):
    rng = np.random.default_rng(seed)
    closes = start + np.cumsum(rng.normal(drift, 1.0, n))
    closes = np.maximum(closes, 1.0)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "datetime": idx,
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": rng.integers(1_000_000, 5_000_000, n),
    })


def test_reconstruct_returns_series_in_range():
    daily = _synthetic_daily()
    spy = _synthetic_daily(seed=2, drift=0.1)
    s = compute.reconstruct_daily_composite(daily, spy, sector_hist=None)
    assert isinstance(s, pd.Series)
    valid = s.dropna()
    assert len(valid) > 200
    assert valid.between(-100.0, 100.0).all()


def test_reconstruct_uptrend_skews_positive():
    up = _synthetic_daily(drift=0.6, seed=3)
    flat_spy = _synthetic_daily(drift=0.0, seed=4)
    s = compute.reconstruct_daily_composite(up, flat_spy, sector_hist=None).dropna()
    assert s.tail(60).mean() > 10.0


def test_reconstruct_handles_short_history():
    short = _synthetic_daily(n=30)
    s = compute.reconstruct_daily_composite(short, None, None)
    assert isinstance(s, pd.Series)
    assert s.dropna().empty or len(s) == len(short)


def test_reconstruct_defensive_on_garbage():
    bad = pd.DataFrame({"datetime": pd.date_range("2024-01-01", periods=70),
                        "close": [float("nan")] * 70})
    s = compute.reconstruct_daily_composite(bad, None, None)
    assert isinstance(s, pd.Series)


def test_reconstruct_all_nan_frame_is_not_a_signal():
    n = 80
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    allnan = pd.DataFrame({"datetime": idx, "open": [np.nan] * n, "high": [np.nan] * n,
                           "low": [np.nan] * n, "close": [np.nan] * n,
                           "volume": [np.nan] * n})
    s = compute.reconstruct_daily_composite(allnan, None, None)
    valid = s.dropna()
    # all-NaN input must NOT produce a confident (bearish) score
    assert valid.empty or (valid.abs() < 1.0).all()


def test_reconstruct_data_hole_is_excluded():
    daily = _synthetic_daily(n=300, drift=0.4, seed=5)
    daily.loc[150:159, "close"] = np.nan  # a 10-bar data hole
    s = compute.reconstruct_daily_composite(daily, None, None)
    # the hole bars are not observations -> NaN (chain breaks), not bearish states
    assert s.iloc[150:160].isna().all()


def test_reconstruct_date_misaligned_spy_stays_in_range():
    sym = _synthetic_daily(n=300, seed=6)
    # SPY shorter and starting later -> date alignment (not positional) must hold
    spy = _synthetic_daily(n=220, seed=7)
    spy["datetime"] = pd.date_range("2024-03-01", periods=220, freq="B")
    s = compute.reconstruct_daily_composite(sym, spy, None).dropna()
    assert not s.empty and s.between(-100.0, 100.0).all()
