"""Tests for out-of-sample calibration (Phase 4, task 4.6).

The shipped artifact calibrates its score->outcome bands on the FULL-sample
composite — the same data the weights were fitted on. So the bands' mean forward
return and hit rate are in-sample statistics, and the numbers the Trade page
prints as "the band's calibrated mean" are optimistic by an unknown amount.

The plan's exit criterion asks whether the BOTTOM band's edge is real. That
cannot be answered from an in-sample band, so the study calibrates on the
walk-forward's out-of-sample composites instead.

The invariant worth pinning: not one training row may reach the calibration.
"""
import numpy as np
import pandas as pd

from src.analysis import backtest as B
from research import variants as V


def _panel(n_dates=90, n_syms=20, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    syms = [f"S{i:02d}" for i in range(n_syms)]
    idx = pd.MultiIndex.from_product([dates, syms], names=["date", "symbol"])
    sig = rng.normal(size=len(idx))
    fwd = 0.5 * sig + rng.normal(0, 0.8, len(idx))
    return pd.DataFrame({"sig": sig, "noise": rng.normal(size=len(idx))},
                        index=idx), pd.Series(fwd, index=idx)


WF = dict(train=40, test=10, step=10)


class TestNoTrainingRowReachesTheCalibration:
    def test_the_oos_composite_covers_only_TEST_windows(self):
        panel, fwd = _panel()
        comp, _ = V.oos_composite(panel, fwd, **WF)
        dates = sorted(panel.index.get_level_values("date").unique())
        first_test_date = dates[WF["train"]]
        assert comp.index.get_level_values("date").min() >= first_test_date

    def test_its_length_matches_what_walk_forward_says_it_scored(self):
        panel, fwd = _panel()
        comp, _ = V.oos_composite(panel, fwd, **WF)
        wf = B.walk_forward(panel, fwd, **WF)
        assert int(comp.notna().sum()) == wf["n_scored_rows"]

    def test_the_forward_series_is_aligned_to_the_composite(self):
        panel, fwd = _panel()
        comp, y = V.oos_composite(panel, fwd, **WF)
        assert comp.index.equals(y.index)


class TestItCalibrates:
    def test_bands_are_ordered_and_cover_the_oos_sample(self):
        panel, fwd = _panel()
        comp, y = V.oos_composite(panel, fwd, **WF)
        bands = B.calibrate(comp, y, n_bands=5)
        assert len(bands) == 5
        assert [b["band"] for b in bands] == sorted(b["band"] for b in bands)
        assert sum(b["n"] for b in bands) == int(
            pd.DataFrame({"c": comp, "y": y}).dropna().shape[0])

    def test_a_real_signal_still_separates_out_of_sample(self):
        panel, fwd = _panel()
        comp, y = V.oos_composite(panel, fwd, **WF)
        bands = B.calibrate(comp, y, n_bands=5)
        assert bands[-1]["mean_fwd"] > bands[0]["mean_fwd"]
