"""Volatility indicators — ATR + Bollinger band width (PURE helpers).

Pure functions: number sequences in, a float (or None on thin data) out.
No I/O, no imports beyond numpy. Inputs for the market-regime blended
classifier's volatility axis.

ATR follows the house smoothing convention — Wilder's RMA with an SMA seed
(the same family as ``calculate_rsi``/``calculate_adx`` in
``shared/analysis_lib/technical.py``): the first n true ranges are averaged
to seed, then each subsequent bar applies ``a = (a*(n-1) + tr) / n``.
"""
from __future__ import annotations

import numpy as np

ATR_PERIOD = 14         # default ATR look-back (Wilder's standard)
BOLL_PERIOD = 20        # default Bollinger window
BOLL_K = 2.0            # default Bollinger band width in std-devs
PCTL_MIN_N = 10         # minimum present values for a percentile rank


def atr(highs, lows, closes, n=ATR_PERIOD):
    """Wilder's Average True Range over the full series; None if < n bars.

    TR = max(h-l, |h-prev_close|, |l-prev_close|); the first bar's TR is
    h-l (no prior close). Seed = SMA of the first n TRs, then RMA
    ``a = (a*(n-1) + tr) / n`` across the remaining bars. Accepts lists
    or ndarrays.
    """
    h = np.asarray(highs, dtype=float)
    lo = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)
    if min(len(h), len(lo), len(c)) < n or n < 1:
        return None
    prev_c = c[:-1]
    tr = np.empty(len(c))
    tr[0] = h[0] - lo[0]
    tr[1:] = np.maximum.reduce([
        h[1:] - lo[1:],
        np.abs(h[1:] - prev_c),
        np.abs(lo[1:] - prev_c),
    ])
    a = float(tr[:n].mean())          # SMA seed (house Wilder convention)
    for t in tr[n:]:
        a = (a * (n - 1) + t) / n     # Wilder's RMA
    return a


def bollinger_width_pct(closes, n=BOLL_PERIOD, k=BOLL_K):
    """Bollinger band width as a fraction of the middle band.

    (upper - lower) / middle = ``2*k*std(ddof=0) / mean`` over the LAST n
    closes. None when fewer than n bars or the mean is <= 0.
    """
    c = np.asarray(closes, dtype=float)
    if len(c) < n or n < 1:
        return None
    window = c[-n:]
    mid = float(window.mean())
    if mid <= 0:
        return None
    return float(2.0 * k * window.std(ddof=0) / mid)


def percentile_of_last(values, min_n=PCTL_MIN_N):
    """Rank of the LAST value among the PRIOR values, as a fraction 0..1.

    None entries are filtered out first; the rank is the fraction of prior
    (present) values <= the last present value. None when fewer than
    min_n present values.
    """
    present = [float(v) for v in values if v is not None]
    if len(present) < min_n:
        return None
    last = present[-1]
    prior = present[:-1]
    if not prior:                     # min_n < 2 caller edge: no basis to rank
        return None
    return float(sum(1 for v in prior if v <= last) / len(prior))
