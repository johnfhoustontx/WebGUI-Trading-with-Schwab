"""Pure backtest / IC engine for the swing factor model.

Operates on a long panel: a factor Series/DataFrame indexed by a (date, symbol)
MultiIndex, plus a forward-excess-return Series on the same index. No I/O.

Winsorization is CROSS-SECTIONAL (per date, across symbols) — applied in
`zscore_by_date` — so there is no temporal look-ahead (the raw factors from
factors.py are causal; the clip band uses only the contemporaneous cross-section).
"""
from typing import Dict

import numpy as np
import pandas as pd


def _spearman(a: pd.Series, b: pd.Series) -> float:
    m = a.notna() & b.notna()
    if m.sum() < 5:
        return np.nan
    if a[m].nunique() < 2 or b[m].nunique() < 2:
        return np.nan
    return float(a[m].rank().corr(b[m].rank()))


def factor_ic(factor: pd.Series, forward: pd.Series) -> dict:
    """Per-date cross-sectional Spearman IC of `factor` vs `forward`, summarized:
    {mean_ic, icir, n_days}. ICIR is only trusted with >= 5 IC-days and a real
    daily-IC dispersion; otherwise it is 0.0 (so a thin/fluke factor cannot be
    handed the weight by icir_weights)."""
    df = pd.DataFrame({"f": factor, "y": forward}).dropna()
    if df.empty:
        return {"mean_ic": 0.0, "icir": 0.0, "n_days": 0}
    ics = df.groupby(level="date").apply(
        lambda g: _spearman(g["f"], g["y"])).dropna()
    n = int(len(ics))
    if n == 0:
        return {"mean_ic": 0.0, "icir": 0.0, "n_days": 0}
    mean_ic = float(ics.mean())
    std = float(ics.std(ddof=0))
    icir = mean_ic / std if (n >= 5 and std > 1e-6) else 0.0
    return {"mean_ic": mean_ic, "icir": icir, "n_days": n}


def quantile_spread(factor: pd.Series, forward: pd.Series, q: int = 5) -> float:
    """Mean forward of the top minus the bottom quantile, per date, averaged."""
    df = pd.DataFrame({"f": factor, "y": forward}).dropna()

    def _one(g):
        if len(g) < q:
            return np.nan
        bins = pd.qcut(g["f"].rank(method="first"), q, labels=False, duplicates="drop")
        top = g["y"][bins == bins.max()].mean()
        bot = g["y"][bins == bins.min()].mean()
        return top - bot

    sp = df.groupby(level="date").apply(_one).dropna()
    return float(sp.mean()) if not sp.empty else 0.0


def zscore_by_date(factors: pd.DataFrame, winsor=(0.02, 0.98)) -> pd.DataFrame:
    """Per date, across symbols: winsorize (clip to the cross-sectional quantile
    band) then standardize (x - mean)/std. A constant cross-section -> all zeros
    (no inf). Look-ahead-free: only same-date data is used."""
    lo, hi = winsor

    def _z(col):  # col: one factor's values for one date (a Series across symbols)
        c = col.clip(lower=col.quantile(lo), upper=col.quantile(hi))
        mu = c.mean()
        sd = c.std(ddof=0)
        if not np.isfinite(sd) or sd <= 0:
            return c * 0.0
        return (c - mu) / sd

    return factors.groupby(level="date").transform(_z)


def icir_weights(ic_by_factor: Dict[str, dict], min_icir: float = 0.3) -> Dict[str, float]:
    """Non-negative weights proportional to max(0, ICIR), with factors below
    `min_icir` zeroed out. Every input factor gets a key (0.0 if it didn't
    qualify), so downstream consumers never KeyError on a dropped factor.
    Qualifying weights are normalized to sum to 1 (uniform fallback if none
    qualify)."""
    raw = {k: (max(0.0, v.get("icir", 0.0)) if v.get("icir", 0.0) >= min_icir else 0.0)
           for k, v in ic_by_factor.items()}
    total = sum(raw.values())
    if total <= 0:
        n = len(ic_by_factor) or 1
        return {k: 1.0 / n for k in ic_by_factor}
    return {k: v / total for k, v in raw.items()}


def mean_ic_weights(ic_by_factor: Dict[str, dict], min_ic: float = 0.0) -> Dict[str, float]:
    """Signed mean-IC weighting: weight proportional to max(0, mean_ic) for factors
    whose mean_ic > min_ic; negative-IC (wrong-sign) and sub-floor factors get 0
    weight. Normalized to sum to 1; empty dict if none qualify.

    Chosen over ICIR-weighting for the production fit because it is n-independent
    (a daily-IC ICIR is ~sqrt(252)x smaller than a monthly-IC ICIR, so a fixed
    ICIR threshold mis-selects, and a t-stat gate computed on small per-fold
    samples is unstable across walk-forward folds). Mean-IC weighting is stable
    across folds and directly rewards predictive edge while dropping wrong-sign
    factors."""
    raw = {k: max(0.0, v.get("mean_ic", 0.0)) for k, v in ic_by_factor.items()
           if v.get("mean_ic", 0.0) > min_ic}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()} if total > 0 else {}


def composite(zscores: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    """Weighted sum of z-scored factors (only weighted columns contribute)."""
    cols = [c for c in weights if c in zscores.columns]
    if not cols:
        return pd.Series(np.nan, index=zscores.index)
    w = pd.Series({c: weights[c] for c in cols})
    return (zscores[cols] * w).sum(axis=1, min_count=1)


def walk_forward(factors, forward, train=252, test=63, step=63, weight_fn=None) -> dict:
    """Rolling train->test. Fit weights on each train window (default
    `mean_ic_weights`, n-independent + stable across folds — see that function),
    score the next (unseen) test window, collect the composite's OOS IC. Returns
    oos_ic, fold count, the per-fold OOS ICs, and the weights from the LAST train
    window. Train/test never overlap within a fold; test windows across folds are
    non-overlapping when step >= test (the default step=test tiles them)."""
    weight_fn = weight_fn or mean_ic_weights
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
        w = weight_fn({c: factor_ic(f_tr[c], y_tr) for c in f_tr.columns})
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
    range, mean forward, hit-rate P(forward>0), n. Sorted ascending by score."""
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
