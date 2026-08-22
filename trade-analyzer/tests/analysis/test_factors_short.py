"""Tests for the short-side factor slate (Phase 4, task 4.5).

The existing ten factors describe a rising name and express a falling one only
by inversion, which is not the same thing: downside participation and lottery
preference are asymmetric effects with their own literature, and a symmetric
factor cannot represent them.

Every one follows `factors.py`'s contract — raw, CAUSAL, and sign-corrected so
HIGHER = MORE BULLISH. The sign correction is the part worth testing hardest,
because a flipped factor does not fail, it just quietly recommends the opposite.
"""
import numpy as np
import pandas as pd
import pytest

from src.analysis import factors as F


def _df(closes, start="2021-01-01"):
    closes = np.asarray(closes, dtype="float64")
    idx = pd.date_range(start, periods=len(closes), freq="B")
    return pd.DataFrame({
        "datetime": idx, "close": closes,
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "volume": np.full(len(closes), 1e6)})


def _walk(n, drift=0.0, vol=0.01, seed=0, start="2021-01-01"):
    rng = np.random.default_rng(seed)
    return _df(100.0 * np.exp(np.cumsum(rng.normal(drift, vol, n))), start=start)


NEW = ("max_effect", "semivol", "downside_beta", "below_200ema")


class TestTheyAreRegisteredAndCausal:
    @pytest.mark.parametrize("name", NEW)
    def test_the_factor_is_in_the_registry(self, name):
        assert name in F.FACTORS

    @pytest.mark.parametrize("name", NEW)
    def test_truncating_the_history_does_not_change_earlier_values(self, name):
        """The factors.py contract: the value at bar t uses only data at or
        before t. A rolling window that reached forward would inflate every
        measured IC in the backtest."""
        df = _walk(600, drift=0.0005, seed=3)
        spec = F.FACTORS[name]
        ref = _walk(600, drift=0.0003, seed=9)
        kw = {"ref_close": F._close(ref)} if spec["needs_ref"] else {}
        full = spec["fn"](df, **kw)
        cut = spec["fn"](df.iloc[:450], **(
            {"ref_close": F._close(ref.iloc[:450])} if spec["needs_ref"] else {}))
        pd.testing.assert_series_equal(
            full.iloc[:450].dropna(), cut.dropna(), check_names=False)


class TestMaxEffect:
    def test_a_lottery_spike_scores_LOWER_than_a_steady_climb(self):
        """Bali/Cakici/Whitelaw: stocks with an extreme recent daily gain
        underperform. Sign-corrected, the spiky name must score lower."""
        steady = _walk(300, drift=0.001, vol=0.005, seed=1)
        spiky = steady.copy()
        spiky.loc[spiky.index[-5], "close"] *= 1.35        # one lottery day
        assert (F.max_effect(spiky).iloc[-1]
                < F.max_effect(steady).iloc[-1])

    def test_it_forgets_a_spike_older_than_its_window(self):
        base = _walk(400, drift=0.0005, vol=0.006, seed=2)
        old = base.copy()
        old.loc[old.index[100], "close"] *= 1.4
        assert F.max_effect(old).iloc[-1] == pytest.approx(
            F.max_effect(base).iloc[-1])


class TestSemivol:
    def test_a_name_with_violent_DOWN_days_scores_lower(self):
        rng = np.random.default_rng(4)
        calm_r = rng.normal(0.0004, 0.008, 300)
        rough_r = calm_r.copy()
        rough_r[-30:] = -np.abs(rng.normal(0.0, 0.05, 30))      # downside only
        calm = _df(100 * np.exp(np.cumsum(calm_r)))
        rough = _df(100 * np.exp(np.cumsum(rough_r)))
        assert F.semivol(rough).iloc[-1] < F.semivol(calm).iloc[-1]

    def test_upside_volatility_alone_does_not_penalise_it(self):
        """The whole point of a SEMI-deviation: a name that only jumps upward
        is not a risky name, and plain realized vol cannot tell the difference."""
        rng = np.random.default_rng(6)
        flat_r = np.full(300, 0.0003)
        up_r = flat_r.copy()
        up_r[-30:] = np.abs(rng.normal(0.0, 0.05, 30))         # upside only
        base = F.semivol(_df(100 * np.exp(np.cumsum(flat_r)))).iloc[-1]
        upside = F.semivol(_df(100 * np.exp(np.cumsum(up_r)))).iloc[-1]
        assert upside == pytest.approx(base, abs=1e-6)


class TestDownsideBeta:
    def test_a_name_that_falls_twice_as_hard_scores_lower(self):
        rng = np.random.default_rng(8)
        mkt_r = rng.normal(0.0, 0.01, 400)
        mkt = _df(100 * np.exp(np.cumsum(mkt_r)))
        ref = F._close(mkt)
        down = mkt_r < 0
        soft_r, hard_r = mkt_r.copy(), mkt_r.copy()
        soft_r[down] = mkt_r[down] * 0.5
        hard_r[down] = mkt_r[down] * 2.0
        soft = _df(100 * np.exp(np.cumsum(soft_r)))
        hard = _df(100 * np.exp(np.cumsum(hard_r)))
        assert (F.downside_beta(hard, ref_close=ref).iloc[-1]
                < F.downside_beta(soft, ref_close=ref).iloc[-1])

    def test_no_reference_yields_NaN_rather_than_a_number(self):
        assert F.downside_beta(_walk(400)).isna().all()


class TestBelow200Ema:
    def test_a_name_above_its_200_ema_is_at_the_cap(self):
        """One-sided by design: this factor measures how BROKEN a name is, and
        must not double-count the upside that trend_quality already carries."""
        up = _walk(500, drift=0.0015, vol=0.005, seed=12)
        assert F.below_200ema(up).iloc[-1] == pytest.approx(0.0)

    def test_a_name_far_below_its_200_ema_is_strongly_negative(self):
        down = _walk(500, drift=-0.0015, vol=0.005, seed=13)
        assert F.below_200ema(down).iloc[-1] < -0.05

    def test_it_is_NaN_through_the_200_bar_warmup(self):
        assert F.below_200ema(_walk(500, seed=14)).iloc[:199].isna().all()


class TestTheFrameStillBuilds:
    def test_compute_factor_frame_emits_every_registered_factor(self):
        df = _walk(600, drift=0.0005, seed=15)
        ref = F._close(_walk(600, drift=0.0004, seed=16))
        frame = F.compute_factor_frame(df, spy_close=ref, sector_close=ref)
        assert set(frame.columns) == set(F.FACTORS)
