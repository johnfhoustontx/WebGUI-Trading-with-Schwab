"""Tests for the regime-conditioned walk-forward (Phase 4, task 4.3).

The single-regime fit gives `low_vol` 39% of the model's absolute weight on an
INVERTED sign — the model rewards volatility. The C13 hypothesis is that this is
a regime artifact rather than a stable effect. Testing it requires fitting
weights per regime and scoring each test date under its own regime's weights.

Two failure modes this has to avoid, both silent:
  * a regime with almost no training data producing wild weights — it must fall
    back to the pooled fit, not to whatever four days of history implies;
  * test dates being DROPPED when their regime is unfitted, which would quietly
    change the sample the OOS IC is measured over and make the comparison
    against the pooled model meaningless.
"""
import numpy as np
import pandas as pd
import pytest

from research import variants as V


def _flip_panel(n_blocks=14, block=20, n_syms=25, seed=0):
    """Alternating regimes where one factor's predictive SIGN flips between
    them — the shape C13 claims `low_vol` has. Pooled, `flip` looks useless;
    conditioned, it is strong in both regimes."""
    rng = np.random.default_rng(seed)
    dates, labels = [], []
    d = pd.Timestamp("2024-01-01")
    for b in range(n_blocks):
        blk = pd.bdate_range(d, periods=block)
        dates.extend(blk)
        labels.extend(["A" if b % 2 == 0 else "B"] * block)
        d = blk[-1] + pd.Timedelta(days=3)
    syms = [f"S{i:02d}" for i in range(n_syms)]
    idx = pd.MultiIndex.from_product([pd.DatetimeIndex(dates), syms],
                                     names=["date", "symbol"])
    regimes = pd.Series(labels, index=pd.DatetimeIndex(dates))
    sign = regimes.reindex(idx.get_level_values("date")).map(
        {"A": 1.0, "B": -1.0}).to_numpy()
    flip = rng.normal(size=len(idx))
    steady = rng.normal(size=len(idx))
    fwd = 0.7 * sign * flip + 0.2 * steady + rng.normal(0, 0.7, len(idx))
    panel = pd.DataFrame({"flip": flip, "steady": steady}, index=idx)
    return panel, pd.Series(fwd, index=idx), regimes


WF = dict(train=100, test=20, step=20)
# A 100-day train window over 20-day blocks holds 60 days of one regime and 40
# of the other, so the production floor of 60 would leave the minority regime
# permanently unfitted. These tests are about the mechanism, not the floor.
MIN = dict(min_regime_days=30)


class TestItCapturesASignFlip:
    def test_regime_weights_beat_pooled_when_a_factor_flips_sign(self):
        panel, fwd, regimes = _flip_panel()
        pooled = V.run_variant(panel, fwd, label="pooled", **WF)
        cond = V.regime_walk_forward(panel, fwd, regimes, label="by regime",
                                     **WF, **MIN)
        assert cond["oos_ic"] > pooled["oos_ic"]

    def test_it_fits_a_separate_weight_set_per_regime(self):
        panel, fwd, regimes = _flip_panel()
        cond = V.regime_walk_forward(panel, fwd, regimes, label="x", **WF, **MIN)
        wa = cond["weights_by_regime"]["A"]["flip"]
        wb = cond["weights_by_regime"]["B"]["flip"]
        assert wa * wb < 0, "the flip factor should carry opposite signs"


class TestFallbackAndCoverage:
    def test_a_thinly_trained_regime_falls_back_to_the_pooled_weights(self):
        """A regime seen for a handful of days must not get its own weights.
        The plan's stated acceptance test."""
        panel, fwd, regimes = _flip_panel()
        rare = regimes.copy()
        rare.iloc[100:103] = "rare"      # inside fold 1's TEST window, and thinly
                                         # represented in every later TRAIN window
        cond = V.regime_walk_forward(panel, fwd, rare, label="x",
                                     min_regime_days=30, **WF)
        assert "rare" in cond["fallback_regimes"]
        assert "rare" not in cond["weights_by_regime"]

    def test_no_test_date_is_dropped_when_a_regime_is_unfitted(self):
        """Dropping them would change the sample the OOS IC is measured over,
        so the comparison against the pooled model would no longer be like for
        like — and nothing would say so."""
        panel, fwd, regimes = _flip_panel()
        rare = regimes.copy()
        rare.iloc[100:103] = "rare"      # inside fold 1's TEST window, and thinly
                                         # represented in every later TRAIN window
        cond = V.regime_walk_forward(panel, fwd, rare, label="x",
                                     min_regime_days=30, **WF)
        pooled = V.run_variant(panel, fwd, label="pooled", **WF)
        assert cond["n_scored_rows"] == pooled["n_scored_rows"]

    def test_an_unlabelled_date_is_scored_with_the_pooled_weights(self):
        panel, fwd, regimes = _flip_panel()
        gappy = regimes.copy()
        gappy.iloc[:] = np.nan
        cond = V.regime_walk_forward(panel, fwd, gappy, label="x", **WF)
        pooled = V.run_variant(panel, fwd, label="pooled", **WF)
        assert cond["oos_ic"] == pytest.approx(pooled["oos_ic"], abs=1e-9)

    def test_it_reports_the_regime_mix_it_actually_saw(self):
        panel, fwd, regimes = _flip_panel()
        cond = V.regime_walk_forward(panel, fwd, regimes, label="x", **WF)
        assert set(cond["regime_days"]) == {"A", "B"}
        assert sum(cond["regime_days"].values()) == regimes.notna().sum()
