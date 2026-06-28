# Validated Swing (1–8 wk) Evaluation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the Trade Analyzer's hand-weighted swing (1–8 wk) verdict with an
**IC-weighted cross-sectional factor model** whose weights are fit offline against
forward returns (backtested, walk-forward validated) and applied cheaply at analyze time.

**Architecture:** Offline fit → versioned JSON artifact → online score. A **pure factor
library** (`factors.py`) feeds both a **pure backtest/IC engine** (`backtest.py`) and an
**offline orchestrator** (`fit_swing_model.py`, pulls data via the proxy) that writes
`trade-analyzer/data/swing_model.json` + a research report. A **lazy live scorer**
(`services/trade_svc/swing_model.py`) loads the artifact, z-scores today's factors
cross-sectionally, applies the weights, and maps to a calibrated outcome band. The verdict
rides into the existing `cache:trade:analysis` → the `/trade` page. The Markov layer runs
on the new validated composite.

**Tech Stack:** Python 3.11, numpy, pandas, scipy (Spearman), pytest. Shared
`analysis_lib`, the copied `trade-analyzer/src/analysis` engines, the Redis `shared.bus`,
`shared.contracts`, NiceGUI + Highcharts.

**Design doc:** `docs/plans/2026-06-22-swing-validated-evaluation-design.md` (read it).

**Critical conventions (read before starting):**
- Run each app's tests **from inside its folder**: `cd trade-analyzer && ..\.venv\Scripts\python -m pytest tests`. Run a service's tests with the repo-root venv **one service at a time**: `.venv\Scripts\python -m pytest services\trade_svc`. NEVER `pytest services` over all of them (cross-app module-name collisions).
- `trade-analyzer/src/analysis/*` is **pure** (no proxy, no I/O) and imported as `from src.analysis...` (a `conftest.py` puts the app dir on `sys.path`).
- `services/trade_svc/compute.py` imports the trade-analyzer engines standalone (its dir on `sys.path`) and is **defensive** — every engine call catches and degrades; the swing model must **never** crash `analyze()` (failure → fall back to the legacy verdict).
- The webgui imports only `nicegui` + `shared.*` — no engine imports. Highcharts gotchas apply (persistent element, explicit height, reflow) — but this feature adds NO new chart (it reuses text + an expander), so those don't bite here.
- Windows: `..\.venv\Scripts\python` from app folders; `.venv\Scripts\python` from repo root.

---

## Phase 0 — Paths & scaffolding

### Task 0.1: Add the artifact path + gitignore the data dir

**Files:**
- Modify: `repo_paths.py`
- Modify: `.gitignore`

**Step 1:** In `repo_paths.py`, after the `TRADE_ANALYZER = REPO_ROOT / "trade-analyzer"`
line, add:
```python
SWING_MODEL = TRADE_ANALYZER / "data" / "swing_model.json"
SWING_MODEL_REPORT = TRADE_ANALYZER / "data" / "swing_model_report.md"
```

**Step 2:** In `.gitignore`, near the other `*/data/` entries, add:
```
trade-analyzer/data/
```

**Step 3: Commit**
```bash
git add repo_paths.py .gitignore
git commit -m "chore(trade): swing-model artifact path + gitignore data dir"
```

---

## Phase 1 — Factor library (pure)

`trade-analyzer/src/analysis/factors.py`. Each factor: `(daily_df) → pd.Series`, daily
OHLCV only, **sign-corrected so higher = more bullish**, winsorized. The live value is the
Series' last element (so backtest and live share one code path). Tests:
`trade-analyzer/tests/analysis/test_factors.py`. Run:
`cd trade-analyzer && ..\.venv\Scripts\python -m pytest tests/analysis/test_factors.py -v`

### Task 1.1: Helpers + the FACTORS registry skeleton

**Step 1: Failing test**
```python
# trade-analyzer/tests/analysis/test_factors.py
import numpy as np
import pandas as pd
from src.analysis import factors


def _df(closes, highs=None, lows=None, vols=None):
    n = len(closes)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "datetime": idx,
        "open": closes,
        "high": highs if highs is not None else [c * 1.01 for c in closes],
        "low": lows if lows is not None else [c * 0.99 for c in closes],
        "close": closes,
        "volume": vols if vols is not None else [1_000_000] * n,
    })


def test_winsorize_clips_tails():
    s = pd.Series([-100, 0, 0, 0, 100])
    w = factors.winsorize(s, lower=0.2, upper=0.8)
    assert w.max() < 100 and w.min() > -100


def test_registry_lists_core_factors():
    names = set(factors.FACTORS)
    for f in ("mom_12_1", "mom_6_1", "pth", "str_5d", "vol_adj_mom",
              "trend_quality", "low_vol", "rs_spy", "rs_sector", "turnover"):
        assert f in names
    # every registry entry has a callable + a direction (+1 already sign-corrected)
    for name, spec in factors.FACTORS.items():
        assert callable(spec["fn"])
        assert spec["direction"] in (1, -1)
```

**Step 2: Run → FAIL** (`ModuleNotFoundError`).

**Step 3: Implement** (start of `factors.py`):
```python
"""Pure swing (1-8 wk) factor library.

Each factor is `(daily_df) -> pd.Series` over a daily OHLCV frame (columns:
datetime, open, high, low, close, volume), sign-corrected so HIGHER = more
bullish, and winsorized. The live value is the Series' last element, so the same
code feeds the offline backtest and the online scorer (no drift).

Reference-relative factors (RS) take an extra reference close Series aligned by
date. The FACTORS registry is the single source of truth for what exists; the
backtest decides which actually carry predictive IC.
"""
from typing import Callable, Optional

import numpy as np
import pandas as pd


def winsorize(s: pd.Series, lower: float = 0.02, upper: float = 0.98) -> pd.Series:
    """Clip a series to its [lower, upper] quantiles (robustness to outliers)."""
    s = pd.Series(s, dtype="float64")
    if s.dropna().empty:
        return s
    lo, hi = s.quantile(lower), s.quantile(upper)
    return s.clip(lower=lo, upper=hi)


def _close(df: pd.DataFrame) -> pd.Series:
    return pd.Series(df["close"].to_numpy(dtype="float64"),
                     index=pd.to_datetime(df["datetime"]))


def _ret(close: pd.Series, lookback: int, skip: int = 0) -> pd.Series:
    """Total return over `lookback` bars ending `skip` bars ago."""
    end = close.shift(skip)
    start = close.shift(skip + lookback)
    return end / start - 1.0
```

Then the registry (populated as factors are added in 1.2–1.4; start it now):
```python
FACTORS: dict = {}  # name -> {"fn": callable, "direction": +1/-1, "needs_ref": bool, "desc": str}


def _register(name, fn, direction=1, needs_ref=False, desc=""):
    FACTORS[name] = {"fn": fn, "direction": direction, "needs_ref": needs_ref, "desc": desc}
```

**Step 4: Run → the registry test FAILS** (factors not registered yet — that's expected;
it passes after 1.2–1.4). Make `test_winsorize_clips_tails` pass now.

**Step 5: Commit** `feat(factors): winsorize + registry scaffold`.

### Task 1.2: Momentum/PTH/STR factors

**Step 1: Failing tests** (append):
```python
def test_mom_12_1_positive_for_uptrend():
    closes = list(np.linspace(100, 200, 300))  # steady uptrend
    s = factors.mom_12_1(_df(closes))
    assert s.iloc[-1] > 0


def test_pth_near_high_is_high():
    closes = list(np.linspace(100, 200, 300))   # ends at the high
    assert factors.pth(_df(closes)).iloc[-1] > 0.95
    closes2 = list(np.linspace(200, 120, 300))  # well off the high
    assert factors.pth(_df(closes2)).iloc[-1] < 0.8


def test_str_5d_is_negative_recent_return():
    closes = [100] * 300
    closes[-1] = 110   # +10% last bar -> STR should be negative (sign-corrected)
    s = factors.str_5d(_df(closes))
    assert s.iloc[-1] < 0
```

**Step 3: Implement** (add to `factors.py`; register each):
```python
def mom_12_1(df: pd.DataFrame) -> pd.Series:
    # 12-month (252 bar) return skipping the last month (21 bars)
    return winsorize(_ret(_close(df), lookback=252, skip=21))


def mom_6_1(df: pd.DataFrame) -> pd.Series:
    return winsorize(_ret(_close(df), lookback=126, skip=21))


def pth(df: pd.DataFrame) -> pd.Series:
    close = _close(df)
    high_252 = close.rolling(252, min_periods=60).max()
    return (close / high_252).clip(0, 1.5)


def str_5d(df: pd.DataFrame) -> pd.Series:
    # short-term reversal: NEGATIVE of the 5-day return (sign-corrected -> higher=bullish)
    close = _close(df)
    return winsorize(-(close / close.shift(5) - 1.0))


_register("mom_12_1", mom_12_1, desc="12-1 intermediate momentum")
_register("mom_6_1", mom_6_1, desc="6-1 momentum")
_register("pth", pth, desc="price / 252d high (anchoring)")
_register("str_5d", str_5d, desc="short-term (5d) reversal, sign-corrected")
```

**Step 4: Run → PASS.** **Step 5: Commit** `feat(factors): momentum, PTH, short-term reversal`.

### Task 1.3: Volatility/trend factors

**Step 1: Failing tests** (append):
```python
def test_low_vol_higher_for_calm_series():
    calm = list(100 + np.sin(np.arange(300)) * 0.5)
    wild = list(100 + np.sin(np.arange(300)) * 10)
    assert factors.low_vol(_df(calm)).iloc[-1] > factors.low_vol(_df(wild)).iloc[-1]


def test_trend_quality_positive_above_emas():
    closes = list(np.linspace(100, 200, 300))
    assert factors.trend_quality(_df(closes)).iloc[-1] > 0


def test_vol_adj_mom_defined():
    closes = list(np.linspace(100, 160, 300))
    assert np.isfinite(factors.vol_adj_mom(_df(closes)).iloc[-1])
```

**Step 3: Implement**:
```python
def _realized_vol(close: pd.Series, window: int = 60) -> pd.Series:
    return close.pct_change().rolling(window, min_periods=20).std()


def low_vol(df: pd.DataFrame) -> pd.Series:
    # negative realized vol -> higher = lower vol = more bullish (low-vol anomaly)
    return winsorize(-_realized_vol(_close(df)))


def vol_adj_mom(df: pd.DataFrame) -> pd.Series:
    close = _close(df)
    r3 = close / close.shift(63) - 1.0
    vol = _realized_vol(close).replace(0, np.nan)
    return winsorize(r3 / vol)


def trend_quality(df: pd.DataFrame) -> pd.Series:
    close = _close(df)
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    # distance above the slower EMA + a positive-stack bonus, both normalized
    dist = (close - ema200) / ema200
    stack = ((close > ema50).astype(float) + (ema50 > ema200).astype(float) - 1.0)
    return winsorize(dist + 0.02 * stack)


_register("low_vol", low_vol, desc="-(60d realized vol)")
_register("vol_adj_mom", vol_adj_mom, desc="3m return / realized vol")
_register("trend_quality", trend_quality, desc="distance above 50/200 EMA stack")
```

**Step 4: Run → PASS.** **Step 5: Commit** `feat(factors): low-vol, vol-adj momentum, trend quality`.

### Task 1.4: Reference-relative factors (RS) + turnover + the frame builder

RS factors need a **reference close** (SPY / sector ETF) aligned by date.

**Step 1: Failing tests** (append):
```python
def test_rs_spy_positive_when_outperforming():
    up = list(np.linspace(100, 200, 300))     # symbol +100%
    flat = list(np.linspace(100, 105, 300))   # spy ~flat
    sym, ref = _df(up), _df(flat)
    s = factors.rs_spy(sym, ref_close=factors._close(ref))
    assert s.iloc[-1] > 0


def test_turnover_defined():
    closes = [100] * 300
    vols = list(np.linspace(1e6, 5e6, 300))
    assert np.isfinite(factors.turnover(_df(closes, vols=vols)).iloc[-1])


def test_compute_factor_frame_columns():
    closes = list(np.linspace(100, 180, 300))
    ref = factors._close(_df(list(np.linspace(100, 110, 300))))
    frame = factors.compute_factor_frame(_df(closes), spy_close=ref, sector_close=ref)
    # every registered factor is a column; index is the symbol's dates
    assert set(factors.FACTORS).issubset(set(frame.columns))
    assert len(frame) == 300
```

**Step 3: Implement**:
```python
def _excess_return(sym_close, ref_close, lookback):
    if ref_close is None:
        return pd.Series(np.nan, index=sym_close.index)
    ref = ref_close.reindex(sym_close.index).ffill()
    return (sym_close / sym_close.shift(lookback)) - (ref / ref.shift(lookback))


def rs_spy(df: pd.DataFrame, ref_close: Optional[pd.Series] = None) -> pd.Series:
    return winsorize(_excess_return(_close(df), ref_close, 63))


def rs_sector(df: pd.DataFrame, ref_close: Optional[pd.Series] = None) -> pd.Series:
    return winsorize(_excess_return(_close(df), ref_close, 63))


def turnover(df: pd.DataFrame) -> pd.Series:
    vol = pd.Series(df["volume"].to_numpy(dtype="float64"),
                    index=pd.to_datetime(df["datetime"]))
    return winsorize(vol / vol.rolling(63, min_periods=20).mean())


_register("rs_spy", rs_spy, needs_ref=True, desc="63d excess return vs SPY")
_register("rs_sector", rs_sector, needs_ref=True, desc="63d excess return vs sector ETF")
_register("turnover", turnover, desc="volume / 63d avg (conditioning var)")


def compute_factor_frame(df, spy_close=None, sector_close=None) -> pd.DataFrame:
    """Build a DataFrame (index = symbol dates, columns = every registered factor),
    each column sign-corrected (× direction). Reference-relative factors get SPY /
    sector closes; missing refs -> NaN column (the backtest/scorer tolerate NaN)."""
    out = {}
    for name, spec in FACTORS.items():
        try:
            if spec["needs_ref"]:
                ref = sector_close if name == "rs_sector" else spy_close
                s = spec["fn"](df, ref_close=ref)
            else:
                s = spec["fn"](df)
            out[name] = s * spec["direction"]
        except Exception:
            out[name] = pd.Series(np.nan, index=pd.to_datetime(df["datetime"]))
    return pd.DataFrame(out)
```

**Step 4: Run → the Task 1.1 registry test + these now PASS.** Run the whole file.

**Step 5: Commit** `feat(factors): RS vs SPY/sector, turnover, factor-frame builder`.

> **Phase 1 gate:** `cd trade-analyzer && ..\.venv\Scripts\python -m pytest tests/analysis/test_factors.py -v` all green; `..\.venv\Scripts\python -m pytest tests` shows no regressions.

---

## Phase 2 — Backtest / IC engine (pure) + the offline orchestrator

### Task 2.1: IC, ICIR, decile spread (pure)

**Files:** Create `trade-analyzer/src/analysis/backtest.py`; test
`trade-analyzer/tests/analysis/test_backtest.py`.

The engine works on a **panel**: a dict `{symbol: factor_frame}` + `{symbol: close}`,
or — simpler and what we test — pre-assembled **long tables**. Use this representation:
a `pd.DataFrame` with a MultiIndex (date, symbol) and one column per factor, plus a
`forward` Series (forward excess return) aligned to the same index.

**Step 1: Failing test**
```python
# trade-analyzer/tests/analysis/test_backtest.py
import numpy as np
import pandas as pd
from src.analysis import backtest


def _panel(n_days=200, n_syms=30, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    syms = [f"S{i}" for i in range(n_syms)]
    idx = pd.MultiIndex.from_product([dates, syms], names=["date", "symbol"])
    # factor `good` perfectly predicts forward; `noise` is random
    good = pd.Series(rng.normal(size=len(idx)), index=idx)
    fwd = good * 0.05 + rng.normal(scale=0.001, size=len(idx))  # forward excess return
    noise = pd.Series(rng.normal(size=len(idx)), index=idx)
    factors = pd.DataFrame({"good": good, "noise": noise}, index=idx)
    return factors, pd.Series(fwd, index=idx, name="forward")


def test_factor_ic_detects_signal():
    f, fwd = _panel()
    ic = backtest.factor_ic(f["good"], fwd)
    assert ic["mean_ic"] > 0.8 and ic["icir"] > 1.0
    ic_n = backtest.factor_ic(f["noise"], fwd)
    assert abs(ic_n["mean_ic"]) < 0.2


def test_quantile_spread_positive_for_signal():
    f, fwd = _panel()
    spread = backtest.quantile_spread(f["good"], fwd, q=5)
    assert spread > 0
```

**Step 3: Implement** `backtest.py`:
```python
"""Pure backtest / IC engine for the swing factor model.

Works on a long panel: a factor Series (or DataFrame) indexed by a (date, symbol)
MultiIndex, plus a forward-excess-return Series on the same index. No I/O.
"""
from typing import Dict

import numpy as np
import pandas as pd


def _spearman(a: pd.Series, b: pd.Series) -> float:
    m = a.notna() & b.notna()
    if m.sum() < 5:
        return np.nan
    return float(a[m].rank().corr(b[m].rank()))


def factor_ic(factor: pd.Series, forward: pd.Series) -> dict:
    """Per-date cross-sectional Spearman IC of `factor` vs `forward`, summarized.
    Returns {mean_ic, icir, n_days}."""
    df = pd.DataFrame({"f": factor, "y": forward}).dropna()
    ics = df.groupby(level="date").apply(lambda g: _spearman(g["f"], g["y"]))
    ics = ics.dropna()
    if ics.empty:
        return {"mean_ic": 0.0, "icir": 0.0, "n_days": 0}
    mean_ic = float(ics.mean())
    std = float(ics.std(ddof=0)) or 1e-9
    return {"mean_ic": mean_ic, "icir": mean_ic / std, "n_days": int(len(ics))}


def quantile_spread(factor: pd.Series, forward: pd.Series, q: int = 5) -> float:
    """Mean forward return of the top quantile minus the bottom, per date, averaged."""
    df = pd.DataFrame({"f": factor, "y": forward}).dropna()

    def _one(g):
        if len(g) < q:
            return np.nan
        ranks = g["f"].rank(method="first")
        bins = pd.qcut(ranks, q, labels=False, duplicates="drop")
        top = g["y"][bins == bins.max()].mean()
        bot = g["y"][bins == bins.min()].mean()
        return top - bot

    sp = df.groupby(level="date").apply(_one).dropna()
    return float(sp.mean()) if not sp.empty else 0.0
```

**Step 4: Run → PASS.** **Step 5: Commit** `feat(backtest): Spearman IC/ICIR + quantile spread`.

### Task 2.2: Cross-sectional z-score, IC-weighting, composite

**Step 1: Failing test** (append):
```python
def test_zscore_cross_section_is_standardized():
    f, _ = _panel()
    z = backtest.zscore_by_date(f)
    # each date's cross-section is ~mean 0, std 1
    by_date_std = z["good"].groupby(level="date").std(ddof=0).dropna()
    assert (by_date_std.between(0.8, 1.2)).mean() > 0.9


def test_icir_weights_favor_signal():
    f, fwd = _panel()
    ics = {c: backtest.factor_ic(f[c], fwd) for c in f.columns}
    w = backtest.icir_weights(ics)
    assert w["good"] > w["noise"]
    assert abs(sum(w.values()) - 1.0) < 1e-9 or all(v >= 0 for v in w.values())


def test_composite_score_predicts():
    f, fwd = _panel()
    z = backtest.zscore_by_date(f)
    ics = {c: backtest.factor_ic(f[c], fwd) for c in f.columns}
    w = backtest.icir_weights(ics)
    comp = backtest.composite(z, w)
    assert backtest.factor_ic(comp, fwd)["mean_ic"] > 0.7
```

**Step 3: Implement** (append to `backtest.py`):
```python
def zscore_by_date(factors: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-score: per date, (x - mean) / std across symbols."""
    def _z(g):
        mu = g.mean()
        sd = g.std(ddof=0).replace(0, np.nan)
        return (g - mu) / sd
    return factors.groupby(level="date").transform(_z)


def icir_weights(ic_by_factor: Dict[str, dict], min_icir: float = 0.3) -> Dict[str, float]:
    """Non-negative weights ∝ max(0, ICIR), dropping factors below `min_icir`.
    Normalized to sum to 1 (uniform fallback if nothing qualifies)."""
    raw = {k: max(0.0, v.get("icir", 0.0)) for k, v in ic_by_factor.items()
           if v.get("icir", 0.0) >= min_icir}
    total = sum(raw.values())
    if total <= 0:
        n = len(ic_by_factor) or 1
        return {k: 1.0 / n for k in ic_by_factor}
    return {k: v / total for k, v in raw.items()}


def composite(zscores: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    """Weighted sum of z-scored factors (only weighted columns contribute)."""
    cols = [c for c in weights if c in zscores.columns]
    if not cols:
        return pd.Series(np.nan, index=zscores.index)
    w = pd.Series({c: weights[c] for c in cols})
    return (zscores[cols] * w).sum(axis=1, min_count=1)
```

**Step 4: Run → PASS.** **Step 5: Commit** `feat(backtest): cross-sectional z-score, ICIR-weighting, composite`.

### Task 2.3: Walk-forward + calibration

**Step 1: Failing test** (append):
```python
def test_walk_forward_reports_oos_ic():
    f, fwd = _panel(n_days=400)
    res = backtest.walk_forward(f, fwd, train=150, test=50, step=50)
    assert res["oos_ic"] > 0.5            # the signal survives out-of-sample
    assert res["n_folds"] >= 2
    assert "good" in res["weights"]


def test_calibration_bands_monotone():
    f, fwd = _panel()
    z = backtest.zscore_by_date(f)
    w = backtest.icir_weights({c: backtest.factor_ic(f[c], fwd) for c in f.columns})
    comp = backtest.composite(z, w)
    bands = backtest.calibrate(comp, fwd, n_bands=5)
    means = [b["mean_fwd"] for b in bands]
    assert means == sorted(means)         # higher score band -> higher mean forward
```

**Step 3: Implement** (append):
```python
def walk_forward(factors, forward, train=252, test=63, step=63) -> dict:
    """Rolling train→test. Fit ICIR-weights on each train window, score the next
    (unseen) test window, collect the composite's OOS IC. Returns the OOS IC, fold
    count, and the weights from the LAST (most recent) train window for live use."""
    dates = factors.index.get_level_values("date").unique().sort_values()
    folds, oos_ics, last_weights = 0, [], {}
    i = train
    while i + test <= len(dates):
        tr = dates[i - train:i]
        te = dates[i:i + test]
        f_tr = factors[factors.index.get_level_values("date").isin(tr)]
        y_tr = forward[forward.index.get_level_values("date").isin(tr)]
        f_te = factors[factors.index.get_level_values("date").isin(te)]
        y_te = forward[forward.index.get_level_values("date").isin(te)]
        w = icir_weights({c: factor_ic(f_tr[c], y_tr) for c in f_tr.columns})
        comp_te = composite(zscore_by_date(f_te), w)
        oos_ics.append(factor_ic(comp_te, y_te)["mean_ic"])
        last_weights = w
        folds += 1
        i += step
    oos = float(np.nanmean(oos_ics)) if oos_ics else 0.0
    return {"oos_ic": oos, "n_folds": folds, "weights": last_weights,
            "oos_ic_by_fold": [float(x) for x in oos_ics]}


def calibrate(comp: pd.Series, forward: pd.Series, n_bands: int = 5) -> list:
    """Bucket composite scores into n_bands by quantile; per band record score
    range, mean forward return, and hit-rate P(forward>0). Sorted ascending."""
    df = pd.DataFrame({"c": comp, "y": forward}).dropna()
    if len(df) < n_bands:
        return []
    df["band"] = pd.qcut(df["c"].rank(method="first"), n_bands, labels=False)
    out = []
    for b, g in df.groupby("band"):
        out.append({
            "band": int(b),
            "score_lo": float(g["c"].min()), "score_hi": float(g["c"].max()),
            "mean_fwd": float(g["y"].mean()),
            "hit_rate": float((g["y"] > 0).mean()),
            "n": int(len(g)),
        })
    return sorted(out, key=lambda d: d["score_lo"])
```

**Step 4: Run → PASS.** **Step 5: Commit** `feat(backtest): walk-forward OOS validation + calibration`.

### Task 2.4: Offline orchestrator (`fit_swing_model.py`)

**Files:** Create `trade-analyzer/fit_swing_model.py` (NOT pure — pulls data via the proxy).

This is an orchestration script; **no unit test** (it does I/O). It must be runnable:
`cd trade-analyzer && ..\.venv\Scripts\python fit_swing_model.py`. Structure:

```python
"""Offline: fit the swing factor model and write the artifact + research report.

Pulls daily history for a liquid fit universe via the schwab-proxy, builds factor
frames (src.analysis.factors), assembles a (date, symbol) panel with forward
EXCESS returns vs SPY, runs the backtest (src.analysis.backtest), and writes
SWING_MODEL (json) + SWING_MODEL_REPORT (markdown). Run weekly/monthly. Never
imported by a service.
"""
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL, SWING_MODEL, SWING_MODEL_REPORT, TRADE_ANALYZER
# ... import a proxy client (mirror trade_svc.compute._price_history via PROXY_URL),
#     src.analysis.factors, src.analysis.backtest
```
Implement these functions (keep each small):
- `fit_universe()` → list of ~100 liquid tickers (hard-coded curated list is fine for v1;
  document it). Include SPY + the 11 sector ETFs separately for refs.
- `fetch_daily(symbol)` → OHLCV DataFrame via `GET {PROXY_URL}/pricehistory?symbol=…&periodType=year&period=3&frequencyType=daily&frequency=1` (mirror the param shape in `services/trade_svc/compute._price_history`). Parallelize with a thread pool (≤8).
- `sector_of(symbol)` → sector ETF (reuse the `_SYMBOL_SECTOR` map idea or a local dict).
- `build_panel(histories, spy, sector_map, horizons=(10,20,40))` → for the PRIMARY horizon (20): a (date,symbol) factor panel via `factors.compute_factor_frame`, and a `forward` Series = forward excess return vs SPY over 20 bars: `(close.shift(-20)/close - 1) - (spy.shift(-20)/spy - 1)`, dropping the last 20 bars per symbol (no future data). Also compute IC per factor for ALL horizons for the report.
- `fit()` → assemble panel, `backtest.walk_forward(...)`, `backtest.factor_ic(...)` per factor (full-sample, for the report), fit final weights on the FULL sample (`icir_weights`), z-score normalization stats per factor (mean/std over the full panel for the historical-norm fallback), `backtest.calibrate(...)`.
- `write_artifact(...)` → `SWING_MODEL` JSON:
  ```json
  {"version":"<YYYY-MM-DD>","fit_universe_n":100,"horizon":20,
   "regimes":{"all":{"weights":{...},"factor_ic":{...},
                     "norm":{"<factor>":{"mean":..,"std":..}},
                     "calibration":[...],"oos_ic":..,"oos_ic_by_fold":[..]}}}
  ```
  (regime key "all" — the C-hook.) Create `TRADE_ANALYZER/"data"` if missing.
- `write_report(...)` → `SWING_MODEL_REPORT` Markdown: a factor IC table (factor · mean IC · ICIR · weight · per-horizon IC), the composite OOS IC + per-fold, the calibration bands, and the documented limitations (survivorship, non-stationarity, thin live cross-section).
- `main()` → fit + write both; print a one-line summary (OOS IC, # factors kept).

**Step: Run it** (requires the proxy up):
`cd trade-analyzer && ..\.venv\Scripts\python fit_swing_model.py`
Expected: prints `OOS IC=<x> · kept <n>/<N> factors`; `data/swing_model.json` +
`data/swing_model_report.md` exist.

**Step: Commit** `feat(trade): offline swing-model fit orchestrator`.

> **Phase 2 gate (DECISION POINT):** open `data/swing_model_report.md`. If the composite
> **OOS IC is not clearly positive** (rule of thumb ≥ ~0.02–0.03 mean IC) with a positive
> decile spread, STOP and iterate the factor set before wiring the model live — do NOT
> promote a non-validating model. Report the numbers to the user and decide together.

---

## Phase 3 — Live scorer + contract

### Task 3.1: Extend the `TradeAnalysis` contract

**Files:** Modify `shared/contracts/trade.py`; append to its test file.

**Step 1: Failing test** (`shared/contracts/tests/test_trade.py`):
```python
def test_trade_accepts_swing_model_block():
    t = TradeAnalysis(symbol="AAPL", swing_model={
        "verdict": "BUY", "score": 1.4, "percentile": 88,
        "expected_fwd": 0.031, "hit_rate": 0.63, "horizon_days": 20,
        "contributions": [{"factor": "mom_12_1", "z": 1.2, "weight": 0.3,
                           "contribution": 0.36, "ic": 0.05}],
        "model_version": "2026-06-22", "oos_ic": 0.04, "source": "validated",
    })
    assert t.swing_model["verdict"] == "BUY"


def test_trade_swing_model_optional():
    assert TradeAnalysis(symbol="AAPL").swing_model is None
```

**Step 3:** Add to `TradeAnalysis` (after `markov`): `swing_model: dict | None = None`.

**Step 4: Run** `.venv\Scripts\python -m pytest shared\contracts -q` → green.
**Step 5: Commit** `feat(contracts): optional swing_model block on TradeAnalysis`.

### Task 3.2: Live scorer (`services/trade_svc/swing_model.py`)

**Files:** Create `services/trade_svc/swing_model.py`; test
`services/trade_svc/tests/test_swing_model.py`.

Pure-ish: artifact + universe-snapshot in, verdict dict out. Loading the artifact and the
universe snapshot is done by thin functions that tests monkeypatch.

**Step 1: Failing test**
```python
import numpy as np, pandas as pd
from services.trade_svc import swing_model as sm

_ARTIFACT = {
    "version": "2026-06-22", "horizon": 20,
    "regimes": {"all": {
        "weights": {"mom_12_1": 0.6, "pth": 0.4},
        "factor_ic": {"mom_12_1": {"mean_ic": 0.05, "icir": 1.2},
                      "pth": {"mean_ic": 0.03, "icir": 0.8}},
        "norm": {"mom_12_1": {"mean": 0.0, "std": 1.0},
                 "pth": {"mean": 0.9, "std": 0.1}},
        "calibration": [
            {"band": 0, "score_lo": -3, "score_hi": -0.5, "mean_fwd": -0.02, "hit_rate": 0.40, "n": 100},
            {"band": 1, "score_lo": -0.5, "score_hi": 0.5, "mean_fwd": 0.00, "hit_rate": 0.50, "n": 100},
            {"band": 2, "score_lo": 0.5, "score_hi": 3, "mean_fwd": 0.03, "hit_rate": 0.64, "n": 100}],
        "oos_ic": 0.04}}}


def test_score_symbol_bullish(monkeypatch):
    monkeypatch.setattr(sm, "load_artifact", lambda: _ARTIFACT)
    # universe snapshot: this symbol's factors are top of the cross-section
    snap = {"mom_12_1": [0.0, 0.1, 0.2], "pth": [0.8, 0.85, 0.9]}
    cur = {"mom_12_1": 0.5, "pth": 0.99}     # well above the snapshot -> high z
    out = sm.score_symbol(cur, universe_snapshot=snap, artifact=_ARTIFACT)
    assert out["verdict"] == "BUY"
    assert out["score"] > 0.5 and 0 <= out["percentile"] <= 100
    assert out["expected_fwd"] == 0.03 and out["hit_rate"] == 0.64
    assert any(c["factor"] == "mom_12_1" for c in out["contributions"])


def test_score_symbol_degrades_without_artifact():
    assert sm.score_symbol({"mom_12_1": 0.5}, universe_snapshot=None, artifact=None) is None
```

**Step 3: Implement** `swing_model.py`:
```python
"""Live swing-model scorer (Tier-2). Loads the offline artifact and scores ONE
symbol's current factors against a cached universe snapshot. Pure scoring; the
artifact/snapshot loaders are thin and monkeypatched in tests. Defensive: returns
None on any failure so analyze() can fall back to the legacy verdict."""
import json
import numpy as np
from repo_paths import SWING_MODEL

_BUY_HIT, _SELL_HIT = 0.58, 0.45   # calibrated thresholds (tunable from the report)


def load_artifact():
    try:
        return json.loads(SWING_MODEL.read_text(encoding="utf-8"))
    except Exception:
        return None


def _zscore(value, series_or_norm):
    arr = np.asarray(series_or_norm, dtype="float64")
    arr = arr[np.isfinite(arr)]
    if len(arr) >= 5:
        mu, sd = float(arr.mean()), float(arr.std(ddof=0))
    else:
        return None
    return (value - mu) / sd if sd > 0 else 0.0


def score_symbol(current_factors, universe_snapshot, artifact):
    """current_factors: {factor: value} for the symbol now.
    universe_snapshot: {factor: [values across the watchlist]} or None.
    Returns the swing_model verdict dict, or None to degrade."""
    try:
        if not artifact:
            return None
        reg = artifact["regimes"]["all"]
        weights, norm, calib = reg["weights"], reg.get("norm", {}), reg["calibration"]
        contribs, comp = [], 0.0
        for f, w in weights.items():
            v = current_factors.get(f)
            if v is None or not np.isfinite(v):
                continue
            basis = (universe_snapshot or {}).get(f)
            z = _zscore(v, basis) if basis else None
            if z is None and f in norm and norm[f]["std"]:
                z = (v - norm[f]["mean"]) / norm[f]["std"]
            if z is None:
                continue
            c = w * z
            comp += c
            contribs.append({"factor": f, "z": round(z, 3), "weight": w,
                             "contribution": round(c, 3),
                             "ic": reg.get("factor_ic", {}).get(f, {}).get("mean_ic")})
        band = _band_for(comp, calib)
        verdict = ("BUY" if band["hit_rate"] >= _BUY_HIT
                   else "SELL" if band["hit_rate"] <= _SELL_HIT else "HOLD")
        return {
            "verdict": verdict, "score": round(comp, 3),
            "percentile": _percentile(comp, calib),
            "expected_fwd": band["mean_fwd"], "hit_rate": band["hit_rate"],
            "horizon_days": artifact.get("horizon", 20),
            "contributions": sorted(contribs, key=lambda d: abs(d["contribution"]), reverse=True),
            "model_version": artifact.get("version"), "oos_ic": reg.get("oos_ic"),
            "source": "validated",
        }
    except Exception:
        return None


def _band_for(comp, calib):
    for b in calib:
        if comp <= b["score_hi"]:
            return b
    return calib[-1]


def _percentile(comp, calib):
    # crude percentile from band position + within-band interpolation
    lo, hi = calib[0]["score_lo"], calib[-1]["score_hi"]
    if hi <= lo:
        return 50
    return int(np.clip((comp - lo) / (hi - lo) * 100, 0, 100))
```

**Step 4: Run** `.venv\Scripts\python -m pytest services\trade_svc\tests\test_swing_model.py -v` → green.
**Step 5: Commit** `feat(trade): live swing-model scorer`.

### Task 3.3: Universe-factor snapshot + wire into analyze()

**Files:** Modify `services/trade_svc/compute.py`, `services/trade_svc/handlers.py`; tests
`services/trade_svc/tests/test_swing_wiring.py`.

**Universe snapshot:** add `compute.build_universe_factor_snapshot()` — for each watchlist
symbol (reuse `_MK_UNIVERSE` or the watchlist), fetch daily history, `factors.compute_factor_frame`,
take the **last row** → `{factor: [values across symbols]}`. Cache helper
`cache:trade:universe_factors` (lazy daily, mirror `get_prior`/`_read_prior_cache`). A
handler `refresh_universe_factors` publishes it (optional command; also lazily built in
analyze if missing/stale).

**Wire into `analyze()`** (after the daily history + spy/sector are fetched, near the
Markov block): compute the symbol's current factors (`factors.compute_factor_frame(daily,
spy_close, sector_close).iloc[-1].to_dict()`), call `swing_model.score_symbol(cur,
snapshot, artifact)`. If it returns a dict, attach it as `result["swing_model"]`; the
existing `position_verdict` is kept (legacy) and ALSO attached. If `None`, `swing_model`
stays absent (page shows legacy). Add `"swing_model": None` to the handler `_FIELDS`.

**Step 1: Failing test** (`test_swing_wiring.py`): monkeypatch `swing_model.load_artifact`
+ `compute._read_universe_snapshot`, feed a synthetic daily frame, assert
`compute.analyze`-style assembly attaches a `swing_model` dict; and that with the artifact
absent it's `None` and `position_verdict` still present. (Test `build_universe_factor_snapshot`
shape with monkeypatched `_price_history`.) Follow the patterns in `test_markov_analyze.py`.

**Step 4: Run** `.venv\Scripts\python -m pytest services\trade_svc -q` → green.
**Step 5: Commit** `feat(trade): universe snapshot + swing_model wired into analyze()`.

### Task 3.4: Head-less live verification

Restart `trade_svc` (load new code). Enqueue `analyze AAPL` via the bus; read
`cache:trade:analysis`; confirm `swing_model` is populated (verdict, percentile,
expected_fwd, contributions, model_version) — OR `None` if no artifact yet (then run
Phase 2's fit first). Mirror the Markov head-less check pattern. No commit (verification).

---

## Phase 4 — Page: calibrated outcome + evidence

### Task 4.1: Pure builders

**Files:** Modify `webgui/pages/trade.py`; append to `webgui/tests/test_trade.py`.

Add pure builders:
- `swing_headline(sm)` → e.g. `{"verdict":"BUY","line":"top 88% · ≈+3.1% / 4wk · beat-SPY 63%"}` or None.
- `swing_contrib_rows(sm)` → `[{factor, z, weight, contribution, ic}]` formatted strings (e.g. ic as `+0.05` or `—`).
- `swing_model_meta(sm)` → `{"version":..., "oos_ic":"+0.04"}` for the track-record line.
- All tolerate None.

**Step 1–5:** TDD with a sample `swing_model` dict (verdict/percentile/expected_fwd/hit_rate/
contributions/model_version/oos_ic). Commit `feat(webgui): pure builders for swing-model verdict`.

### Task 4.2: Render the validated verdict in the Position card

**Files:** Modify `webgui/pages/trade.py`.

In `_fill_verdict_card` for the **Position** card: when `res.get("swing_model")` is present,
the headline shows the **validated** verdict + `swing_headline` line; an **evidence
expander** ("Why — validated factors") renders `swing_contrib_rows` (factor · z · weight ·
contribution · IC) + the `swing_model_meta` track-record line; the **legacy** heuristic
score moves into a second collapsed expander ("Legacy heuristic"). When `swing_model` is
absent, render exactly as today (legacy primary). No new chart → no Highcharts gotchas.
Markov card unchanged (it already rides the composite).

**Step:** `cd webgui && ..\.venv\Scripts\python -m pytest -q` green. Then **browser-verify**
(restart the webgui preview, `/trade`, analyze AAPL): confirm the validated headline +
evidence expander render and the legacy view is tucked away. Use DOM `preview_eval` (the
screenshot times out on this page). Commit `feat(webgui): validated swing verdict + evidence on the Trade page`.

---

## Phase 5 — Cutover + docs

### Task 5.1: Promote validated to primary (config flag)

Add a small switch (e.g. `services/trade_svc/settings.py` `SWING_MODEL_PRIMARY = True`, or
infer from artifact presence + OOS-IC gate). When primary and a valid artifact exists, the
Position verdict's **verdict + score** come from `swing_model`; else legacy. Markov reads the
validated composite when present. TDD the selector helper. Commit
`feat(trade): promote validated swing model to primary verdict`.

### Task 5.2: Docs + manuals + ? popup

- **CLAUDE.md**: new "Validated swing evaluation" subsection under the Trade page (factor
  library, offline harness + artifact, live scorer, calibrated verdict, OOS-IC gate); bump
  "Last updated"; update the `/trade` routes row.
- **Manuals** (`docs/manuals/*/*.md` → rebuild html+docx via `build_docs.py`): User Guide
  (what the calibrated verdict + evidence mean), Technical Reference (the IC/walk-forward
  methodology + factor formulas), API Reference (the `swing_model` contract block +
  `cache:trade:universe_factors` + the artifact + `fit_swing_model.py`).
- **page_help.py**: `/trade` ? popup — note the verdict is now backtested with a hit-rate.
- Commit `docs: document the validated swing evaluation`.

### Task 5.3: Final verification

Run all suites: `cd trade-analyzer && ..\.venv\Scripts\python -m pytest tests -q`;
`.venv\Scripts\python -m pytest services\trade_svc shared\contracts -q`;
`cd webgui && ..\.venv\Scripts\python -m pytest -q`. All green. Confirm the EOD/other pages
that read `cache:trade:analysis` tolerate the additive field. Commit any doc fixups.

---

## Done criteria

- Pure `factors.py` (evidence-backed factor library) + `backtest.py` (IC/ICIR/spread/
  walk-forward/weighting/calibration), fully unit-tested.
- `fit_swing_model.py` produces `swing_model.json` + a research report; the **OOS-IC gate**
  was reviewed before cutover.
- `swing_model.py` live scorer wired into `analyze()` defensively (no artifact → legacy,
  never crashes); `swing_model` rides `cache:trade:analysis`; Markov runs on the validated
  composite.
- `/trade` shows a **calibrated** swing verdict (expected return + hit-rate + percentile)
  with a factor-IC evidence expander and the model's OOS track record; legacy tucked away.
- All suites green; head-less + browser verified; CLAUDE.md + manuals + ? popup updated.
- Investor verdict untouched (deferred); C (regime) / B (ML) are clean future extensions of
  the same harness.
```
