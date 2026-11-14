"""How good a probability is: Brier score, log loss, the reliability table,
and a day-clustered bootstrap for comparing two models.

Every function takes parallel sequences (predictions, 0/1 outcomes, weights)
and sums in their order, so a caller that moved here from its own loop gets the
same floats it had.
"""
import math

import numpy as np

from shared.numeric import finite

#: The smallest probability a log loss reads, so a certain miss is large but
#: finite (a numerical convention, not a tunable).
_EPS = 1e-15


def _total(w):
    total = sum(w)
    return total if math.isfinite(total) and total > 0 else None


def brier(p, y, w) -> float | None:
    """The weighted mean of ``(p - y)**2``; ``None`` with no positive finite
    total weight."""
    total = _total(w)
    if total is None:
        return None
    return sum(wi * (pi - yi) ** 2 for pi, yi, wi in zip(p, y, w)) / total


def log_loss(p, y, w) -> float | None:
    """The weighted mean negative log-likelihood, ``p`` held inside
    ``[1e-15, 1 - 1e-15]``; ``None`` with no positive finite total weight."""
    total = _total(w)
    if total is None:
        return None
    acc = 0.0
    for pi, yi, wi in zip(p, y, w):
        q = min(max(pi, _EPS), 1.0 - _EPS)
        acc += -wi * (yi * math.log(q) + (1 - yi) * math.log(1.0 - q))
    return acc / total


def reliability(p, y, w, bins=10) -> list:
    """``[lo, hi, mean_p, rate, n]`` per equal slice of the predicted
    probability (tenths by default): the weighted mean prediction and the
    weighted observed rate of the items in it, ``None`` for an empty slice. A
    prediction of exactly 1 joins the top slice; an item with a non-finite
    prediction or weight is left out."""
    acc = [[0.0, 0.0, 0.0, 0] for _ in range(bins)]    # weight, w*p, w*y, n
    for pi, yi, wi in zip(p, y, w):
        if finite(pi) is None or finite(wi) is None:
            continue
        b = acc[min(int(pi * bins), bins - 1)]
        b[0] += wi
        b[1] += wi * pi
        b[2] += wi * yi
        b[3] += 1
    return [[round(k / bins, 1), round((k + 1) / bins, 1),
             b[1] / b[0] if b[0] > 0 else None,
             b[2] / b[0] if b[0] > 0 else None, b[3]]
            for k, b in enumerate(acc)]


def day_bootstrap_diff(loss_a, loss_b, days, w, n_boot=2000, seed=0, level=0.95):
    """The weighted mean of ``loss_a - loss_b`` per item, with an interval from
    resampling whole DAYS with replacement (each day's rows travel together, so
    a day of many correlated rows counts once, as it should).

    Returns ``{"mean", "lo", "hi", "days"}``; ``None`` with no rows or no
    positive total weight. Negative means model A's loss is lower."""
    sums = {}
    for a, b, d, wi in zip(loss_a, loss_b, days, w):
        s = sums.setdefault(d, [0.0, 0.0])
        s[0] += wi * (a - b)
        s[1] += wi
    if not sums:
        return None
    diff = np.array([s[0] for s in sums.values()])
    weight = np.array([s[1] for s in sums.values()])
    total = float(weight.sum())
    if not math.isfinite(total) or total <= 0:
        return None
    mean = float(diff.sum()) / total
    k = len(sums)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, k, size=(n_boot, k))
    stats = diff[pick].sum(axis=1) / weight[pick].sum(axis=1)
    tail = (1.0 - level) / 2.0
    lo, hi = (float(v) for v in np.quantile(stats, [tail, 1.0 - tail]))
    return {"mean": mean, "lo": min(lo, mean), "hi": max(hi, mean), "days": k}


__all__ = ["brier", "log_loss", "reliability", "day_bootstrap_diff"]
