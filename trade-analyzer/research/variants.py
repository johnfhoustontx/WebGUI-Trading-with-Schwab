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
    }
