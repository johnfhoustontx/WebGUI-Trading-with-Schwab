"""shared/ml/linear.py (ridge) and the quantile helpers in shared/ml/metrics.py."""
import numpy as np
import pytest

from shared.ml import linear, metrics


def test_ridge_without_penalty_recovers_an_exact_line():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(50, 2))
    y = 1.5 + 2.0 * x[:, 0] - 0.5 * x[:, 1]
    model = linear.ridge(x, y, np.ones(50), 0.0)
    assert np.allclose(linear.predict(model, x), y)
    assert np.allclose(linear.predict(model, np.array([[0.0, 0.0]])), [1.5])


def test_a_large_penalty_shrinks_toward_the_weighted_mean():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(40, 3))
    y = 3.0 + x @ np.array([1.0, -2.0, 0.5])
    w = rng.uniform(0.5, 2.0, size=40)
    pred = linear.predict(linear.ridge(x, y, w, 1e9), x)
    assert np.allclose(pred, np.average(y, weights=w), atol=1e-4)


def test_a_constant_column_does_not_divide_by_zero():
    x = np.column_stack([np.arange(10.0), np.full(10, 4.0)])
    y = 2.0 * np.arange(10.0)
    pred = linear.predict(linear.ridge(x, y, np.ones(10), 0.0), x)
    assert np.all(np.isfinite(pred)) and np.allclose(pred, y)


def test_weights_count():
    x = np.array([[0.0], [0.0], [1.0]])
    y = np.array([0.0, 2.0, 5.0])
    # The second row weighs nothing: the line runs through (0, 0) and (1, 5).
    model = linear.ridge(x, y, np.array([1.0, 0.0, 1.0]), 0.0)
    assert np.allclose(linear.predict(model, np.array([[0.0], [1.0]])), [0.0, 5.0])


def test_pinball_of_a_perfect_forecast_is_zero():
    y = [1.0, 2.0, 3.0]
    assert metrics.pinball(0.9, y, y, [1, 1, 1]) == 0.0


def test_pinball_hand_case():
    # q = 0.9: under-forecast by 1 costs 0.9, over-forecast by 2 costs 0.2.
    got = metrics.pinball(0.9, [0.0, 2.0], [1.0, 0.0], [1.0, 1.0])
    assert got == pytest.approx((0.9 + 0.2) / 2)
    assert metrics.pinball(0.5, [0.0], [1.0], [0.0]) is None


def test_weighted_quantile_matches_numpy_on_equal_weights():
    v = np.random.default_rng(2).normal(size=101)
    for q in (0.1, 0.5, 0.9):
        assert metrics.weighted_quantile(v, np.ones(101), q) == pytest.approx(
            float(np.quantile(v, q, method="inverted_cdf")))


def test_weighted_quantile_follows_weight():
    assert metrics.weighted_quantile([1.0, 2.0, 3.0], [0.0, 0.0, 1.0], 0.1) == 3.0
    assert metrics.weighted_quantile([], [], 0.5) is None
