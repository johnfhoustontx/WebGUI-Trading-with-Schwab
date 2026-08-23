"""Tests for the paired fold comparison (Phase 4).

The noise-floor sweep produced a 0.0055 spread in OOS IC across floors. Whether
that is a finding or a coin flip is decided by the FOLD-LEVEL dispersion, not by
which number is biggest — the folds are the same 13 windows in every variant, so
they pair, and a paired test is far more powerful than eyeballing two means.

Picking the best of five means without this is exactly how a study concludes
that a one-factor model beats a nine-factor one.
"""
import math

import pytest

from research import variants as V


def _rec(label, folds):
    return {"label": label, "oos_ic_by_fold": list(folds)}


class TestPairedDelta:
    def test_identical_folds_are_a_zero_delta_not_a_nan(self):
        d = V.paired_delta(_rec("a", [0.01, 0.02, -0.01]), _rec("b", [0.01, 0.02, -0.01]))
        assert d["mean"] == pytest.approx(0.0)
        assert d["t"] is None          # no dispersion -> no test to run

    def test_it_measures_the_mean_PER_FOLD_difference(self):
        d = V.paired_delta(_rec("a", [0.03, 0.05]), _rec("b", [0.01, 0.01]))
        assert d["mean"] == pytest.approx(0.03)

    def test_a_consistent_shift_with_no_dispersion_reports_no_t(self):
        """+0.01 every fold: real, but a paired t is undefined at zero variance.
        Report it honestly rather than emitting an infinity that reads as
        overwhelming significance."""
        d = V.paired_delta(_rec("a", [0.02, 0.03]), _rec("b", [0.01, 0.02]))
        assert d["mean"] == pytest.approx(0.01)
        assert d["t"] is None

    def test_a_noisy_difference_gets_a_small_t(self):
        a = _rec("a", [0.05, -0.04, 0.06, -0.05, 0.04])
        b = _rec("b", [0.04, -0.05, 0.05, -0.04, 0.05])
        d = V.paired_delta(a, b)
        assert d["t"] is not None
        assert abs(d["t"]) < 3.0

    def test_the_t_matches_the_paired_formula(self):
        a, b = _rec("a", [0.03, 0.01, 0.04, 0.00]), _rec("b", [0.01, 0.01, 0.01, 0.01])
        d = V.paired_delta(a, b)
        diffs = [0.02, 0.00, 0.03, -0.01]
        n = len(diffs)
        mean = sum(diffs) / n
        var = sum((x - mean) ** 2 for x in diffs) / (n - 1)
        assert d["t"] == pytest.approx(mean / (math.sqrt(var) / math.sqrt(n)))

    def test_mismatched_fold_counts_RAISE_rather_than_truncate(self):
        """Different fold counts mean different panels, so the comparison is
        meaningless. Truncating would quietly answer a question nobody asked."""
        with pytest.raises(ValueError):
            V.paired_delta(_rec("a", [0.01, 0.02, 0.03]), _rec("b", [0.01, 0.02]))

    def test_it_carries_both_labels_so_the_sign_is_readable(self):
        d = V.paired_delta(_rec("hi", [0.02]), _rec("lo", [0.01]))
        assert d["a"] == "hi" and d["b"] == "lo"
