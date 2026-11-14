"""Logistic regression with a Gaussian prior on every weight (MAP), fitted by
Newton steps.

Moved verbatim from the Opening Scalp's fit (``scalp_prob_fit``, 2026-11-14);
``services/options_svc/tests/test_scalp_prob_fit_golden.py`` holds it to the
last bit of the numbers it produced there.
"""
import numpy as np

#: Newton's stopping rule: a step whose largest move is under ``TOL``, or
#: ``MAX_STEPS`` steps (numerical-solver constants, not tunables).
TOL = 1e-8
MAX_STEPS = 50


def sigmoid(z):
    """The logistic function, written through ``tanh`` so it never overflows."""
    return 0.5 * (1.0 + np.tanh(0.5 * z))


def map_fit(x, y, w, mean, prec, *, tol=TOL, max_steps=MAX_STEPS) -> np.ndarray:
    """The weights minimising the weighted log-loss of ``x @ theta`` against the
    0/1 labels ``y`` plus the penalty of independent Gaussian priors (``mean``,
    precision ``prec``, both one per column), by Newton steps with step halving.

    The objective is strictly convex when every ``prec`` is positive. Starts at
    the prior mean; raises ``ValueError`` when the fit does not end on finite
    weights."""
    def objective(theta):
        z = x @ theta
        return float(np.sum(w * (np.logaddexp(0.0, z) - y * z))
                     + 0.5 * np.sum(prec * (theta - mean) ** 2))

    theta = mean.copy()
    value = objective(theta)
    for _ in range(max_steps):
        p = sigmoid(x @ theta)
        grad = x.T @ (w * (p - y)) + prec * (theta - mean)
        hess = (x.T * (w * p * (1.0 - p))) @ x + np.diag(prec)
        step = np.linalg.solve(hess, grad)
        t = 1.0
        while True:
            cand = theta - t * step
            cand_value = objective(cand)
            if cand_value <= value + 1e-12 * max(1.0, abs(value)) or t < 1e-6:
                break
            t *= 0.5
        theta, value = cand, cand_value
        if float(np.max(np.abs(t * step), initial=0.0)) < tol:
            break
    if not np.all(np.isfinite(theta)):
        raise ValueError("the fit did not end on finite weights")
    return theta


def data_share(x, w, theta, prec) -> np.ndarray:
    """Per column, the share of the posterior precision at ``theta`` that the
    data supplied: ``H_ii / (H_ii + prec_i)``, ``H`` the weighted data Hessian
    ``X^T W diag(p (1 - p)) X``. Near 1 the history decided the weight; under
    one half it is still mostly its prior. In 0..1 when ``prec`` is positive."""
    p = sigmoid(x @ theta)
    h = np.square(x).T @ (w * p * (1.0 - p))
    return h / (h + prec)


__all__ = ["sigmoid", "map_fit", "data_share", "TOL", "MAX_STEPS"]
