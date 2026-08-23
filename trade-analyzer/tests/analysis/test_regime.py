"""Tests for the daily-bar market regime classifier (Phase 4, task 4.3).

This exists so the fit can estimate weights PER REGIME and the live scorer can
ask which regime we are in today — the C13 fix for `low_vol` carrying a
regime-overfit inverted sign at 39% of the model's absolute weight.

The load-bearing property is CAUSALITY, exactly as in `factors.py`: the label at
bar t may depend only on data at or before t. A full-sample volatility quantile
would classify 2022 using 2026's distribution and inflate every per-regime IC in
the backtest — the same look-ahead that was already removed from per-factor
winsorization once.
"""
import numpy as np
import pandas as pd
import pytest

from src.analysis import regime as R


def _series(values, start="2019-01-01"):
    return pd.Series(np.asarray(values, dtype="float64"),
                     index=pd.date_range(start, periods=len(values), freq="B"))


def _quiet(n=900, level=100.0):
    rng = np.random.default_rng(7)
    return _series(level + np.cumsum(rng.normal(0, 0.15, n)))


def _uptrend(n=900):
    rng = np.random.default_rng(11)
    return _series(100.0 * np.exp(np.cumsum(rng.normal(0.0012, 0.006, n))))


class TestCausality:
    def test_truncating_the_history_does_not_change_earlier_labels(self):
        """The test that catches a full-sample quantile. If the vol reference
        were computed over the whole series, cutting the tail would move labels
        near the start."""
        spy = _uptrend(900)
        full = R.classify(spy)
        cut = R.classify(spy.iloc[:700])
        pd.testing.assert_series_equal(full.iloc[:700], cut, check_names=False)

    def test_appending_a_crash_does_not_relabel_the_calm_years_before_it(self):
        spy = _quiet(800)
        before = R.classify(spy)
        crashed = pd.concat([spy, _series(
            spy.iloc[-1] * np.exp(np.cumsum(np.random.default_rng(3).normal(-0.01, 0.05, 60))),
            start="2022-01-25")])
        after = R.classify(crashed)
        pd.testing.assert_series_equal(before, after.iloc[:len(before)], check_names=False)


class TestTheLabels:
    def test_every_label_is_from_the_known_set(self):
        got = set(R.classify(_uptrend()).dropna().unique())
        assert got <= set(R.REGIMES)

    def test_warmup_is_None_rather_than_a_guessed_regime(self):
        """Before there is enough history to know what 'elevated vol' means for
        this market, the honest answer is no label — not the middle one."""
        lab = R.classify(_quiet(900))
        assert lab.iloc[:R.WARMUP - 1].isna().all()
        assert lab.iloc[-1] is not None

    def test_a_flat_quiet_tape_is_PREVAILINGLY_chop(self):
        assert R.classify(_quiet()).dropna().mode().iloc[0] == "chop"

    def test_a_sustained_uptrend_is_PREVAILINGLY_trend(self):
        assert R.classify(_uptrend()).dropna().mode().iloc[0] == "trend"

    def test_highvol_fires_at_roughly_its_QUANTILE_rate_on_a_constant_vol_tape(self):
        """Not an accident worth working around — a consequence of the
        threshold being a trailing PERCENTILE. On a tape whose volatility never
        actually changes, ~1-VOL_HI_Q of bars sit above their own trailing
        quantile, so an individual bar's label is noisy while the prevailing one
        is not. Anything reading a single bar (the live scorer) inherits that,
        which is why `current_regime` is documented as a coarse selector."""
        lab = R.classify(_uptrend()).dropna()
        share = (lab == "highvol").mean()
        assert 0.05 < share < 0.45

    def test_a_volatility_explosion_outranks_trend(self):
        """A crash is both displaced from its 200-EMA and violent. It must
        classify as highvol: that is the regime whose weights differ, and
        calling it 'trend' would score a panic with trend-regime weights."""
        rng = np.random.default_rng(5)
        calm = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.006, 800)))
        crash = calm[-1] * np.exp(np.cumsum(rng.normal(-0.012, 0.045, 40)))
        lab = R.classify(_series(np.concatenate([calm, crash])))
        assert lab.iloc[-1] == "highvol"


class TestTheLiveReading:
    def test_current_regime_is_the_last_classified_bar(self):
        spy = _uptrend()
        assert R.current_regime(spy) == R.classify(spy).dropna().iloc[-1]

    def test_too_little_history_yields_None_not_a_default(self):
        assert R.current_regime(_quiet(50)) is None

    @pytest.mark.parametrize("bad", [None, pd.Series(dtype="float64")])
    def test_a_degenerate_input_yields_None_rather_than_raising(self, bad):
        assert R.current_regime(bad) is None
