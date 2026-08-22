"""Tests for the variant runner (Phase 4).

A "variant" is one methodology choice — a noise floor, a weighting scheme —
scored against a fixed panel. The runner's job is to produce records that are
COMPARABLE, which means two things that are easy to get subtly wrong:

  * the choice under test must reach the WALK-FORWARD folds, not only the
    full-sample fit that ships in the artifact. A floor applied to one and not
    the other yields a study where OOS IC never moves and the conclusion is
    "the floor doesn't matter" — a false negative with no symptom.
  * the shipped-artifact facts (kept factors, a factor's weight sign) must come
    from the FULL-sample fit, because that is what would actually score a live
    symbol. Reading them off the last fold would describe a model nobody runs.
"""
import numpy as np
import pandas as pd

from src.analysis import backtest as B
from research import variants as V


def _panel(n_dates=90, n_syms=12, seed=0):
    """A panel where `good` genuinely predicts the forward return and `noise`
    does not."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    syms = [f"S{i:02d}" for i in range(n_syms)]
    idx = pd.MultiIndex.from_product([dates, syms], names=["date", "symbol"])
    good = rng.normal(size=len(idx))
    noise = rng.normal(size=len(idx))
    fwd = 0.6 * good + 0.8 * rng.normal(size=len(idx))
    panel = pd.DataFrame({"good": good, "noise": noise}, index=idx)
    return panel, pd.Series(fwd, index=idx)


WF = dict(train=40, test=10, step=10)


class TestTheChoiceUnderTestReachesTheFolds:
    def test_an_impossible_floor_and_a_permissive_one_give_DIFFERENT_oos_ic(self):
        """The regression guard. At a floor of 0.5 no factor qualifies, so the
        composite is empty and OOS IC collapses; at 0.005 `good` carries it. If
        the floor were only applied to the full-sample fit these two would be
        identical."""
        panel, fwd = _panel()
        loose = V.run_variant(panel, fwd, label="loose", min_abs_ic=0.005, **WF)
        tight = V.run_variant(panel, fwd, label="tight", min_abs_ic=0.5, **WF)
        assert loose["oos_ic"] != tight["oos_ic"]
        assert loose["oos_ic"] > tight["oos_ic"]

    def test_a_custom_weight_fn_also_reaches_the_folds(self):
        panel, fwd = _panel()
        seen = []

        def _wf(ics):
            seen.append(dict(ics))
            return B.signed_ic_weights(ics)

        V.run_variant(panel, fwd, label="custom", weight_fn=_wf, **WF)
        assert len(seen) > 1, "the weight fn should be called once per fold"


class TestTheRecordDescribesTheSHIPPINGFit:
    def test_kept_factors_come_from_the_full_sample_fit(self):
        panel, fwd = _panel()
        rec = V.run_variant(panel, fwd, label="x", min_abs_ic=0.005, **WF)
        full = B.signed_ic_weights(
            {c: B.factor_ic(panel[c], fwd) for c in panel.columns}, min_abs_ic=0.005)
        assert rec["kept"] == sum(1 for v in full.values() if v != 0)
        assert rec["weights"] == full

    def test_a_higher_floor_keeps_no_more_factors_than_a_lower_one(self):
        panel, fwd = _panel()
        lo = V.run_variant(panel, fwd, label="lo", min_abs_ic=0.001, **WF)
        hi = V.run_variant(panel, fwd, label="hi", min_abs_ic=0.05, **WF)
        assert hi["kept"] <= lo["kept"]

    def test_it_reports_the_sign_of_a_named_factor(self):
        """`rs_spy`'s sign flip is the specific thing Phase 0 flagged, so the
        record has to carry a factor's weight sign, not just the count."""
        panel, fwd = _panel()
        rec = V.run_variant(panel, fwd, label="x", min_abs_ic=0.005, **WF)
        assert rec["weights"].get("good", 0) > 0


class TestFoldStability:
    def test_it_counts_the_NEGATIVE_folds(self):
        panel, fwd = _panel()
        rec = V.run_variant(panel, fwd, label="x", min_abs_ic=0.005, **WF)
        assert rec["negative_folds"] == sum(1 for x in rec["oos_ic_by_fold"] if x < 0)
        assert rec["n_folds"] == len(rec["oos_ic_by_fold"])

    def test_the_label_is_carried_through(self):
        panel, fwd = _panel()
        assert V.run_variant(panel, fwd, label="floor=0.02", **WF)["label"] == "floor=0.02"
