"""Tests for the regime-aware artifact builder (Phase 4, exit criterion).

Phase 4's exit is "a new artifact with populated regime keys". Three things
about how those keys are built decide whether the artifact is honest:

  * a regime the fit could not estimate must be OMITTED, not written empty — the
    scorer falls back on a missing key, and a key present with junk weights is
    the harder failure to notice;
  * a regime's weights must be fitted on THAT regime's rows only, or the keys
    are decoration;
  * the calibration bands must be OUT OF SAMPLE. The shipped artifact calibrates
    on the same rows its weights were fitted on, so the "calibrated mean" the
    Trade page prints as an expectation is an in-sample statistic — and that is
    precisely the number the exit criterion asks about.
"""
import numpy as np
import pandas as pd
import pytest

from research import artifact as A


def _panel(n_dates=260, n_syms=25, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_dates, freq="B")
    syms = [f"S{i:02d}" for i in range(n_syms)]
    idx = pd.MultiIndex.from_product([dates, syms], names=["date", "symbol"])
    sig = rng.normal(size=len(idx))
    fwd = 0.5 * sig + rng.normal(0, 0.8, len(idx))
    panel = pd.DataFrame({"sig": sig, "other": rng.normal(size=len(idx))}, index=idx)
    # Two regimes in long blocks, plus a third that barely appears.
    lab = pd.Series("trend", index=dates, dtype="object")
    lab.iloc[120:] = "chop"
    lab.iloc[60:64] = "highvol"
    return panel, pd.Series(fwd, index=idx), lab


WF = dict(train=80, test=20, step=20)


@pytest.fixture(scope="module")
def art():
    """Built ONCE. Every assertion below interrogates the same artifact, and
    building it runs several walk-forwards — rebuilding per test made this file
    the slowest in the suite for no extra coverage."""
    panel, fwd, lab = _panel()
    return A.build_regimes(panel, fwd, lab, min_regime_days=40, **WF)


class TestWhichKeysExist:
    def test_all_is_always_present(self, art):
        assert "all" in art

    def test_a_well_represented_regime_gets_its_own_key(self, art):
        assert {"trend", "chop"} <= set(art)

    def test_an_underpowered_regime_is_OMITTED_not_written_empty(self, art):
        """A key present with weights from four days is worse than no key: the
        scorer would use it, and nothing on the card would say the weights came
        from nearly no data."""
        assert "highvol" not in art


class TestTheKeysAreNotDecoration:
    def test_a_regime_block_is_fitted_on_that_regimes_rows_only(self, art):
        assert art["trend"]["weights"] != art["chop"]["weights"]

    def test_each_block_carries_a_norm_for_every_factor_it_weights(self, art):
        """The scorer's thin-cross-section fallback reads `norm`. A weighted
        factor with no norm entry silently drops out of the composite there."""
        for blk in art.values():
            assert set(blk["weights"]) <= set(blk["norm"])

    def test_each_block_records_how_many_days_it_was_fitted_on(self, art):
        assert art["trend"]["n_days"] > 0
        assert art["all"]["n_days"] >= art["trend"]["n_days"]


class TestCalibrationIsOutOfSample:
    def test_the_primary_calibration_comes_from_walk_forward_windows(self, art):
        assert art["all"]["calibration"]
        assert art["all"]["calibration_basis"] == "out-of-sample"

    def test_the_in_sample_bands_are_kept_alongside_for_comparison(self, art):
        """Not for scoring — for showing how much the in-sample number
        flattered the model."""
        assert art["all"]["calibration_insample"]
        assert (art["all"]["calibration"]
                != art["all"]["calibration_insample"])

    def test_bands_stay_ascending_by_score(self, art):
        los = [b["score_lo"] for b in art["all"]["calibration"]]
        assert los == sorted(los)


class TestItNeverProducesAnUnscoreableBlock:
    def test_a_block_that_cannot_be_calibrated_is_dropped(self, art):
        """`build_regimes` may only emit blocks the scorer can actually use —
        `_select_regime` requires both weights and calibration."""
        for key, blk in art.items():
            assert blk["weights"] and blk["calibration"], key
