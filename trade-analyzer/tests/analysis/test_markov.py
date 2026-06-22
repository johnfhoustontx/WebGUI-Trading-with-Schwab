import numpy as np
import pytest
from src.analysis import markov


def test_band_constants():
    assert markov.N_BANDS == 5
    assert len(markov.BAND_LABELS) == 5
    assert len(markov.BAND_MIDPOINTS) == 5
    assert markov.BAND_EDGES == [-40.0, -15.0, 15.0, 40.0]


@pytest.mark.parametrize("score,band", [
    (-100, 0), (-40.01, 0), (-40.0, 1), (-15.01, 1), (-15.0, 2),
    (0, 2), (14.99, 2), (15.0, 3), (39.99, 3), (40.0, 4), (100, 4),
    (250, 4), (-250, 0),
])
def test_classify_band(score, band):
    assert markov.classify_band(score) == band


def test_count_matrix_known_sequence():
    bands = [2, 2, 3, 4, 4, 3]
    C = markov.count_matrix(bands)
    assert C.shape == (5, 5)
    assert C[2, 2] == 1
    assert C[2, 3] == 1
    assert C[3, 4] == 1
    assert C[4, 4] == 1
    assert C[4, 3] == 1
    assert C.sum() == 5


def test_count_matrix_ignores_nan_and_short():
    assert markov.count_matrix([]).sum() == 0
    assert markov.count_matrix([3]).sum() == 0
    C = markov.count_matrix([2, np.nan, 4, 4])
    assert C.sum() == 1 and C[4, 4] == 1


def test_pooled_prior_rows_sum_to_one():
    C = np.ones((5, 5)) * 3
    P = markov.pooled_prior(C)
    np.testing.assert_allclose(P.sum(axis=1), 1.0)


def test_pooled_prior_empty_row_uniform():
    C = np.zeros((5, 5))
    C[0] = [10, 0, 0, 0, 0]
    P = markov.pooled_prior(C)
    np.testing.assert_allclose(P[1], np.full(5, 0.2))
    np.testing.assert_allclose(P[0], [1, 0, 0, 0, 0])


def test_shrink_thin_row_leans_on_prior():
    prior = np.full((5, 5), 0.2)
    C_sym = np.zeros((5, 5))
    C_sym[2] = [0, 0, 1, 0, 0]
    P = markov.shrink(C_sym, prior, alpha=30.0)
    assert P[2, 2] < 0.35
    np.testing.assert_allclose(P.sum(axis=1), 1.0)


def test_shrink_rich_row_dominated_by_data():
    prior = np.full((5, 5), 0.2)
    C_sym = np.zeros((5, 5))
    C_sym[2] = [0, 0, 300, 0, 0]
    P = markov.shrink(C_sym, prior, alpha=30.0)
    assert P[2, 2] > 0.85


def test_project_identity_and_powers():
    P = np.eye(5)
    dist0 = np.array([0, 0, 1.0, 0, 0])
    np.testing.assert_allclose(markov.project(P, dist0, 1), dist0)
    np.testing.assert_allclose(markov.project(P, dist0, 10), dist0)


def test_project_one_step_is_row():
    P = markov.shrink(np.zeros((5, 5)), np.full((5, 5), 0.2), alpha=1.0)
    dist = markov.project(P, np.eye(5)[2], 1)
    np.testing.assert_allclose(dist, P[2])
    np.testing.assert_allclose(dist.sum(), 1.0)


def test_forecast_shape_and_metrics():
    prior = np.full((5, 5), 0.2)
    C = np.zeros((5, 5)); C[3] = [0, 0, 5, 20, 75]
    P = markov.shrink(C, prior, alpha=10.0)
    fc = markov.forecast(P, current_band=3, horizons=[5, 10, 20])
    assert [h["n"] for h in fc["horizons"]] == [5, 10, 20]
    for h in fc["horizons"]:
        np.testing.assert_allclose(sum(h["dist"]), 1.0, atol=1e-9)
        assert 0.0 <= h["p_buy"] <= 1.0 and 0.0 <= h["p_sell"] <= 1.0
        assert -100.0 <= h["e_score"] <= 100.0
    assert 0.0 <= fc["persistence"] <= 1.0
    np.testing.assert_allclose(sum(fc["stationary"]), 1.0, atol=1e-6)
    assert fc["current_band"] == 3


def test_drift_tilt_clamped_and_signed():
    fc = {"horizons": [{"n": 10, "e_score": 60.0}]}
    t = markov.drift_tilt(fc, composite_daily_now=0.0, horizon=10,
                          k=1.0, max_pts=12.0, confidence=1.0)
    assert t == pytest.approx(12.0)
    fc2 = {"horizons": [{"n": 10, "e_score": -50.0}]}
    assert markov.drift_tilt(fc2, 0.0, 10, 1.0, 12.0, 1.0) == pytest.approx(-12.0)


def test_drift_tilt_flat_is_zero():
    fc = {"horizons": [{"n": 10, "e_score": 5.0}]}
    assert markov.drift_tilt(fc, composite_daily_now=5.0, horizon=10,
                             k=1.0, max_pts=12.0, confidence=1.0) == 0.0


def test_drift_tilt_scales_with_confidence():
    fc = {"horizons": [{"n": 10, "e_score": 20.0}]}
    full = markov.drift_tilt(fc, 0.0, 10, 1.0, 12.0, confidence=1.0)
    half = markov.drift_tilt(fc, 0.0, 10, 1.0, 12.0, confidence=0.5)
    assert half == pytest.approx(full * 0.5)
    assert markov.drift_tilt(fc, 0.0, 10, 1.0, 12.0, confidence=0.0) == 0.0


def test_row_confidence_monotonic():
    lo = markov.row_confidence(np.array([0, 0, 2, 0, 0]), kappa=40.0)
    hi = markov.row_confidence(np.array([0, 0, 200, 0, 0]), kappa=40.0)
    assert 0.0 <= lo < hi <= 1.0


def test_stationary_doubly_stochastic_is_uniform():
    # a doubly-stochastic matrix has the uniform stationary distribution
    P = np.full((5, 5), 0.2)
    s = markov._stationary(P)
    np.testing.assert_allclose(s, np.full(5, 0.2), atol=1e-6)


def test_stationary_is_a_genuine_fixed_point():
    prior = np.full((5, 5), 0.2)
    C = np.zeros((5, 5)); C[1] = [0, 5, 3, 0, 0]; C[3] = [0, 0, 2, 6, 4]
    P = markov.shrink(C, prior, alpha=8.0)
    s = markov._stationary(P)
    assert (s >= 0).all()
    np.testing.assert_allclose(s.sum(), 1.0, atol=1e-9)
    np.testing.assert_allclose(s @ P, s, atol=1e-6)  # d·P = d


def test_stationary_reducible_returns_valid_distribution():
    # reducible chain (absorbing 0,1,2; {3,4} an internal class) -> the old
    # abs()-of-eigenvector path could corrupt this; power iteration must still
    # return a valid, non-negative, sum-1 fixed point.
    P = np.array([
        [1, 0, 0, 0, 0],
        [0, 1, 0, 0, 0],
        [0, 0, 1, 0, 0],
        [0, 0, 0, 0.5, 0.5],
        [0, 0, 0, 0.5, 0.5],
    ], dtype=float)
    s = markov._stationary(P)
    assert (s >= 0).all() and np.isfinite(s).all()
    np.testing.assert_allclose(s.sum(), 1.0, atol=1e-9)
    np.testing.assert_allclose(s @ P, s, atol=1e-6)


def test_stationary_bad_input_falls_back_uniform():
    bad = np.full((5, 5), np.nan)
    np.testing.assert_allclose(markov._stationary(bad), np.full(5, 0.2))


def test_project_rejects_nonpositive_horizon():
    P = np.full((5, 5), 0.2)
    with pytest.raises(ValueError):
        markov.project(P, np.eye(5)[2], 0)
    with pytest.raises(ValueError):
        markov.project(P, np.eye(5)[2], -3)


def test_forecast_skips_nonpositive_horizons():
    P = np.full((5, 5), 0.2)
    fc = markov.forecast(P, current_band=2, horizons=[0, 5, -1, 10])
    assert [h["n"] for h in fc["horizons"]] == [5, 10]


def test_drift_tilt_missing_horizon_is_zero():
    fc = {"horizons": [{"n": 5, "e_score": 50.0}]}
    assert markov.drift_tilt(fc, 0.0, horizon=999, k=1.0, max_pts=12.0, confidence=1.0) == 0.0
    assert markov.drift_tilt({}, 0.0, horizon=10) == 0.0
