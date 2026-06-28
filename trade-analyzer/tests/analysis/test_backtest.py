import numpy as np
import pandas as pd
from src.analysis import backtest


def _panel(n_days=200, n_syms=30, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    syms = [f"S{i}" for i in range(n_syms)]
    idx = pd.MultiIndex.from_product([dates, syms], names=["date", "symbol"])
    good = pd.Series(rng.normal(size=len(idx)), index=idx)
    fwd = good * 0.05 + rng.normal(scale=0.001, size=len(idx))
    noise = pd.Series(rng.normal(size=len(idx)), index=idx)
    factors = pd.DataFrame({"good": good, "noise": noise}, index=idx)
    return factors, pd.Series(fwd, index=idx, name="forward")


def test_factor_ic_detects_signal():
    f, fwd = _panel()
    ic = backtest.factor_ic(f["good"], fwd)
    assert ic["mean_ic"] > 0.8 and ic["icir"] > 1.0
    ic_n = backtest.factor_ic(f["noise"], fwd)
    assert abs(ic_n["mean_ic"]) < 0.2


def test_quantile_spread_positive_for_signal():
    f, fwd = _panel()
    assert backtest.quantile_spread(f["good"], fwd, q=5) > 0
