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


def test_zscore_cross_section_is_standardized():
    f, _ = _panel()
    z = backtest.zscore_by_date(f)
    by_date_std = z["good"].groupby(level="date").std(ddof=0).dropna()
    assert (by_date_std.between(0.8, 1.2)).mean() > 0.9


def test_zscore_handles_constant_cross_section():
    # a date where every symbol has the same factor value -> z = 0, no inf/NaN-blowup
    dates = pd.date_range("2023-01-02", periods=3, freq="B")
    syms = ["A", "B", "C"]
    idx = pd.MultiIndex.from_product([dates, syms], names=["date", "symbol"])
    f = pd.DataFrame({"x": [1.0, 1.0, 1.0, 2.0, 0.0, 4.0, 1.0, 1.0, 1.0]}, index=idx)
    z = backtest.zscore_by_date(f)
    assert np.isfinite(z["x"].iloc[3:6]).all()        # the varied date is finite
    assert (z["x"].iloc[0:3] == 0).all()              # the constant date -> 0


def test_icir_weights_favor_signal():
    f, fwd = _panel()
    ics = {c: backtest.factor_ic(f[c], fwd) for c in f.columns}
    w = backtest.icir_weights(ics)
    assert w["good"] > w["noise"]
    assert all(v >= 0 for v in w.values())


def test_composite_score_predicts():
    f, fwd = _panel()
    z = backtest.zscore_by_date(f)
    w = backtest.icir_weights({c: backtest.factor_ic(f[c], fwd) for c in f.columns})
    comp = backtest.composite(z, w)
    assert backtest.factor_ic(comp, fwd)["mean_ic"] > 0.7
