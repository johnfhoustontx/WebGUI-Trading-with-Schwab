"""Momentum components — the five measures, z-score, blend, percentile (PURE).

Daily closes in, ``float | None`` out. No I/O, no tk, no proxy. Every helper
returns None on insufficient data rather than a silent 0 — a zero would be a
real (bad) momentum reading and would drag the cross-sectional distribution.

Each component is computed identically at all three levels (sector, industry,
stock) so the numbers stay comparable down the cascade.
"""
from __future__ import annotations

import math

import numpy as np

TRADING_DAYS = 252

TREND_WINDOW = 90
RS_WINDOW = 63
ACCEL_FAST = 21
ACCEL_SLOW = 63
PATH_WINDOW = 63
PARTICIPATION_SMA = 50

Z_CLIP = 3.0

# path_quality blends two 0..1 terms; the Sharpe-like term is squashed through
# tanh so an unbounded ratio cannot dominate the bounded up-day fraction.
PATH_UP_WEIGHT = 0.5
PATH_SHARPE_WEIGHT = 0.5
SHARPE_SCALE = 3.0

MOMENTUM_WEIGHTS = {
    "trend":         0.30,   # Clenow slope x R^2 — R^2 discards gappy news moves
    "rs":            0.25,   # excess vs SPY + RS-line slope
    "accel":         0.20,   # starting vs finished
    "path":          0.15,   # smooth advances persist
    "participation": 0.10,   # undefined at stock level -> blend renormalizes
}
assert abs(sum(MOMENTUM_WEIGHTS.values()) - 1.0) < 1e-9, \
    f"MOMENTUM_WEIGHTS must sum to 1.0, got {sum(MOMENTUM_WEIGHTS.values())}"


def _clean(closes):
    """Positive, finite closes as an ndarray; None if any entry fails."""
    try:
        arr = np.asarray(list(closes), dtype=float)
    except (TypeError, ValueError):
        return None
    if arr.size == 0 or not np.all(np.isfinite(arr)) or np.any(arr <= 0):
        return None
    return arr


def _ols(x, y):
    """(slope, r2) of y on x; r2 is 0.0 when y has no variance to explain."""
    n = len(x)
    x_mean, y_mean = x.mean(), y.mean()
    sxx = float(((x - x_mean) ** 2).sum())
    if sxx <= 0:
        return None, 0.0
    slope = float(((x - x_mean) * (y - y_mean)).sum() / sxx)
    ss_tot = float(((y - y_mean) ** 2).sum())
    if ss_tot <= 0:
        return slope, 0.0
    resid = y - (y_mean + slope * (x - x_mean))
    r2 = 1.0 - float((resid ** 2).sum()) / ss_tot
    return slope, max(0.0, min(1.0, r2))


def _pct_return(arr, n):
    """Simple return over the last n bars; None if the base is unusable."""
    if arr is None or len(arr) < n + 1:
        return None
    base = float(arr[-1 - n])
    if base <= 0:
        return None
    return float(arr[-1]) / base - 1.0


def trend_strength(closes, n=TREND_WINDOW):
    """Annualized exp-regression slope of log(close), scaled by R^2."""
    arr = _clean(closes)
    if arr is None or len(arr) < n or n < 2:
        return None
    y = np.log(arr[-n:])
    x = np.arange(n, dtype=float)
    slope, r2 = _ols(x, y)
    if slope is None:
        return None
    annualized = math.exp(slope * TRADING_DAYS) - 1.0
    out = annualized * r2
    return out if math.isfinite(out) else None


def relative_strength(closes, bench_closes, n=RS_WINDOW):
    """(excess return vs benchmark, OLS slope of the normalized RS line).

    Absolute return alone just re-ranks beta, so the pair is the component:
    how far ahead, and whether the lead is still widening.
    """
    arr, bench = _clean(closes), _clean(bench_closes)
    if arr is None or bench is None:
        return None, None
    m = min(len(arr), len(bench))
    if m < n + 1:
        return None, None
    arr, bench = arr[-m:], bench[-m:]
    r_sym, r_bench = _pct_return(arr, n), _pct_return(bench, n)
    if r_sym is None or r_bench is None:
        return None, None
    rs = arr[-(n + 1):] / bench[-(n + 1):]
    if rs[0] <= 0 or not np.all(np.isfinite(rs)):
        return None, None
    slope, _ = _ols(np.arange(len(rs), dtype=float), rs / rs[0])
    return r_sym - r_bench, slope


def acceleration(closes, fast=ACCEL_FAST, slow=ACCEL_SLOW):
    """r21 - r63/3 — separates a move that is starting from one that is finished."""
    arr = _clean(closes)
    if arr is None or len(arr) < slow + 1:
        return None
    r_fast, r_slow = _pct_return(arr, fast), _pct_return(arr, slow)
    if r_fast is None or r_slow is None:
        return None
    return r_fast - r_slow / (slow / fast)


def path_quality(closes, n=PATH_WINDOW):
    """Up-day fraction blended with a squashed return/realized-vol ratio, 0..1."""
    arr = _clean(closes)
    if arr is None or len(arr) < n + 1 or n < 2:
        return None
    window = arr[-(n + 1):]
    log_rets = np.diff(np.log(window))
    if not np.all(np.isfinite(log_rets)):
        return None
    pct_up = float((log_rets > 0).mean())
    mean, sd = float(log_rets.mean()), float(log_rets.std(ddof=0))
    if sd <= 0:                       # a perfectly smooth path is the ideal case
        sharpe_term = 1.0 if mean > 0 else (0.0 if mean < 0 else 0.5)
    else:
        sharpe = mean / sd * math.sqrt(TRADING_DAYS)
        sharpe_term = 0.5 * (1.0 + math.tanh(sharpe / SHARPE_SCALE))
    out = PATH_UP_WEIGHT * pct_up + PATH_SHARPE_WEIGHT * sharpe_term
    return out if math.isfinite(out) else None


def participation(constituent_closes, n=PARTICIPATION_SMA):
    """Fraction of constituents trading above their own n-day SMA.

    Constituents without n bars are skipped, not counted as below — a short
    history is missing evidence, not bearish evidence.
    """
    above = usable = 0
    for series in constituent_closes or []:
        arr = _clean(series)
        if arr is None or len(arr) < n:
            continue
        usable += 1
        if float(arr[-1]) > float(arr[-n:].mean()):
            above += 1
    if usable == 0:
        return None
    return above / usable


def _present(values):
    return [(i, float(v)) for i, v in enumerate(values)
            if v is not None and math.isfinite(v)]


def zscore_within_level(values, clip=Z_CLIP):
    """Cross-sectional z-scores, clipped to +/-clip. None slots stay None."""
    out = [None] * len(values)
    present = _present(values)
    if not present:
        return out
    arr = np.array([v for _, v in present], dtype=float)
    mean, sd = float(arr.mean()), float(arr.std(ddof=0))
    for idx, v in present:
        z = 0.0 if sd <= 0 else (v - mean) / sd
        out[idx] = float(max(-clip, min(clip, z)))
    return out


def blend(components, weights=None):
    """Weighted sum over the components actually present, renormalized to 1.0.

    Renormalizing (rather than substituting a neutral 0) is what makes stock
    level correct: participation is undefined there, and a synthetic 0 would
    bias every stock down against its own peer group.
    """
    weights = MOMENTUM_WEIGHTS if weights is None else weights
    total_w = 0.0
    acc = 0.0
    for key, weight in weights.items():
        value = (components or {}).get(key)
        if value is None or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or weight <= 0:
            continue
        acc += weight * float(value)
        total_w += weight
    if total_w <= 0:
        return None
    return acc / total_w


def percentile_rank(values):
    """0-100 percentile rank within the level. None slots stay None.

    Textbook definition ((below + half the ties) / N), so the sample maximum
    reads below 100 — it is the top of this sample, not of the population.
    """
    out = [None] * len(values)
    present = _present(values)
    if not present:
        return out
    arr = np.array([v for _, v in present], dtype=float)
    n = len(arr)
    for idx, v in present:
        below = int((arr < v).sum())
        ties = int((arr == v).sum())
        out[idx] = 100.0 * (below + 0.5 * ties) / n
    return out
