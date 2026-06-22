# Markov 2.0 Trade Analyzer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a probabilistic, forward-looking Markov layer to the Trade Analyzer's
`PositionVerdict` — a visible forecast panel (where the composite score is heading)
plus a bounded "drift tilt" that nudges the verdict.

**Architecture:** Discretize the existing PositionVerdict composite score into 5
bands anchored at the ±40 BUY/SELL cuts. Reconstruct a daily-consistent score
series per symbol (`composite_daily`), count band-to-band transitions, and
Bayesian-shrink them toward a pooled (watchlist-wide) prior. Project via matrix
powers to forecast 5/10/20-day band distributions; the expected forward score
change becomes a bounded ±12-pt tilt on the displayed verdict. Pure Markov math
lives in `trade-analyzer/src/analysis/markov.py`; data-dependent score
reconstruction lives in `services/trade_svc/compute.py`; the result rides the
existing `cache:trade:analysis` envelope to a new card in `webgui/pages/trade.py`.

**Tech Stack:** Python 3.11, numpy, pandas, pytest. Shared `analysis_lib.technical`
indicators, copied `trade-analyzer/src/analysis` scoring primitives, the Redis
`shared.bus` backbone, `shared.contracts`, NiceGUI + Highcharts (`ui.highchart`).

**Key reference docs:** design at
`docs/plans/2026-06-21-markov-trade-analyzer-design.md`; project conventions in the
root `CLAUDE.md` (3-tier rules, NiceGUI/Highcharts gotchas, per-service test
isolation).

**Critical conventions (read before starting):**
- Run each app's tests **from inside its folder** (e.g. `cd trade-analyzer`); run a
  service's tests with the repo-root venv **one service at a time**
  (`.venv\Scripts\python -m pytest services\trade_svc`) — never `pytest services`
  over all of them (cross-app module-name collisions).
- `trade_svc/compute.py` is defensive: every engine call catches and degrades; the
  Markov block must **never** crash `analyze()` — on any failure set `markov: None`.
- Pure functions are TDD'd; `render()`/handlers stay thin. The webgui imports only
  `nicegui` + `shared.bus` + `shared.contracts` — no engine imports.

---

## Phase 1 — Pure Markov core (`markov.py`)

Zero runtime risk: no wiring, fully unit-tested with synthetic series.

### Task 1.1: Band definitions + `classify_band`

**Files:**
- Create: `trade-analyzer/src/analysis/markov.py`
- Test: `trade-analyzer/tests/analysis/test_markov.py`

**Step 1: Write the failing test**

```python
# trade-analyzer/tests/analysis/test_markov.py
import numpy as np
import pytest
from src.analysis import markov


def test_band_constants():
    assert markov.N_BANDS == 5
    assert len(markov.BAND_LABELS) == 5
    assert len(markov.BAND_MIDPOINTS) == 5
    # edges are the 4 internal cut points anchored at the verdict boundaries
    assert markov.BAND_EDGES == [-40.0, -15.0, 15.0, 40.0]


@pytest.mark.parametrize("score,band", [
    (-100, 0), (-40.01, 0), (-40.0, 1), (-15.01, 1), (-15.0, 2),
    (0, 2), (14.99, 2), (15.0, 3), (39.99, 3), (40.0, 4), (100, 4),
    (250, 4), (-250, 0),  # clamps
])
def test_classify_band(score, band):
    assert markov.classify_band(score) == band
```

**Step 2: Run test to verify it fails**

Run: `cd trade-analyzer && python -m pytest tests/analysis/test_markov.py -v`
Expected: FAIL (`ModuleNotFoundError: src.analysis.markov`).

**Step 3: Write minimal implementation**

```python
# trade-analyzer/src/analysis/markov.py
"""Pure Markov-chain math over PositionVerdict composite-score bands.

No I/O, no proxy, no indicator fetch — every function takes plain arrays/scalars
so the whole module is trivially unit-testable. The data-dependent score
reconstruction that *feeds* this lives in ``services/trade_svc/compute.py``.

States = 5 contiguous score bands whose internal edges are the verdict's BUY/SELL
cut points (+-40) and the neutral zone (+-15), so a forecast directly yields
P(cross into BUY) / P(cross into SELL).
"""
from typing import List, Optional

import numpy as np

N_BANDS = 5
BAND_LABELS = ["Strong-Bear", "Weak-Bear", "Neutral", "Weak-Bull", "Strong-Bull"]
# 4 internal edges; bands are [-inf,-40) [-40,-15) [-15,15) [15,40) [40,inf]
BAND_EDGES = [-40.0, -15.0, 15.0, 40.0]
# representative score per band for E[score] (extreme bands use a value inside the
# usable [-100,100] range, not +-inf)
BAND_MIDPOINTS = [-70.0, -27.5, 0.0, 27.5, 70.0]
BUY_BAND = 4
SELL_BAND = 0


def classify_band(score: float) -> int:
    """Map a composite score in [-100,100] to a band index 0..4 (clamped)."""
    s = float(np.clip(score, -100.0, 100.0))
    # np.searchsorted on the internal edges: returns 0..4
    return int(np.searchsorted(BAND_EDGES, s, side="right"))
```

**Step 4: Run test to verify it passes**

Run: `cd trade-analyzer && python -m pytest tests/analysis/test_markov.py -v`
Expected: PASS.

> Note: `classify_band(-40.0)` must be band 1. With `side="right"`,
> `searchsorted([-40,-15,15,40], -40, side="right") == 1`. Good. Verify the
> parametrized cases all pass; adjust `side` only if a boundary case fails.

**Step 5: Commit**

```bash
git add trade-analyzer/src/analysis/markov.py trade-analyzer/tests/analysis/test_markov.py
git commit -m "feat(markov): band constants + classify_band"
```

---

### Task 1.2: `count_matrix` — transition counts from a band sequence

**Files:**
- Modify: `trade-analyzer/src/analysis/markov.py`
- Test: `trade-analyzer/tests/analysis/test_markov.py`

**Step 1: Write the failing test**

```python
def test_count_matrix_known_sequence():
    # bands over time: 2->2->3->4->4->3
    bands = [2, 2, 3, 4, 4, 3]
    C = markov.count_matrix(bands)
    assert C.shape == (5, 5)
    assert C[2, 2] == 1  # 2->2
    assert C[2, 3] == 1  # 2->3
    assert C[3, 4] == 1  # 3->4
    assert C[4, 4] == 1  # 4->4
    assert C[4, 3] == 1  # 4->3
    assert C.sum() == 5  # n-1 transitions


def test_count_matrix_ignores_nan_and_short():
    assert markov.count_matrix([]).sum() == 0
    assert markov.count_matrix([3]).sum() == 0
    # NaN band entries break the chain (no transition counted across a gap)
    C = markov.count_matrix([2, np.nan, 4, 4])
    assert C.sum() == 1 and C[4, 4] == 1
```

**Step 2: Run to verify it fails** — `... -k count_matrix -v` → FAIL (`no attribute`).

**Step 3: Implement**

```python
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
```

**Step 4: Run to verify it passes.** **Step 5: Commit**

```bash
git commit -am "feat(markov): count_matrix transition counter"
```

---

### Task 1.3: `pooled_prior` + `shrink` (Dirichlet-multinomial)

**Files:** Modify `markov.py`; test in `test_markov.py`.

**Step 1: Failing test**

```python
def test_pooled_prior_rows_sum_to_one():
    C = np.ones((5, 5)) * 3
    P = markov.pooled_prior(C)
    np.testing.assert_allclose(P.sum(axis=1), 1.0)


def test_pooled_prior_empty_row_uniform():
    C = np.zeros((5, 5))
    C[0] = [10, 0, 0, 0, 0]  # row 0 has data; others empty
    P = markov.pooled_prior(C)
    np.testing.assert_allclose(P[1], np.full(5, 0.2))  # empty row -> uniform
    np.testing.assert_allclose(P[0], [1, 0, 0, 0, 0])


def test_shrink_thin_row_leans_on_prior():
    prior = np.full((5, 5), 0.2)
    C_sym = np.zeros((5, 5))
    C_sym[2] = [0, 0, 1, 0, 0]  # a single observed 2->2
    P = markov.shrink(C_sym, prior, alpha=30.0)
    # 1 observation vs alpha=30 prior pseudo-counts -> still close to prior
    assert P[2, 2] < 0.35
    np.testing.assert_allclose(P.sum(axis=1), 1.0)


def test_shrink_rich_row_dominated_by_data():
    prior = np.full((5, 5), 0.2)
    C_sym = np.zeros((5, 5))
    C_sym[2] = [0, 0, 300, 0, 0]  # overwhelming evidence 2->2
    P = markov.shrink(C_sym, prior, alpha=30.0)
    assert P[2, 2] > 0.85
```

**Step 2: Run → FAIL.**

**Step 3: Implement**

```python
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
```

**Step 4: Run → PASS.** **Step 5: Commit** `feat(markov): pooled_prior + Dirichlet shrink`.

---

### Task 1.4: `project` + `forecast` (matrix powers, derived metrics)

**Files:** Modify `markov.py`; test in `test_markov.py`.

**Step 1: Failing test**

```python
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
    C = np.zeros((5, 5)); C[3] = [0, 0, 5, 20, 75]  # from Weak-Bull, drifts up
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
```

**Step 2: Run → FAIL.**

**Step 3: Implement**

```python
def project(P: np.ndarray, dist0: np.ndarray, n: int) -> np.ndarray:
    """Distribution after n steps: dist0 @ P^n."""
    Pn = np.linalg.matrix_power(np.array(P, dtype=float), int(n))
    return np.array(dist0, dtype=float) @ Pn


def _stationary(P: np.ndarray) -> np.ndarray:
    """Long-run stationary distribution (left eigenvector for eigenvalue 1),
    falling back to a power-iteration / uniform if the solve is ill-conditioned."""
    P = np.array(P, dtype=float)
    try:
        vals, vecs = np.linalg.eig(P.T)
        idx = int(np.argmin(np.abs(vals - 1.0)))
        v = np.real(vecs[:, idx])
        v = np.abs(v)
        s = v.sum()
        if s > 0:
            return v / s
    except Exception:
        pass
    d = np.full(N_BANDS, 1.0 / N_BANDS)
    for _ in range(1000):
        d = d @ P
    s = d.sum()
    return d / s if s > 0 else np.full(N_BANDS, 1.0 / N_BANDS)


def forecast(P: np.ndarray, current_band: int, horizons: List[int]) -> dict:
    """Forecast band distribution + derived metrics from the current band."""
    mids = np.array(BAND_MIDPOINTS)
    dist0 = np.eye(N_BANDS)[int(current_band)]
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
        "transition_row": [float(x) for x in np.array(P)[int(current_band)]],
        "persistence": float(np.array(P)[int(current_band), int(current_band)]),
        "horizons": hs,
        "stationary": [float(x) for x in _stationary(P)],
    }
```

**Step 4: Run → PASS.** **Step 5: Commit** `feat(markov): project + forecast metrics`.

---

### Task 1.5: `drift_tilt` — bounded, confidence-weighted forward nudge

**Files:** Modify `markov.py`; test in `test_markov.py`.

**Step 1: Failing test**

```python
def test_drift_tilt_clamped_and_signed():
    fc = {"horizons": [{"n": 10, "e_score": 60.0}]}
    # huge expected jump -> clamped at +max_pts
    t = markov.drift_tilt(fc, composite_daily_now=0.0, horizon=10,
                          k=1.0, max_pts=12.0, confidence=1.0)
    assert t == pytest.approx(12.0)
    # downward
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
    # more observed transitions out of the current band -> higher confidence
    lo = markov.row_confidence(np.array([0, 0, 2, 0, 0]), kappa=40.0)
    hi = markov.row_confidence(np.array([0, 0, 200, 0, 0]), kappa=40.0)
    assert 0.0 <= lo < hi <= 1.0
```

**Step 2: Run → FAIL.**

**Step 3: Implement**

```python
def row_confidence(row_counts: np.ndarray, kappa: float = 40.0) -> float:
    """Confidence in the current band's transition row from its effective sample
    size: n/(n+kappa) -> 0 when unseen, ->1 with many observations."""
    n = float(np.asarray(row_counts, dtype=float).sum())
    return float(n / (n + kappa)) if n >= 0 else 0.0


def drift_tilt(forecast_dict: dict, composite_daily_now: float, horizon: int,
               k: float = 0.5, max_pts: float = 12.0, confidence: float = 1.0) -> float:
    """Bounded, confidence-weighted tilt = clip(k*(E[score@h]-now)) * confidence."""
    h = next((x for x in forecast_dict.get("horizons", []) if x["n"] == horizon), None)
    if h is None:
        return 0.0
    drift = h["e_score"] - float(composite_daily_now)
    tilt = float(np.clip(k * drift, -max_pts, max_pts))
    return tilt * float(confidence)
```

**Step 4: Run → PASS.** **Step 5: Commit** `feat(markov): confidence-weighted drift_tilt`.

> **Phase 1 gate:** `cd trade-analyzer && python -m pytest tests/analysis/test_markov.py -v`
> all green. The pure core is complete and independently shippable.

---

## Phase 2 — Daily score reconstruction (`trade_svc/compute.py`)

Build the per-symbol `composite_daily` time series the chain learns from. This is
the data-dependent half; it reuses the existing scoring primitives + daily
indicators. Keep the function **pure-ish** (DataFrame in, Series out) so it's
testable with synthetic frames.

### Task 2.1: `reconstruct_daily_composite` — failing test first

**Files:**
- Modify: `services/trade_svc/compute.py`
- Test: `services/trade_svc/tests/test_markov_reconstruct.py` (create)

**Daily composite spec (the factor subset that is reconstructable per bar).** Use
8–9 of the 11 PositionVerdict factors — the ones derivable from *daily* OHLCV —
and renormalize their weights to sum to 100. **Drop** the intraday-only factors
(`vwap`, `volume_profile`); compute the rest as full pandas Series so the whole
history is built in one vectorized pass (no O(N^2) re-slicing):

| Factor | Weight | Daily series source |
|--------|-------:|---------------------|
| ema_alignment (daily stack) | 20 | fraction of {price>EMA12,>EMA21,>EMA50,>EMA200} mapped to [-100,100] |
| adx | 10 | rolling `score_adx_directional(adx_t, ema_slope_t)` |
| rsi | 10 | `score_rsi(rsi_series_t)` |
| macd | 10 | `score_macd(hist_t, hist_{t-1})` from `macd_histogram_series` |
| rel_volume | 5 | daily volume / rolling 20-day avg → `score_relative_volume` |
| dist_52wk | 5 | rolling 252 max → `score_distance_from_52wk_high` |
| rs_3m vs SPY | 10 | 63-bar excess return percentile → `score_relative_strength_percentile` |
| rs_6m vs SPY | 10 | 126-bar → same |
| sector | 10 | sector ETF vs SPY 63-bar percentile → `score_relative_strength_percentile` (neutral 0 if no sector history) |

Sum = 90 → divide the weighted sum by 0.90 to renormalize to a [-100,100] scale.

```python
# services/trade_svc/tests/test_markov_reconstruct.py
"""Run with the repo-root venv from the repo root:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_markov_reconstruct.py -v
(never `pytest services` over all services — cross-app module-name collisions.)"""
import numpy as np
import pandas as pd
import pytest

from services.trade_svc import compute


def _synthetic_daily(n=300, start=100.0, drift=0.3, seed=1):
    rng = np.random.default_rng(seed)
    closes = start + np.cumsum(rng.normal(drift, 1.0, n))
    closes = np.maximum(closes, 1.0)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "datetime": idx,
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": rng.integers(1_000_000, 5_000_000, n),
    })


def test_reconstruct_returns_series_in_range():
    daily = _synthetic_daily()
    spy = _synthetic_daily(seed=2, drift=0.1)
    s = compute.reconstruct_daily_composite(daily, spy, sector_hist=None)
    assert isinstance(s, pd.Series)
    valid = s.dropna()
    assert len(valid) > 200
    assert valid.between(-100.0, 100.0).all()


def test_reconstruct_uptrend_skews_positive():
    up = _synthetic_daily(drift=0.6, seed=3)
    flat_spy = _synthetic_daily(drift=0.0, seed=4)
    s = compute.reconstruct_daily_composite(up, flat_spy, sector_hist=None).dropna()
    # a strong outperforming uptrend should average clearly bullish
    assert s.tail(60).mean() > 10.0


def test_reconstruct_handles_short_history():
    short = _synthetic_daily(n=30)
    s = compute.reconstruct_daily_composite(short, None, None)
    # too short -> returns an (all-NaN or empty) Series, never raises
    assert isinstance(s, pd.Series)
    assert s.dropna().empty or len(s) == len(short)
```

**Step 2: Run → FAIL** (`AttributeError: reconstruct_daily_composite`).

**Step 3: Implement** in `services/trade_svc/compute.py` (add near the other
helpers; import the scoring primitives at module top alongside the existing
`src.analysis` imports). Build each factor as a Series, then combine:

```python
# add to the isolated-import block at top of compute.py:
from src.analysis import scoring as _scoring  # noqa: E402

_MK_WEIGHTS = {  # daily-reconstructable subset, renormalized below
    "ema": 20, "adx": 10, "rsi": 10, "macd": 10, "rel_vol": 5,
    "dist52": 5, "rs3m": 10, "rs6m": 10, "sector": 10,
}
_MK_WEIGHT_SUM = sum(_MK_WEIGHTS.values())  # 90


def _ema_series(close, span):
    return close.ewm(span=span, adjust=False).mean()


def _rsi_series(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-4)
    return 100 - (100 / (1 + rs))


def _adx_series(daily, period=14):
    high, low, close = daily["high"], daily["low"], daily["close"]
    plus_dm = high.diff()
    minus_dm = low.diff().abs() * -1
    plus_dm = plus_dm.where((plus_dm > minus_dm.abs()) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.abs().where((minus_dm.abs() > plus_dm) & (low.diff() < 0), 0.0)
    tr = pd.concat([(high - low),
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().replace(0, 1e-4)
    plus_di = 100 * plus_dm.rolling(period).mean() / atr
    minus_di = 100 * minus_dm.rolling(period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-4)
    return dx.rolling(period).mean()


def _rs_percentile_series(sym_close, ref_close, lookback):
    if ref_close is None:
        return pd.Series(0.5, index=sym_close.index)
    ref = ref_close.reindex(sym_close.index).ffill()
    sym_ret = sym_close / sym_close.shift(lookback) - 1
    ref_ret = ref / ref.shift(lookback) - 1
    excess = sym_ret - ref_ret
    return (0.5 + excess / 0.40).clip(0.0, 1.0)


def reconstruct_daily_composite(daily, spy, sector_hist):
    """Per-bar daily-only composite score Series (the Markov base score).

    Returns an all-NaN Series of the same length when history is too short.
    Defensive: any failure returns an empty Series (Markov simply won't run).
    """
    try:
        if daily is None or len(daily) < 60:
            return pd.Series([np.nan] * (0 if daily is None else len(daily)),
                             index=None if daily is None else daily.index)
        close = daily["close"].astype(float).reset_index(drop=True)
        idx = close.index

        # ema_alignment: fraction of EMAs price is above -> [-100,100]
        emas = [_ema_series(close, p) for p in (12, 21, 50, 200)]
        above = sum((close > e).astype(float) for e in emas) / len(emas)
        ema_score = (above * 2 - 1) * 100  # 0..1 -> -100..100
        ema_slope = np.where(ema_score >= 0, 1, -1)

        # adx (directional), vectorized score via thresholds
        adx = _adx_series(daily.reset_index(drop=True))
        adx_score = pd.Series(0.0, index=idx)
        adx_score = adx_score.mask(adx >= 15, 30).mask(adx >= 20, 60).mask(adx >= 25, 100)
        adx_score = adx_score * ema_slope

        # rsi
        rsi = _rsi_series(close)
        rsi_score = rsi.apply(lambda v: _scoring.score_rsi(v) if pd.notna(v) else 0)

        # macd histogram + prev
        ema_fast = _ema_series(close, 12)
        ema_slow = _ema_series(close, 26)
        macd_line = ema_fast - ema_slow
        signal = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal
        macd_score = pd.Series(
            [_scoring.score_macd(float(h), float(p)) if pd.notna(h) and pd.notna(p) else 0
             for h, p in zip(hist, hist.shift())], index=idx)

        # rel_volume (daily vs 20-day avg)
        vol = daily["volume"].astype(float).reset_index(drop=True)
        rel = vol / vol.rolling(20).mean().replace(0, 1e-4)
        rel_score = pd.Series(
            [_scoring.score_relative_volume(float(r), s) if pd.notna(r) else 0
             for r, s in zip(rel, ema_slope)], index=idx)

        # dist from rolling 252 high
        roll_high = close.rolling(252, min_periods=20).max()
        dist = ((roll_high - close) / roll_high.replace(0, np.nan))
        dist_score = dist.apply(
            lambda d: _scoring.score_distance_from_52wk_high(float(d)) if pd.notna(d) else 0)

        # relative strength vs SPY
        spy_close = (spy["close"].astype(float).reset_index(drop=True)
                     if spy is not None and not spy.empty else None)
        rs3 = _rs_percentile_series(close, spy_close, 63).apply(
            lambda p: _scoring.score_relative_strength_percentile(float(p)))
        rs6 = _rs_percentile_series(close, spy_close, 126).apply(
            lambda p: _scoring.score_relative_strength_percentile(float(p)))

        # sector RS (ETF vs SPY); neutral 0 when missing
        if sector_hist is not None and not sector_hist.empty and spy_close is not None:
            sec_close = sector_hist["close"].astype(float).reset_index(drop=True)
            sec_pct = _rs_percentile_series(sec_close, spy_close, 63)
            sec_score = sec_pct.apply(
                lambda p: _scoring.score_relative_strength_percentile(float(p)))
            sec_score = sec_score.reindex(idx).ffill().fillna(0)
        else:
            sec_score = pd.Series(0.0, index=idx)

        w = _MK_WEIGHTS
        weighted = (ema_score * w["ema"] + adx_score * w["adx"]
                    + rsi_score * w["rsi"] + macd_score * w["macd"]
                    + rel_score * w["rel_vol"] + dist_score * w["dist52"]
                    + rs3 * w["rs3m"] + rs6 * w["rs6m"] + sec_score * w["sector"])
        composite = (weighted / _MK_WEIGHT_SUM).clip(-100, 100)
        # null the warmup region where long windows aren't seeded yet
        composite.iloc[:200] = np.nan
        composite.index = daily.index
        return composite
    except Exception:
        return pd.Series([], dtype=float)
```

**Step 4: Run → PASS** (all three tests).

**Step 5: Commit**

```bash
git add services/trade_svc/compute.py services/trade_svc/tests/test_markov_reconstruct.py
git commit -m "feat(trade): reconstruct_daily_composite Markov-base score series"
```

> If `test_reconstruct_uptrend_skews_positive` is flaky, widen `n` or relax the
> threshold to `> 5.0` — the intent is "clearly positive," not an exact value.

---

## Phase 3 — Service wiring (prior cache + `markov` block + contract)

### Task 3.1: Extend the `TradeAnalysis` contract (additive)

**Files:**
- Modify: `shared/contracts/trade.py`
- Test: `shared/contracts/tests/test_trade.py` (create if absent; otherwise append)

**Step 1: Failing test**

```python
# shared/contracts/tests/test_trade.py
from shared.contracts.trade import TradeAnalysis


def test_accepts_payload_without_markov():
    t = TradeAnalysis(symbol="AAPL")
    assert t.markov is None


def test_accepts_payload_with_markov():
    t = TradeAnalysis(symbol="AAPL", markov={
        "current_band": 3, "band_labels": ["a"], "transition_row": [0.2] * 5,
        "horizons": [{"n": 10, "dist": [0.2] * 5, "p_buy": 0.3, "p_sell": 0.1,
                      "e_score": 12.0}],
        "drift": 8.0, "tilt": 6.0, "markov_adjusted_score": 46.0,
        "confidence": 0.7, "prior_version": "2026-06-21",
    })
    assert t.markov["current_band"] == 3
```

**Step 2: Run → FAIL** (`markov` not a field).

Run: `.venv\Scripts\python -m pytest shared\contracts\tests\test_trade.py -v`

**Step 3: Implement** — add one field to `shared/contracts/trade.py`:

```python
    markov: dict | None = None
```

(Place it after `errors: list = []`. Loose `dict | None` matches the existing
sparse-sub-object convention; the GUI builders tolerate missing keys.)

**Step 4: Run → PASS.**

**Step 5: Commit** `feat(contracts): optional markov block on TradeAnalysis`.

---

### Task 3.2: Pooled-prior cache helper (`compute_markov_prior` + lazy read)

**Files:**
- Modify: `services/trade_svc/compute.py`
- Test: `services/trade_svc/tests/test_markov_prior.py` (create)

**Approach.** Build a universe-wide pooled count matrix by reconstructing
`composite_daily` for each watchlist/index symbol and summing `count_matrix`. Cache
the normalized prior + a date stamp at `cache:trade:markov_prior`. `get_prior()`
reads the cache; recomputes if missing or older than one calendar day; on total
failure returns a uniform prior so `analyze()` never blocks. Universe symbols come
from a small built-in list (the `_SYMBOL_SECTOR` keys + `SPY`/`QQQ`) to avoid a
watchlist-file dependency in the service.

**Step 1: Failing test** (mock the per-symbol fetch + the bus so it's hermetic)

```python
# services/trade_svc/tests/test_markov_prior.py
import numpy as np
import pandas as pd
from services.trade_svc import compute


def test_universe_prior_from_series(monkeypatch):
    # two fake symbols' band series -> pooled prior is a valid stochastic matrix
    series = {
        "AAA": pd.Series([0, 0, 2, 3, 4, 4, 3] * 40, dtype=float),
        "BBB": pd.Series([2, 2, 1, 0, 1, 2] * 40, dtype=float),
    }
    monkeypatch.setattr(compute, "_symbol_band_series", lambda sym: series.get(sym))
    prior, n = compute.build_pooled_prior(["AAA", "BBB", "MISSING"])
    assert prior.shape == (5, 5)
    np.testing.assert_allclose(prior.sum(axis=1), 1.0)
    assert n >= 1  # at least one symbol contributed


def test_get_prior_uses_cache(monkeypatch):
    calls = {"n": 0}

    def fake_build(_universe):
        calls["n"] += 1
        return np.full((5, 5), 0.2), 2

    monkeypatch.setattr(compute, "build_pooled_prior", fake_build)
    fresh = {"matrix": [[0.2] * 5] * 5, "date": compute._today_ct_str(),
             "n_symbols": 2}
    monkeypatch.setattr(compute, "_read_prior_cache", lambda: fresh)
    P, ver = compute.get_prior()
    assert calls["n"] == 0  # fresh cache -> no rebuild
    np.testing.assert_allclose(np.array(P).sum(axis=1), 1.0)
```

**Step 2: Run → FAIL.**

**Step 3: Implement** in `compute.py` (uses `shared.bus.Bus`, `markov`,
`parallel_map`). Sketch — fill to satisfy the tests:

```python
from datetime import date
from src.analysis import markov as _markov  # add to isolated imports
from shared.bus import Bus

_PRIOR_KEY = "cache:trade:markov_prior"
_MK_UNIVERSE = sorted(set(_SYMBOL_SECTOR) | {"SPY", "QQQ"})


def _today_ct_str():
    return date.today().isoformat()


def _symbol_band_series(sym):
    """Daily composite -> band series for one universe symbol (or None)."""
    daily = _price_history(sym, "year", 2, "daily", 1)
    if daily is None or len(daily) < 220:
        return None
    spy = _price_history("SPY", "year", 2, "daily", 1) if sym != "SPY" else daily
    sect = resolve_sector(sym)
    sector_hist = _price_history(sect["etf"], "year", 2, "daily", 1) if sect["etf"] else None
    comp = reconstruct_daily_composite(daily, spy, sector_hist).dropna()
    if comp.empty:
        return None
    return comp.apply(_markov.classify_band)


def build_pooled_prior(universe):
    """Sum transition counts across the universe -> (prior matrix, n_symbols)."""
    C = np.zeros((5, 5))
    n = 0
    results = parallel_map(lambda s: (s, _symbol_band_series(s)), list(universe))
    for _sym, bands in results:
        if bands is None or bands.empty:
            continue
        C += _markov.count_matrix(list(bands))
        n += 1
    return _markov.pooled_prior(C), n


def _read_prior_cache():
    try:
        return Bus().cache_get(_PRIOR_KEY)
    except Exception:
        return None


def _write_prior_cache(matrix, n):
    try:
        Bus().cache_set(_PRIOR_KEY, {"matrix": [list(map(float, r)) for r in matrix],
                                     "date": _today_ct_str(), "n_symbols": int(n)})
    except Exception:
        pass


def get_prior():
    """(prior matrix, version-string). Lazy: rebuild if cache missing/stale."""
    cached = _read_prior_cache()
    if cached and cached.get("date") == _today_ct_str() and cached.get("matrix"):
        return np.array(cached["matrix"], dtype=float), cached["date"]
    try:
        matrix, n = build_pooled_prior(_MK_UNIVERSE)
        _write_prior_cache(matrix, n)
        return matrix, _today_ct_str()
    except Exception:
        return np.full((5, 5), 0.2), "uniform"
```

> Verify `Bus().cache_get`/`cache_set` signatures against `shared/bus/` before
> finalizing (the tests stub them, so unit tests pass regardless; the live path
> needs the real names). Adjust `cache_set` kwargs if the API differs.

**Step 4: Run → PASS.**

**Step 5: Commit** `feat(trade): pooled Markov prior with lazy daily cache`.

---

### Task 3.3: Assemble the `markov` block inside `analyze()`

**Files:**
- Modify: `services/trade_svc/compute.py` (inside `analyze`, after the verdicts)
- Test: `services/trade_svc/tests/test_markov_analyze.py` (create)

**Step 1: Failing test**

```python
# services/trade_svc/tests/test_markov_analyze.py
import numpy as np
import pandas as pd
from services.trade_svc import compute


def test_build_markov_block_happy(monkeypatch):
    # deterministic prior + a band series ending in Weak-Bull
    monkeypatch.setattr(compute, "get_prior",
                        lambda: (np.full((5, 5), 0.2), "test"))
    bands = pd.Series([2, 2, 3, 3, 4, 3, 3, 4] * 30, dtype=float)
    comp_now = 25.0  # Weak-Bull
    block = compute.build_markov_block(bands, comp_now, composite_full=38.0)
    assert block["current_band"] == 3
    assert len(block["transition_row"]) == 5
    assert {"n", "dist", "p_buy", "p_sell", "e_score"} <= set(block["horizons"][0])
    assert -100 <= block["markov_adjusted_score"] <= 100
    assert abs(block["tilt"]) <= 12.0 + 1e-9
    assert block["prior_version"] == "test"


def test_build_markov_block_degrades(monkeypatch):
    monkeypatch.setattr(compute, "get_prior",
                        lambda: (np.full((5, 5), 0.2), "test"))
    # empty band series -> None (caller sets markov: None), never raises
    assert compute.build_markov_block(pd.Series([], dtype=float), 0.0, 0.0) is None
```

**Step 2: Run → FAIL.**

**Step 3: Implement** `build_markov_block` and call it from `analyze`:

```python
_MK_HORIZONS = [5, 10, 20]
_MK_DRIFT_HORIZON = 10
_MK_ALPHA = 30.0
_MK_K = 0.5
_MK_MAX_PTS = 12.0


def build_markov_block(band_series, composite_daily_now, composite_full):
    """Markov forecast + tilt for one symbol, or None if it can't be built."""
    try:
        bands = band_series.dropna()
        if bands.empty or len(bands) < 30:
            return None
        prior, version = get_prior()
        C_sym = _markov.count_matrix(list(bands))
        P = _markov.shrink(C_sym, prior, alpha=_MK_ALPHA)
        current = _markov.classify_band(composite_daily_now)
        fc = _markov.forecast(P, current, _MK_HORIZONS)
        conf = _markov.row_confidence(C_sym[current])
        tilt = _markov.drift_tilt(fc, composite_daily_now, _MK_DRIFT_HORIZON,
                                  k=_MK_K, max_pts=_MK_MAX_PTS, confidence=conf)
        h = next(x for x in fc["horizons"] if x["n"] == _MK_DRIFT_HORIZON)
        return {
            "current_band": current,
            "band_labels": _markov.BAND_LABELS,
            "transition_row": fc["transition_row"],
            "persistence": fc["persistence"],
            "stationary": fc["stationary"],
            "horizons": fc["horizons"],
            "drift": float(h["e_score"] - composite_daily_now),
            "tilt": float(tilt),
            "confidence": float(conf),
            "composite_daily": float(composite_daily_now),
            "markov_adjusted_score": float(np.clip(composite_full + tilt, -100, 100)),
            "prior_version": version,
        }
    except Exception:
        return None
```

In `analyze()`, after `position_verdict` is computed and before the return dict,
add (defensively):

```python
    markov_block = None
    try:
        comp_series = reconstruct_daily_composite(daily, spy, sector_hist)
        valid = comp_series.dropna()
        if not valid.empty:
            bands = valid.apply(__import__("src.analysis.markov",
                                           fromlist=["classify_band"]).classify_band)
            markov_block = build_markov_block(
                bands, float(valid.iloc[-1]),
                composite_full=float(position_verdict.get("score", 0)))
    except Exception:
        markov_block = None
```

> Prefer a clean top-level `from src.analysis import markov as _markov` (already
> added in 3.2) over the inline `__import__`; use `_markov.classify_band`.

Then add `"markov": markov_block,` to the returned dict.

**Step 4: Run → PASS** (`.venv\Scripts\python -m pytest services\trade_svc -v`).

**Step 5: Commit** `feat(trade): attach markov forecast block to analyze()`.

---

### Task 3.4: Head-less live verification (no browser)

**Files:** none (verification only).

**Step 1:** Ensure Memurai + proxy + `trade_svc` are running (see root `CLAUDE.md`
"Running"). Then from the repo root:

```bash
.venv\Scripts\python -c "import time; from shared.bus import Bus; b=Bus(); \
b.enqueue_command('cmd:trade', {'command':'analyze','symbol':'AAPL'}); \
time.sleep(8); print(b.cache_get('cache:trade:analysis').get('markov'))"
```

Expected: a dict with `current_band`, `horizons` (3 entries), `tilt`,
`markov_adjusted_score`, `prior_version` — not `None`. (Adjust the enqueue/cache
call to match `shared/bus` real signatures.)

**Step 2:** Confirm `cache:trade:markov_prior` exists and `n_symbols` > 0:

```bash
.venv\Scripts\python -c "from shared.bus import Bus; print(Bus().cache_get('cache:trade:markov_prior').get('n_symbols'))"
```

**Step 3: Commit** (if any tuning of constants resulted) — otherwise skip.

> If `markov` is `None` live but unit tests pass, it's almost always a real-data
> shape issue (thin history, missing SPY). Per the "defensive guards mask bugs"
> memory, temporarily log the caught exception in `build_markov_block`/`analyze`
> to find the cause, then revert the log.

---

## Phase 4 — Page: Markov Forecast card (`webgui/pages/trade.py`)

Webgui imports only `nicegui` + `shared.*`. Pure builders are unit-tested; the
chart follows the Highcharts gotchas (build once at render, update in place).

### Task 4.1: Pure builders — failing tests

**Files:**
- Modify: `webgui/pages/trade.py`
- Test: `webgui/tests/test_trade.py` (append)

**Step 1: Failing test**

```python
# append to webgui/tests/test_trade.py
from pages import trade as trade_page

_MK = {
    "current_band": 3, "band_labels": ["Strong-Bear", "Weak-Bear", "Neutral",
                                       "Weak-Bull", "Strong-Bull"],
    "transition_row": [0.05, 0.1, 0.2, 0.45, 0.2],
    "persistence": 0.45,
    "horizons": [
        {"n": 5, "dist": [0.05, 0.1, 0.2, 0.4, 0.25], "p_buy": 0.25, "p_sell": 0.05, "e_score": 22.0},
        {"n": 10, "dist": [0.05, 0.1, 0.15, 0.4, 0.3], "p_buy": 0.30, "p_sell": 0.05, "e_score": 28.0},
        {"n": 20, "dist": [0.05, 0.1, 0.15, 0.35, 0.35], "p_buy": 0.35, "p_sell": 0.05, "e_score": 32.0},
    ],
    "drift": 8.0, "tilt": 6.0, "confidence": 0.7,
    "markov_adjusted_score": 44.0, "composite_daily": 24.0, "prior_version": "2026-06-21",
}


def test_markov_band_chip():
    assert trade_page.markov_band_chip(_MK)["label"] == "Weak-Bull"


def test_markov_metric_rows():
    rows = trade_page.markov_metric_rows(_MK)
    assert any(r["horizon"] == "10d" for r in rows)
    r10 = next(r for r in rows if r["horizon"] == "10d")
    assert r10["p_buy"] == "30%"


def test_markov_drift_row():
    r = trade_page.markov_drift_row(_MK)
    assert "+6" in r["tilt"] and "44" in r["adjusted"]


def test_markov_forecast_figure_shape():
    fig = trade_page.markov_forecast_figure(_MK)
    assert fig["chart"]["type"] == "area"
    assert len(fig["series"]) == 5  # one stacked band per series


def test_markov_builders_tolerate_none():
    assert trade_page.markov_band_chip(None) is None
    assert trade_page.markov_metric_rows(None) == []
    assert trade_page.markov_forecast_figure(None)["series"] == []
```

**Step 2: Run → FAIL** — `cd webgui && python -m pytest tests/test_trade.py -k markov -v`.

**Step 3: Implement** the pure builders in `webgui/pages/trade.py`:

```python
def markov_band_chip(mk):
    if not mk:
        return None
    i = mk.get("current_band", 2)
    labels = mk.get("band_labels") or ["?"] * 5
    colors = ["#c0392b", "#e67e22", "#7f8c8d", "#27ae60", "#1e8449"]
    return {"label": labels[i], "color": colors[i] if 0 <= i < len(colors) else "#7f8c8d"}


def markov_metric_rows(mk):
    if not mk:
        return []
    rows = []
    for h in mk.get("horizons", []):
        rows.append({
            "horizon": f"{h['n']}d",
            "p_buy": f"{round(h['p_buy'] * 100)}%",
            "p_sell": f"{round(h['p_sell'] * 100)}%",
            "e_score": f"{h['e_score']:+.0f}",
        })
    return rows


def markov_drift_row(mk):
    if not mk:
        return None
    return {
        "drift": f"{mk.get('drift', 0):+.0f}",
        "tilt": f"{mk.get('tilt', 0):+.0f}",
        "confidence": f"{round(mk.get('confidence', 0) * 100)}%",
        "adjusted": f"{mk.get('markov_adjusted_score', 0):.0f}",
    }


def markov_forecast_figure(mk):
    if not mk:
        return {"chart": {"type": "area"}, "series": []}
    labels = mk.get("band_labels") or ["?"] * 5
    colors = ["#c0392b", "#e67e22", "#bdc3c7", "#27ae60", "#1e8449"]
    cats = ["now"] + [f"{h['n']}d" for h in mk["horizons"]]
    now = [0.0] * 5
    now[mk.get("current_band", 2)] = 1.0
    dists = [now] + [h["dist"] for h in mk["horizons"]]
    series = [{
        "name": labels[b], "color": colors[b],
        "data": [round(d[b], 4) for d in dists],
    } for b in range(5)]
    return {
        "accessibility": {"enabled": False},
        "chart": {"type": "area", "height": 260},
        "title": {"text": None},
        "xAxis": {"categories": cats},
        "yAxis": {"min": 0, "max": 1, "title": {"text": "P(band)"},
                  "labels": {"format": "{value:.0%}"}},
        "plotOptions": {"area": {"stacking": "percent", "marker": {"enabled": False}}},
        "series": series,
    }
```

**Step 4: Run → PASS.**

**Step 5: Commit** `feat(webgui): pure builders for Markov forecast card`.

---

### Task 4.2: Wire the card into `render()`

**Files:** Modify `webgui/pages/trade.py` (the render + repaint path).

**Step 1:** Read the existing `render()` to find where `position_verdict` is drawn
and how the page repaints on a new cache version (it version-polls
`cache:trade:analysis`). Add a **persistent** `ui.highchart(markov_forecast_figure(None))`
at build time (so the ESM import map is present), and a container for the chip +
metric rows + drift row.

**Step 2:** In the repaint function, after rendering the verdicts, pull
`data.get("markov")` and:
- update the chip/metric/drift widgets (use the pure builders);
- update the chart in place: `el.options = markov_forecast_figure(mk); el.update()`;
- if `mk` is `None`, hide the card section (`set_visibility(False)`), per the
  "degrades cleanly" rule.

Wrap any `.on(...)`/timer callbacks in `@guard` (see `pages/ui_guard.py`).

**Step 3: Manual/preview verification** (per root `CLAUDE.md` "Verify in the
browser"):
- `preview_start` the `webgui` dev server (:8500); navigate to `/trade`.
- Analyze a liquid symbol with long history (e.g. `AAPL`).
- `preview_snapshot` / `preview_screenshot`: confirm the Markov Forecast card
  renders the stacked-area chart, the band chip, the 5/10/20d metric rows, and the
  drift/adjusted-score line. Check `preview_console_logs` for errors.

**Step 4:** Run the webgui suite — `cd webgui && python -m pytest -q` — all green.

**Step 5: Commit**

```bash
git add webgui/pages/trade.py webgui/tests/test_trade.py
git commit -m "feat(webgui): Markov Forecast card on the Trade page"
```

---

## Phase 5 — Tilt activation (headline adjusted score)

Make the verdict reflect the tilt as the headline, keeping the base score visible.
Isolated last step so the verdict change is deliberate.

### Task 5.1: Show `markov_adjusted_score` as the headline Position score

**Files:** Modify `webgui/pages/trade.py` (Position verdict card render).

**Step 1: Failing test** (a pure helper that picks the headline + subtitle)

```python
def test_position_headline_prefers_markov():
    pv = {"verdict": "HOLD", "score": 38}
    mk = {"markov_adjusted_score": 44.0, "tilt": 6.0}
    head = trade_page.position_headline(pv, mk)
    assert head["score"] == 44 and head["base"] == 38 and "+6" in head["tilt"]


def test_position_headline_no_markov():
    head = trade_page.position_headline({"verdict": "BUY", "score": 41}, None)
    assert head["score"] == 41 and head["base"] == 41 and head["tilt"] == ""
```

**Step 2: Run → FAIL.**

**Step 3: Implement**

```python
def position_headline(pv, mk):
    base = int(round(pv.get("score", 0)))
    if not mk:
        return {"score": base, "base": base, "tilt": ""}
    adj = int(round(mk.get("markov_adjusted_score", base)))
    return {"score": adj, "base": base, "tilt": f"{mk.get('tilt', 0):+.0f}"}
```

Wire it into the Position card: show `head["score"]` as the big number, with a
small "base {base} · Markov {tilt}" subtitle when a tilt is present. **Do not**
change the BUY/HOLD/SELL verdict label itself (the tilt is advisory on the score;
re-deriving the verdict from the adjusted score is an explicit later decision, out
of scope here).

**Step 4: Run → PASS** (`cd webgui && python -m pytest -q`).

**Step 5: Commit** `feat(webgui): Markov-adjusted score as Position headline`.

---

### Task 5.2: Final full-suite verification + docs

**Step 1:** Run every affected suite, confirm green:

```bash
cd trade-analyzer && python -m pytest tests -q
cd ..
.venv\Scripts\python -m pytest services\trade_svc -q
.venv\Scripts\python -m pytest shared\contracts -q
cd webgui && python -m pytest -q
```

Expected: trade-analyzer baseline + new markov tests; trade_svc all green; contracts
green; webgui green. (trade-analyzer has a known ~date-relative carryover or two —
do not "fix" unrelated failures.)

**Step 2:** Update the root `CLAUDE.md`: add a "Markov 2.0 (Trade page)" subsection
under the Trade page notes (states/bands, `composite_daily`, hybrid prior, tilt),
bump "Last updated", and reference this plan + the design doc. Use the
claude-md-management:revise-claude-md skill if available.

**Step 3:** Update the `/trade` row in the Routes table + the Trade page paragraph
to mention the Markov Forecast card.

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document Markov 2.0 Trade Analyzer feature"
```

---

## Done criteria

- `trade-analyzer/src/analysis/markov.py` pure core, fully unit-tested.
- `reconstruct_daily_composite` + pooled prior + `markov` block wired into
  `trade_svc.analyze()`, defensively (failure → `markov: None`, verdict unchanged).
- `TradeAnalysis` carries the optional `markov` block; existing payloads still valid.
- `/trade` shows a Markov Forecast card (stacked-area band probabilities, band chip,
  5/10/20d P(BUY)/P(SELL)/E[score], drift/tilt) + a Markov-adjusted headline score.
- All affected test suites green; feature verified head-less (Redis) and in the
  browser preview.
- Conflict resolved by construction: the chain is built from `composite_daily`
  (never tilted), so the tilt added to `composite_full` cannot feed back.
```
