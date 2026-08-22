"""Score one methodology variant against a fixed panel, comparably.

A variant is a single choice — a noise floor, a weighting scheme, a regime
subset. Two properties make the resulting records worth comparing:

**The choice must reach the folds.** ``walk_forward`` re-fits weights inside
every fold, so a floor applied only to the full-sample fit would leave every
fold on the default and the study would conclude "the floor doesn't matter"
with nothing to show it was never tested.

**The shipped facts come from the full-sample fit.** ``kept`` and ``weights``
describe the artifact that would actually score a live symbol; reading them off
the last fold would describe a model nobody runs.

Pure — the caller supplies the panel. No I/O, no fetching.
"""
import math
from functools import partial

import numpy as np
import pandas as pd

from src.analysis import backtest as B


def paired_delta(rec_a, rec_b):
    """Fold-by-fold difference between two variants: ``{a, b, mean, std, t, n}``.

    The variants run over the SAME walk-forward windows, so the folds pair and
    a paired t is both valid and far more powerful than comparing two means —
    which matters at this signal level, where the whole spread across five
    noise floors was 0.0055.

    ``t`` is None when the per-fold differences have no dispersion: a paired t
    is undefined there, and emitting an infinity would read as overwhelming
    significance when the honest statement is "identical, or identically
    shifted, in every fold"."""
    fa, fb = rec_a["oos_ic_by_fold"], rec_b["oos_ic_by_fold"]
    if len(fa) != len(fb):
        raise ValueError(
            f"fold counts differ ({len(fa)} vs {len(fb)}) — these variants ran "
            "over different panels and cannot be paired")
    diffs = [x - y for x, y in zip(fa, fb)]
    n = len(diffs)
    mean = sum(diffs) / n if n else 0.0
    if n < 2:
        return {"a": rec_a["label"], "b": rec_b["label"], "mean": mean,
                "std": 0.0, "t": None, "n": n}
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    std = math.sqrt(var)
    t = mean / (std / math.sqrt(n)) if std > 1e-12 else None
    return {"a": rec_a["label"], "b": rec_b["label"], "mean": mean,
            "std": std, "t": t, "n": n}


def run_variant(panel, forward, *, label, min_abs_ic=None, weight_fn=None,
                train=378, test=63, step=63):
    """One comparable record for this variant. ``min_abs_ic`` and ``weight_fn``
    are alternatives; ``weight_fn`` wins when both are given."""
    if weight_fn is None:
        weight_fn = (partial(B.signed_ic_weights, min_abs_ic=min_abs_ic)
                     if min_abs_ic is not None else B.signed_ic_weights)

    ics = {c: B.factor_ic(panel[c], forward) for c in panel.columns}
    weights = weight_fn(ics)
    wf = B.walk_forward(panel, forward, train=train, test=test, step=step,
                        weight_fn=weight_fn)
    folds = wf["oos_ic_by_fold"]
    return {
        "label": label,
        "min_abs_ic": min_abs_ic,
        "oos_ic": wf["oos_ic"],
        "n_folds": wf["n_folds"],
        "oos_ic_by_fold": folds,
        "negative_folds": sum(1 for x in folds if x < 0),
        "kept": sum(1 for v in weights.values() if v != 0),
        "n_factors": len(ics),
        "weights": weights,
        "factor_ic": {c: ics[c]["mean_ic"] for c in ics},
        "n_scored_rows": wf["n_scored_rows"],
    }


def _fold_windows(panel, train, test, step):
    dates = panel.index.get_level_values("date").unique().sort_values()
    i = train
    while i + test <= len(dates):
        yield dates[i - train:i], dates[i:i + test]
        i += step


def _slice(frame, dates):
    return frame[frame.index.get_level_values("date").isin(dates)]


def oos_composite(panel, forward, *, train=378, test=63, step=63,
                  weight_fn=None, fit_fn=None):
    """``(composite, forward)`` over the walk-forward's TEST windows only.

    The shipped artifact calibrates its score->outcome bands on the full-sample
    composite — the same rows the weights were fitted on — so the mean forward
    return and hit rate the Trade page prints are in-sample statistics. Bands
    built from THIS are not: no training row reaches them, which is the only way
    to answer whether a band's edge is real."""
    weight_fn = weight_fn or B.signed_ic_weights
    comps, ys = [], []
    for tr, te in _fold_windows(panel, train, test, step):
        f_tr, y_tr = _slice(panel, tr), _slice(forward, tr)
        w = (fit_fn(f_tr, y_tr) if fit_fn is not None
             else weight_fn({c: B.factor_ic(f_tr[c], y_tr) for c in f_tr.columns}))
        f_te = _slice(panel, te)
        comps.append(B.composite(B.zscore_by_date(f_te), w))
        ys.append(_slice(forward, te))
    if not comps:
        empty = pd.Series(dtype="float64")
        return empty, empty
    comp = pd.concat(comps)
    y = pd.concat(ys).reindex(comp.index)
    return comp, y


def regime_walk_forward(panel, forward, regimes, *, label, train=378, test=63,
                        step=63, weight_fn=None, min_regime_days=60):
    """Walk-forward where each test date is scored under ITS OWN regime's
    weights, fitted on that regime's dates within the same train window.

    Two rules keep the comparison against the pooled fit honest:

    **A thinly-trained regime falls back to pooled.** Weights from a handful of
    days are noise wearing a regime's name, and this model's edge is thin enough
    that such a weight set would dominate whatever fold it landed in.

    **No test row is ever dropped.** An unlabelled or fallen-back date is scored
    with the pooled weights, so the OOS IC covers exactly the sample the pooled
    variant covers. Dropping them would silently change the denominator.
    """
    weight_fn = weight_fn or B.signed_ic_weights
    reg = pd.Series(regimes).dropna()

    oos_ics, folds, scored = [], 0, 0
    weights_by_regime, fallback = {}, set()
    for tr, te in _fold_windows(panel, train, test, step):
        f_tr, y_tr = _slice(panel, tr), _slice(forward, tr)
        pooled_w = weight_fn({c: B.factor_ic(f_tr[c], y_tr) for c in f_tr.columns})

        tr_lab = reg.reindex(tr).dropna()
        reg_w = {}
        for r, cnt in tr_lab.value_counts().items():
            if cnt < min_regime_days:
                fallback.add(r)
                continue
            rd = tr_lab.index[tr_lab == r]
            f_r, y_r = _slice(panel, rd), _slice(forward, rd)
            w = weight_fn({c: B.factor_ic(f_r[c], y_r) for c in f_r.columns})
            if w:
                reg_w[r] = w
            else:
                fallback.add(r)

        z_te = B.zscore_by_date(_slice(panel, te))
        lab = pd.Series(reg.reindex(z_te.index.get_level_values("date")).to_numpy(),
                        index=z_te.index)
        parts = [B.composite(z_te[~lab.isin(list(reg_w))], pooled_w)]
        parts += [B.composite(z_te[lab == r], w) for r, w in reg_w.items()]
        parts = [p for p in parts if len(p)]
        comp = pd.concat(parts).reindex(z_te.index) if parts else pd.Series(
            float("nan"), index=z_te.index)

        oos_ics.append(B.factor_ic(comp, _slice(forward, te))["mean_ic"])
        scored += int(comp.notna().sum())
        weights_by_regime = reg_w          # the LAST fold's, as walk_forward does
        folds += 1

    oos = float(np.nanmean(oos_ics)) if oos_ics else 0.0
    return {
        "label": label, "oos_ic": oos, "n_folds": folds,
        "oos_ic_by_fold": [float(x) for x in oos_ics],
        "negative_folds": sum(1 for x in oos_ics if x < 0),
        "weights_by_regime": weights_by_regime,
        "fallback_regimes": sorted(fallback),
        "regime_days": {str(k): int(v) for k, v in reg.value_counts().items()},
        "n_scored_rows": scored,
    }
