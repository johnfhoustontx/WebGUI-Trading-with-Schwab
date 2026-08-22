"""Tests for covariance-aware weighting (Phase 4, task 4.4 — the C12 fix).

`signed_ic_weights` scores every factor UNIVARIATELY, so a cluster of four
correlated momentum factors each receive full credit for the same underlying
signal and the cluster ends up with ~4x the weight its information justifies.
Both schemes here are alternatives that see the covariance; the study keeps
whichever wins out-of-sample.

The decisive test in both classes is the same: hand the weighter a factor and
an exact DUPLICATE of it, and see whether the pair is double-counted.
"""
import numpy as np
import pandas as pd
import pytest

from src.analysis import backtest as B


def _panel(n_dates=60, n_syms=25, seed=0, dup=True):
    """`sig` predicts the forward return; `sig_copy` is a near-duplicate of it
    (the momentum cluster in miniature); `indep` predicts independently."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    syms = [f"S{i:02d}" for i in range(n_syms)]
    idx = pd.MultiIndex.from_product([dates, syms], names=["date", "symbol"])
    n = len(idx)
    sig = rng.normal(size=n)
    indep = rng.normal(size=n)
    fwd = 0.5 * sig + 0.5 * indep + rng.normal(0, 0.7, n)
    cols = {"sig": sig, "indep": indep}
    if dup:
        cols["sig_copy"] = sig + rng.normal(0, 0.02, n)     # ~0.999 correlated
    return pd.DataFrame(cols, index=idx), pd.Series(fwd, index=idx)


def _cluster_share(weights, names=("sig", "sig_copy")):
    tot = sum(abs(v) for v in weights.values())
    return sum(abs(weights.get(k, 0.0)) for k in names) / tot if tot else 0.0


class TestRidgeWeights:
    def test_it_gives_a_duplicated_cluster_LESS_share_than_univariate_ic(self):
        """C12 in one assertion."""
        panel, fwd = _panel()
        ic = B.signed_ic_weights({c: B.factor_ic(panel[c], fwd) for c in panel.columns})
        ridge = B.ridge_weights(panel, fwd)
        assert _cluster_share(ridge) < _cluster_share(ic)

    def test_the_weights_are_signed(self):
        """A factor that predicts with the wrong sign must carry a negative
        weight, exactly as signed_ic_weights does — `low_vol` depends on it."""
        panel, fwd = _panel(dup=False)
        panel = panel.assign(inverted=-panel["sig"])
        w = B.ridge_weights(panel, fwd)
        assert w["sig"] > 0 and w["inverted"] < 0

    def test_absolute_weights_sum_to_one(self):
        panel, fwd = _panel()
        w = B.ridge_weights(panel, fwd)
        assert sum(abs(v) for v in w.values()) == pytest.approx(1.0)

    def test_a_stronger_alpha_shrinks_the_spread_between_weights(self):
        panel, fwd = _panel()
        loose = B.ridge_weights(panel, fwd, alpha=0.01)
        tight = B.ridge_weights(panel, fwd, alpha=1000.0)
        assert np.std(list(tight.values())) < np.std(list(loose.values()))

    def test_every_input_factor_gets_a_key(self):
        panel, fwd = _panel()
        assert set(B.ridge_weights(panel, fwd)) == set(panel.columns)

    @pytest.mark.parametrize("mangle", [
        lambda p: p.iloc[:0],                       # empty
        lambda p: p.assign(dead=0.0),               # a constant column
        lambda p: p[["sig"]],                       # a single factor
    ])
    def test_degenerate_panels_do_not_raise(self, mangle):
        panel, fwd = _panel()
        w = B.ridge_weights(mangle(panel), fwd)
        assert isinstance(w, dict)


class TestOrthogonalizedIcWeights:
    def test_a_duplicate_factor_earns_almost_no_weight(self):
        """The greedy version of the same idea: once `sig` is in, its copy has
        no residual information left to contribute."""
        panel, fwd = _panel()
        w = B.orthogonalized_ic_weights(panel, fwd)
        assert abs(w.get("sig_copy", 0.0)) < abs(w["sig"]) / 4

    def test_an_independent_factor_keeps_its_weight(self):
        panel, fwd = _panel()
        w = B.orthogonalized_ic_weights(panel, fwd)
        assert abs(w["indep"]) > 0.1

    def test_absolute_weights_sum_to_one(self):
        panel, fwd = _panel()
        w = B.orthogonalized_ic_weights(panel, fwd)
        assert sum(abs(v) for v in w.values()) == pytest.approx(1.0)

    def test_it_keeps_the_sign_of_the_residual_ic(self):
        panel, fwd = _panel(dup=False)
        panel = panel.assign(inverted=-panel["sig"])
        w = B.orthogonalized_ic_weights(panel, fwd)
        assert w["sig"] * w["inverted"] < 0

    def test_a_floor_below_which_a_residual_is_noise_still_applies(self):
        panel, fwd = _panel()
        strict = B.orthogonalized_ic_weights(panel, fwd, min_abs_ic=0.5)
        assert strict == {}


class TestWalkForwardAcceptsADataWeighter:
    def test_fit_fn_is_called_with_the_fold_data_not_ic_summaries(self):
        """Ridge needs the panel, not a dict of IC summaries — so walk_forward
        has to offer a second door. Without this the covariance-aware variants
        could only ever be measured in-sample."""
        panel, fwd = _panel(n_dates=90)
        seen = []

        def _fit(f, y):
            seen.append(f.shape)
            return B.ridge_weights(f, y)

        out = B.walk_forward(panel, fwd, train=40, test=10, step=10, fit_fn=_fit)
        assert len(seen) == out["n_folds"] > 1
        assert all(len(s) == 2 for s in seen)

    def test_a_ridge_walk_forward_produces_a_real_oos_ic(self):
        panel, fwd = _panel(n_dates=90)
        out = B.walk_forward(panel, fwd, train=40, test=10, step=10,
                             fit_fn=B.ridge_weights)
        assert out["oos_ic"] > 0

    def test_it_reports_how_many_rows_the_composite_actually_scored(self):
        """The regime-conditioned variant is only comparable to the pooled one
        if both score the same rows, so the count has to come from the same
        place the OOS IC does — recomputing it in a second pass would double the
        cost of every variant on a ~100k-row panel."""
        panel, fwd = _panel(n_dates=90)
        out = B.walk_forward(panel, fwd, train=40, test=10, step=10)
        assert out["n_scored_rows"] == out["n_folds"] * 10 * 25
