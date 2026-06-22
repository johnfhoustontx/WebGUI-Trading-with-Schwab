"""Pure Markov-chain math over PositionVerdict composite-score bands.

No I/O, no proxy, no indicator fetch — every function takes plain arrays/scalars
so the whole module is trivially unit-testable. The data-dependent score
reconstruction that *feeds* this lives in ``services/trade_svc/compute.py``.

States = 5 contiguous score bands whose internal edges are the verdict's BUY/SELL
cut points (+-40) and the neutral zone (+-15), so a forecast directly yields
P(cross into BUY) / P(cross into SELL).
"""
from typing import List

import numpy as np

N_BANDS = 5
BAND_LABELS = ["Strong-Bear", "Weak-Bear", "Neutral", "Weak-Bull", "Strong-Bull"]
BAND_EDGES = [-40.0, -15.0, 15.0, 40.0]
BAND_MIDPOINTS = [-70.0, -27.5, 0.0, 27.5, 70.0]
BUY_BAND = 4
SELL_BAND = 0


def classify_band(score: float) -> int:
    """Map a composite score in [-100,100] to a band index 0..4 (clamped)."""
    s = float(np.clip(score, -100.0, 100.0))
    return int(np.searchsorted(BAND_EDGES, s, side="right"))


def count_matrix(bands) -> np.ndarray:
    """Count day-to-day transitions from a sequence of band indices.

    NaN/None entries break the chain (no transition spans a gap), so a series
    with missing bars never invents a transition across the gap.
    """
    C = np.zeros((N_BANDS, N_BANDS), dtype=float)
    prev = None
    for b in bands:
        if b is None or (isinstance(b, float) and np.isnan(b)):
            prev = None
            continue
        b = int(b)
        if prev is not None:
            C[prev, b] += 1
        prev = b
    return C


def pooled_prior(C: np.ndarray) -> np.ndarray:
    """Row-normalize a pooled count matrix to a prior probability matrix.

    Empty rows (a band never observed in the universe) fall back to uniform so
    the prior is always a valid stochastic matrix.
    """
    P = np.array(C, dtype=float)
    rowsums = P.sum(axis=1, keepdims=True)
    out = np.divide(P, rowsums, out=np.full_like(P, 1.0 / N_BANDS),
                    where=rowsums > 0)
    return out


def shrink(C_sym: np.ndarray, prior: np.ndarray, alpha: float = 30.0) -> np.ndarray:
    """Dirichlet-multinomial blend of per-symbol counts toward a prior.

    P[i,j] = (C[i,j] + alpha*prior[i,j]) / (rowsum(C[i]) + alpha).
    Thin rows lean on the prior; data-rich rows are dominated by own counts.
    """
    C = np.array(C_sym, dtype=float)
    rowsums = C.sum(axis=1, keepdims=True)
    P = (C + alpha * prior) / (rowsums + alpha)
    return P


def project(P: np.ndarray, dist0: np.ndarray, n: int) -> np.ndarray:
    """Distribution after n steps: dist0 @ P^n."""
    if int(n) < 1:
        raise ValueError("project horizon n must be >= 1")
    Pn = np.linalg.matrix_power(np.array(P, dtype=float), int(n))
    return np.array(dist0, dtype=float) @ Pn


def _stationary(P: np.ndarray) -> np.ndarray:
    """Long-run stationary distribution via power iteration.

    Robust to the reducible/degenerate cases an eigen-decomposition mishandles:
    it returns a genuine non-negative, sum-1 fixed point d ≈ d·P for the
    (aperiodic, shrinkage-regularized) matrices this module produces, and falls
    back to uniform on malformed input (wrong shape / non-finite values).
    """
    P = np.asarray(P, dtype=float)
    uniform = np.full(N_BANDS, 1.0 / N_BANDS)
    if P.shape != (N_BANDS, N_BANDS) or not np.all(np.isfinite(P)):
        return uniform
    d = uniform.copy()
    for _ in range(2000):
        nxt = d @ P
        s = nxt.sum()
        if s <= 0 or not np.all(np.isfinite(nxt)):
            return uniform
        nxt = nxt / s
        if np.max(np.abs(nxt - d)) < 1e-12:
            return nxt
        d = nxt
    return d


def forecast(P: np.ndarray, current_band: int, horizons: List[int]) -> dict:
    """Forecast band distribution + derived metrics from the current band."""
    P = np.asarray(P, dtype=float)
    mids = np.array(BAND_MIDPOINTS)
    dist0 = np.eye(N_BANDS)[int(current_band)]
    horizons = [int(n) for n in horizons if int(n) >= 1]
    hs = []
    for n in horizons:
        d = project(P, dist0, n)
        hs.append({
            "n": int(n),
            "dist": [float(x) for x in d],
            "p_buy": float(d[BUY_BAND]),
            "p_sell": float(d[SELL_BAND]),
            "e_score": float(d @ mids),
        })
    return {
        "current_band": int(current_band),
        "transition_row": [float(x) for x in P[int(current_band)]],
        "persistence": float(P[int(current_band), int(current_band)]),
        "horizons": hs,
        "stationary": [float(x) for x in _stationary(P)],
    }


def row_confidence(row_counts: np.ndarray, kappa: float = 40.0) -> float:
    """Confidence in the current band's transition row from its effective sample
    size: n/(n+kappa) -> 0 when unseen, ->1 with many observations."""
    n = max(0.0, float(np.asarray(row_counts, dtype=float).sum()))
    return float(n / (n + kappa))


def drift_tilt(forecast_dict: dict, composite_daily_now: float, horizon: int,
               k: float = 0.5, max_pts: float = 12.0, confidence: float = 1.0) -> float:
    """Bounded, confidence-weighted tilt = clip(k*(E[score@h]-now)) * confidence."""
    h = next((x for x in forecast_dict.get("horizons", []) if x["n"] == horizon), None)
    if h is None:
        return 0.0
    drift = h["e_score"] - float(composite_daily_now)
    tilt = float(np.clip(k * drift, -max_pts, max_pts))
    return tilt * float(confidence)
