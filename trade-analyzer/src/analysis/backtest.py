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
    return float(a[m].rank().corr(b[m].rank()))


def factor_ic(factor: pd.Series, forward: pd.Series) -> dict:
    """Per-date cross-sectional Spearman IC of `factor` vs `forward`, summarized:
    {mean_ic, icir, n_days}."""
    df = pd.DataFrame({"f": factor, "y": forward}).dropna()
    if df.empty:
        return {"mean_ic": 0.0, "icir": 0.0, "n_days": 0}
    ics = df.groupby(level="date").apply(
        lambda g: _spearman(g["f"], g["y"])).dropna()
    if ics.empty:
        return {"mean_ic": 0.0, "icir": 0.0, "n_days": 0}
    mean_ic = float(ics.mean())
    std = float(ics.std(ddof=0)) or 1e-9
    return {"mean_ic": mean_ic, "icir": mean_ic / std, "n_days": int(len(ics))}


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


def composite(zscores: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    """Weighted sum of z-scored factors (only weighted columns contribute)."""
    cols = [c for c in weights if c in zscores.columns]
    if not cols:
        return pd.Series(np.nan, index=zscores.index)
    w = pd.Series({c: weights[c] for c in cols})
    return (zscores[cols] * w).sum(axis=1, min_count=1)
