"""Build the artifact's ``regimes`` map — one weight set per market regime.

The artifact has carried a `regimes` dict with a single `"all"` key since the
model shipped, with the other keys documented as "C-ready". This fills them.

Three rules, each of which exists because the alternative fails silently:

**An underpowered regime is OMITTED, never written empty.** The scorer falls
back on a missing key; a key present with weights from a handful of days would
be used, and nothing on the card would say the weights came from almost no data.

**Calibration is OUT OF SAMPLE.** The shipping artifact calibrates its bands on
the full-sample composite — the same rows its weights were fitted on — so the
"calibrated mean" the Trade page prints as an expectation is an in-sample
statistic, optimistic by an unknown amount. The in-sample bands are kept
alongside so the size of that flattery is visible rather than assumed.

**Every emitted block is scoreable.** `swing_model._select_regime` requires both
weights and calibration, so a block missing either is dropped here rather than
silently falling back at score time.
"""
import numpy as np
import pandas as pd

from src.analysis import backtest as B
from research import variants as V


def _norm(panel):
    """Per-factor time-averaged cross-sectional mean/std — the scorer's
    thin-snapshot fallback basis. Mirrors ``fit_swing_model._xs_norm``."""
    out = {}
    for c in panel.columns:
        s = panel[c].dropna()
        if s.empty:
            out[c] = {"mean": 0.0, "std": 1.0}
            continue
        gw = s.groupby(level="date").transform(
            lambda g: g.clip(lower=g.quantile(0.02), upper=g.quantile(0.98)))
        means = gw.groupby(level="date").mean()
        stds = gw.groupby(level="date").std(ddof=0)
        stds = stds[stds > 0]
        out[c] = {"mean": float(means.mean()),
                  "std": float(stds.mean()) if len(stds) else 1.0}
    return out


def _block(panel, forward, *, train, test, step, weight_fn, n_bands, n_days):
    ics = {c: B.factor_ic(panel[c], forward) for c in panel.columns}
    weights = weight_fn(ics)
    if not weights:
        return None
    z = B.zscore_by_date(panel)
    insample = B.calibrate(B.composite(z, weights), forward, n_bands=n_bands)
    comp_oos, y_oos = V.oos_composite(panel, forward, train=train, test=test,
                                      step=step, weight_fn=weight_fn)
    oos_bands = B.calibrate(comp_oos, y_oos, n_bands=n_bands)
    if not oos_bands:
        return None
    wf = B.walk_forward(panel, forward, train=train, test=test, step=step,
                        weight_fn=weight_fn)
    return {
        "weights": weights,
        "factor_ic": {c: {k: ics[c][k] for k in ("mean_ic", "icir", "n_days")}
                      for c in ics},
        "norm": _norm(panel),
        "calibration": oos_bands,
        "calibration_basis": "out-of-sample",
        "calibration_insample": insample,
        "oos_ic": wf["oos_ic"],
        "oos_ic_by_fold": wf["oos_ic_by_fold"],
        "n_folds": wf["n_folds"],
        "n_days": int(n_days),
    }


def build_regimes(panel, forward, regimes, *, train=378, test=63, step=63,
                  weight_fn=None, min_regime_days=252, n_bands=5):
    """``{regime_key: block}``, always including ``"all"``.

    ``min_regime_days`` defaults to a full trading year: a weight set is only
    worth its own key if it was estimated on enough dates to survive a fold, and
    this model's edge is thin enough that an under-powered block would dominate
    whatever tape it happened to be selected on."""
    weight_fn = weight_fn or B.signed_ic_weights
    lab = pd.Series(regimes).dropna()
    out = {}

    allblk = _block(panel, forward, train=train, test=test, step=step,
                    weight_fn=weight_fn, n_bands=n_bands,
                    n_days=panel.index.get_level_values("date").nunique())
    if allblk:
        out["all"] = allblk

    row_lab = lab.reindex(panel.index.get_level_values("date")).to_numpy()
    for key, n_days in lab.value_counts().items():
        if n_days < min_regime_days:
            continue
        mask = row_lab == key
        sub, sub_fwd = panel[mask], forward[mask]
        if sub.empty:
            continue
        blk = _block(sub, sub_fwd, train=train, test=test, step=step,
                     weight_fn=weight_fn, n_bands=n_bands, n_days=n_days)
        if blk and blk["weights"] and blk["calibration"]:
            out[str(key)] = blk
    return out
