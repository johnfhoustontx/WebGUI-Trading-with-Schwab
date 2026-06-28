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


def test_walk_forward_reports_oos_ic():
    f, fwd = _panel(n_days=400)
    res = backtest.walk_forward(f, fwd, train=150, test=50, step=50)
    assert res["oos_ic"] > 0.5
    assert res["n_folds"] >= 2
    assert "good" in res["weights"]


def test_calibration_bands_monotone():
    f, fwd = _panel()
    z = backtest.zscore_by_date(f)
    w = backtest.icir_weights({c: backtest.factor_ic(f[c], fwd) for c in f.columns})
    comp = backtest.composite(z, w)
    bands = backtest.calibrate(comp, fwd, n_bands=5)
    means = [b["mean_fwd"] for b in bands]
    assert means == sorted(means)


def test_factor_ic_thin_history_no_icir_explosion():
    # a factor with IC computable on only 2 dates: signal detected, but ICIR NOT
    # trusted (no 1e9 explosion) -> icir_weights won't hand it the weight.
    dates = pd.date_range("2023-01-02", periods=2, freq="B")
    syms = [f"S{i}" for i in range(10)]
    idx = pd.MultiIndex.from_product([dates, syms], names=["date", "symbol"])
    vals = np.tile(np.arange(10.0), 2)
    f = pd.Series(vals, index=idx)
    fwd = pd.Series(vals * 0.01, index=idx)        # perfectly aligned each day
    ic = backtest.factor_ic(f, fwd)
    assert ic["mean_ic"] > 0.9 and ic["n_days"] == 2
    assert ic["icir"] == 0.0


def test_thin_factor_does_not_dominate_weights():
    thin = {"mean_ic": 0.9, "icir": 0.0, "n_days": 2}      # post-I1: icir gated to 0
    stable = {"mean_ic": 0.04, "icir": 1.5, "n_days": 200}
    w = backtest.icir_weights({"thin": thin, "stable": stable})
    assert w["stable"] > w["thin"] and w["thin"] == 0.0


def test_walk_forward_no_leakage_on_noise():
    rng = np.random.default_rng(7)
    dates = pd.date_range("2023-01-02", periods=400, freq="B")
    syms = [f"S{i}" for i in range(30)]
    idx = pd.MultiIndex.from_product([dates, syms], names=["date", "symbol"])
    f = pd.DataFrame({"noise": rng.normal(size=len(idx))}, index=idx)
    fwd = pd.Series(rng.normal(size=len(idx)), index=idx)   # independent of f
    res = backtest.walk_forward(f, fwd, train=150, test=50, step=50)
    assert abs(res["oos_ic"]) < 0.2     # a noise factor yields no spurious OOS edge
