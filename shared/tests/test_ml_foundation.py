"""The shared machine learning library (``shared/ml``), against references
computed by hand rather than by the code under test.

Design: docs/plans/2026-11-14-ml-foundation-design.md.
"""
import math

import numpy as np
import pytest

from shared.ml import baselines, logistic, metrics, walkforward


# ── logistic ────────────────────────────────────────────────────────────────
def test_sigmoid_is_the_logistic_function():
    for z in (-30.0, -2.0, 0.0, 0.7, 30.0):
        assert logistic.sigmoid(z) == pytest.approx(1.0 / (1.0 + math.exp(-z)), rel=1e-12)


def test_map_fit_with_a_vanishing_prior_is_the_group_log_odds():
    # One intercept per group: group A wins 3 of 4, group B 1 of 5. With no
    # prior to speak of, the fitted log-odds are ln(3) and ln(1/4).
    x = np.array([[1.0, 0.0]] * 4 + [[0.0, 1.0]] * 5)
    y = np.array([1, 1, 1, 0, 1, 0, 0, 0, 0], dtype=float)
    w = np.ones(9)
    theta = logistic.map_fit(x, y, w, np.zeros(2), np.full(2, 1e-10))
    assert theta[0] == pytest.approx(math.log(3.0), abs=1e-6)
    assert theta[1] == pytest.approx(math.log(0.25), abs=1e-6)


def test_map_fit_with_no_data_weight_keeps_the_prior_mean():
    x = np.array([[1.0, 0.5], [1.0, -0.5]])
    y = np.array([1.0, 0.0])
    mean = np.array([0.3, -0.7])
    theta = logistic.map_fit(x, y, np.zeros(2), mean, np.ones(2))
    assert theta == pytest.approx(mean)


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_map_fit_refuses_to_end_on_non_finite_weights():
    x = np.array([[float("nan")]])
    with pytest.raises(ValueError):
        logistic.map_fit(x, np.array([1.0]), np.ones(1), np.zeros(1), np.ones(1))


def test_data_share_is_between_zero_and_one_and_zero_for_an_unread_column():
    x = np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
    w = np.ones(3)
    prec = np.ones(2)
    theta = logistic.map_fit(x, np.array([1.0, 0.0, 1.0]), w, np.zeros(2), prec)
    share = logistic.data_share(x, w, theta, prec)
    assert 0.0 < share[0] < 1.0
    assert share[1] == 0.0


# ── walk-forward ────────────────────────────────────────────────────────────
def test_walk_forward_never_fits_on_the_period_it_scores():
    period = [i // 2 for i in range(10)]        # five periods of two items
    fitted = []

    def fit_fn(train):
        fitted.append(list(train))
        return max(period[j] for j in train)

    scored, periods = walkforward.walk_forward(10, period.__getitem__, 2, fit_fn,
                                               lambda model, j: (model, j))
    assert periods == 3
    assert [j for j, _ in scored] == [4, 5, 6, 7, 8, 9]
    for j, (latest_trained, _j) in scored:
        assert latest_trained < period[j]
    assert fitted[0] == [0, 1, 2, 3]


def test_walk_forward_skips_a_period_with_nothing_to_score():
    period = [0, 0, 1, 1, 2, 2]
    scored, periods = walkforward.walk_forward(
        6, period.__getitem__, 1, lambda train: None, lambda m, j: j,
        keep=lambda j: j != 2 and j != 3)
    assert periods == 1
    assert [j for j, _ in scored] == [4, 5]


def test_walk_forward_with_too_few_periods_scores_nothing():
    scored, periods = walkforward.walk_forward(
        4, [0, 0, 1, 1].__getitem__, 2, lambda t: None, lambda m, j: j)
    assert scored == [] and periods == 0


# ── baselines ───────────────────────────────────────────────────────────────
def test_usual_rate_is_the_weighted_share_on_strictly_earlier_days():
    groups = ["a", "a", "a", "a", "b"]
    days = ["d1", "d1", "d2", "d3", "d1"]
    wins = [1, 0, 1, 0, 1]
    weights = [1.0, 3.0, 2.0, 1.0, 1.0]
    got = baselines.usual_rates(groups, days, wins, weights)
    # d1 has no earlier day; d2 sees d1 (1 of weight 4); d3 sees d1 + d2 (3 of 6).
    assert got == [None, None, pytest.approx(0.25), pytest.approx(0.5), None]


def test_usual_rate_of_nothing_is_empty():
    assert baselines.usual_rates([], [], [], []) == []


# ── metrics ─────────────────────────────────────────────────────────────────
def test_brier_is_the_weighted_mean_squared_error():
    assert metrics.brier([0.8, 0.3], [1, 0], [1.0, 1.0]) == pytest.approx(0.065)
    assert metrics.brier([0.8, 0.3], [1, 0], [1.0, 3.0]) == pytest.approx(0.0775)


def test_log_loss_is_the_weighted_mean_negative_log_likelihood():
    want = -(math.log(0.8) + math.log(0.7)) / 2
    assert metrics.log_loss([0.8, 0.3], [1, 0], [1.0, 1.0]) == pytest.approx(want)


def test_log_loss_of_a_certain_miss_is_finite():
    assert math.isfinite(metrics.log_loss([1.0, 0.0], [0, 1], [1.0, 1.0]))


@pytest.mark.parametrize("fn", [metrics.brier, metrics.log_loss])
def test_scores_of_no_rows_or_no_weight_are_none(fn):
    assert fn([], [], []) is None
    assert fn([0.5], [1], [0.0]) is None
    assert fn([0.5], [1], [float("nan")]) is None


def test_reliability_bins_by_tenth_of_the_prediction():
    table = metrics.reliability([0.05, 0.95, 0.97, 1.0], [0, 1, 0, 1], [1, 1, 1, 1])
    assert len(table) == 10
    assert table[0] == [0.0, 0.1, pytest.approx(0.05), 0.0, 1]
    assert table[9] == [0.9, 1.0, pytest.approx((0.95 + 0.97 + 1.0) / 3),
                        pytest.approx(2 / 3), 3]
    assert all(row[2] is None and row[3] is None and row[4] == 0 for row in table[1:9])


def test_reliability_of_nothing_is_ten_empty_tenths():
    table = metrics.reliability([], [], [])
    assert [row[4] for row in table] == [0] * 10
    assert table[3][:2] == [0.3, 0.4]


def test_day_bootstrap_of_a_constant_difference_is_that_difference():
    days = ["d1", "d1", "d2", "d3", "d4", "d5"]
    out = metrics.day_bootstrap_diff([0.3] * 6, [0.2] * 6, days, [1.0] * 6)
    assert out["days"] == 5
    assert out["mean"] == pytest.approx(0.1)
    assert out["lo"] == pytest.approx(0.1) and out["hi"] == pytest.approx(0.1)


def test_day_bootstrap_resamples_days_not_rows():
    # Day d1 holds 50 rows that all favour model A by 1.0; the other four days
    # hold one row each favouring B by 1.0. Resampling rows would put the
    # interval near the row mean; resampling days must let it reach below 0.
    days = ["d1"] * 50 + ["d2", "d3", "d4", "d5"]
    a = [0.0] * 50 + [1.0] * 4
    b = [1.0] * 50 + [0.0] * 4
    out = metrics.day_bootstrap_diff(a, b, days, [1.0] * 54, seed=3)
    assert out["mean"] == pytest.approx((-50 + 4) / 54)
    assert out["lo"] < out["mean"] < out["hi"]
    assert out["hi"] > 0.0


def test_day_bootstrap_with_one_day_is_its_point_estimate():
    out = metrics.day_bootstrap_diff([0.4, 0.2], [0.1, 0.1], ["d1", "d1"], [1.0, 1.0])
    assert out == {"mean": pytest.approx(0.2), "lo": pytest.approx(0.2),
                   "hi": pytest.approx(0.2), "days": 1}


def test_day_bootstrap_of_nothing_is_none():
    assert metrics.day_bootstrap_diff([], [], [], []) is None


def test_the_library_imports_no_service_store_or_config():
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "ml"
    allowed = {"math", "numpy", "collections", "random", "shared.ml", "shared.numeric",
               "__future__"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            for name in names:
                assert any(name == a or name.startswith(a + ".") for a in allowed), \
                    f"{path.name} imports {name}"
