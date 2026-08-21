"""RS-Momentum must measure the RATE of change of relative strength, not its
acceleration — i.e. the axis must mean what its own comment and the RRG quadrant
names say it means.

Until 2026-08-20 `compute_rs_momentum` subtracted ROC's OWN rolling mean before
normalizing, differentiating a second time. Isolated on a controlled RS-Ratio
series the sign came out INVERTED: a steadily rising RS-Ratio read 99.70
("weakening") and a steadily falling one read 100.96 ("strengthening").

⚠ Scale note, because it matters for reading these tests: on REAL sector data the
practical effect was far smaller than that inversion suggests — measured over two
years of SPY + the eleven sector ETFs, the two formulas agreed on 10 of 11 sector
quadrants and on the risk-on/risk-off headline for 91% of sessions, and NEITHER
predicted forward excess return. This is a correctness-and-meaning fix, not an
edge improvement. Do not expect the screens to change much.
"""
import numpy as np
import pandas as pd
import pytest

import sector_rotation_assessment as S

_N = 200


def _ratio_series(slope, seed=3, noise=0.15):
    """An RS-Ratio series with a controlled slope and mild noise.

    Noise is required, not decoration: the normalizer divides by a rolling std of
    ROC, and a perfectly straight line has zero ROC variance -> NaN.
    """
    rng = np.random.default_rng(seed)
    return pd.Series(100 + slope * np.arange(_N) + rng.normal(0, noise, _N),
                     index=pd.RangeIndex(_N))


def test_rising_relative_strength_reads_above_100():
    """The documented semantic: RS-Momentum > 100 means strengthening."""
    assert S.compute_rs_momentum(_ratio_series(+0.05)).iloc[-1] > 100.0


def test_falling_relative_strength_reads_below_100():
    assert S.compute_rs_momentum(_ratio_series(-0.05)).iloc[-1] < 100.0


def test_a_flat_relative_strength_reads_about_100():
    mom = S.compute_rs_momentum(_ratio_series(0.0)).iloc[-1]
    assert 100.0 == pytest.approx(mom, abs=1.0)


def test_momentum_is_the_normalized_roc_with_no_second_de_meaning():
    """Pin the formula itself: 100 + ROC / rolling_std(ROC). Subtracting ROC's own
    rolling mean here (what the old code did) differentiates twice and is what
    inverted the sign."""
    rr = _ratio_series(+0.05)
    roc = rr - rr.shift(S.MOM_WINDOW)
    expected = 100.0 + roc / roc.rolling(S.NORM_WINDOW).std()
    got = S.compute_rs_momentum(rr)
    assert got.iloc[-1] == pytest.approx(float(expected.iloc[-1]), abs=1e-9)


def test_a_steadily_outperforming_sector_is_not_called_weakening():
    """End-to-end through the quadrant classifier, from PRICES rather than a
    hand-built ratio: the case that made this worth fixing."""
    bench = pd.Series(100 + np.arange(300) * 0.05, index=pd.RangeIndex(300))
    sector = bench * (1 + np.linspace(0, 0.30, 300))
    rr = S.compute_rs_ratio(sector, bench)
    mom = S.compute_rs_momentum(rr)
    assert rr.iloc[-1] > 100.0                                  # it IS out-performing
    assert S.classify_quadrant(rr.iloc[-1], mom.iloc[-1]) == "Leading"


def test_momentum_degrades_to_none_on_insufficient_data():
    assert S.compute_rs_momentum(None) is None
    assert S.compute_rs_momentum(pd.Series([1.0, 2.0])) is None
