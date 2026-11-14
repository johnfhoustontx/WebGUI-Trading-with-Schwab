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


#: The fewest readings per value bin, on the smaller side: a sample too small
#: for ten bins gets fewer (a statistical convention, not a tunable).
_PER_BIN = 5
#: Added to every bin's count before it becomes a share, so an empty bin on one
#: side gives a finite term (additive smoothing; a convention, not a tunable).
_SMOOTH = 0.5


def psi(reference, recent, bins=10) -> dict:
    """The population stability index of ``recent`` against ``reference``:
    how far the spread of an input has moved, ``sum((a - e) * ln(a / e))`` over
    bins, ``e`` the reference's share and ``a`` the recent one's.

    Value bins are cut at the reference's quantiles (taken at data points, so a
    two-valued input such as a 0/1 flag gets one edge, not nine), at most
    ``bins`` of them and fewer for a small sample (about five readings a bin on
    the smaller side), plus one bin for a missing reading (``None`` or not
    finite). So a reading that stops arriving moves the index and shows in
    ``missing_after``, rather than being read as a change in its value. Counts
    are smoothed by one half before they become shares. Returns ``{"psi",
    "missing_before", "missing_after"}``; ``psi`` and the shares are None when a
    side is empty.

    ⚠ On small samples the index is mostly noise; :func:`psi_by_session` says
    whether a reading is beyond it."""
    ref = list(reference or ())
    new = list(recent or ())
    if not ref or not new:
        return {"psi": None,
                "missing_before": _missing_share(ref),
                "missing_after": _missing_share(new)}
    ref_all = _as_floats(ref)
    ref_vals = ref_all[np.isfinite(ref_all)]
    new_n = int(np.count_nonzero(np.isfinite(_as_floats(new))))
    k = max(1, min(bins, ref_vals.size // _PER_BIN, new_n // _PER_BIN))
    if k > 1:
        qs = [j / k for j in range(1, k)]
        edges = np.unique(np.quantile(ref_vals, qs, method="higher"))
    else:
        edges = np.array([])
    n_bins = len(edges) + 2                        # value bins, then "missing"

    def shares(sample):
        vals = _as_floats(sample)
        ok = np.isfinite(vals)
        counts = np.bincount(np.searchsorted(edges, vals[ok], side="right"),
                             minlength=n_bins - 1).astype(float)
        counts = np.append(counts, float(np.count_nonzero(~ok)))
        total = len(sample) + _SMOOTH * n_bins
        return [(c + _SMOOTH) / total for c in counts]

    e, a = shares(ref), shares(new)
    value = float(sum((ai - ei) * math.log(ai / ei) for ai, ei in zip(a, e)))
    return {"psi": value, "missing_before": _missing_share(ref),
            "missing_after": _missing_share(new)}


def psi_by_session(by_session, recent_n, *, n_perm=200, seed=0, bins=10) -> dict:
    """:func:`psi` of the last ``recent_n`` sessions against the ones before, and
    how often a random choice of ``recent_n`` WHOLE sessions moves it as far.

    ``by_session`` is ``{session: [readings]}``; sessions sort as text (ISO
    dates). Whole sessions are reassigned, so the many correlated readings of
    one day travel together. Returns ``{"psi", "p", "missing_before",
    "missing_after"}``: ``p`` is ``(1 + shuffles at or above) / (1 + n_perm)``,
    None when there are not more sessions than ``recent_n`` or ``psi`` is None."""
    sessions = sorted(by_session)
    recent = sessions[-recent_n:] if recent_n > 0 else []
    if len(sessions) <= recent_n or not recent:
        return {"psi": None, "p": None, "missing_before": None, "missing_after": None}

    def split(chosen):
        chosen = set(chosen)
        ref = [x for s in sessions if s not in chosen for x in by_session[s]]
        new = [x for s in sessions if s in chosen for x in by_session[s]]
        return ref, new

    observed = psi(*split(recent), bins=bins)
    if observed["psi"] is None:
        return {**observed, "p": None}
    rng = np.random.default_rng(seed)
    above = 0
    for _ in range(n_perm):
        pick = rng.choice(len(sessions), size=len(recent), replace=False)
        shuffled = psi(*split(sessions[i] for i in pick), bins=bins)["psi"]
        if shuffled is not None and shuffled >= observed["psi"]:
            above += 1
    return {**observed, "p": (1 + above) / (1 + n_perm)}


def _as_floats(sample) -> np.ndarray:
    """``sample`` as floats, NaN for a missing reading (``finite`` decides)."""
    return np.array([v if v is not None else np.nan
                     for v in (finite(x) for x in sample)], dtype=float)


def _missing_share(sample):
    if not sample:
        return None
    return sum(1 for x in sample if finite(x) is None) / len(sample)


__all__ = ["brier", "log_loss", "reliability", "day_bootstrap_diff", "psi",
           "psi_by_session"]
