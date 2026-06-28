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
