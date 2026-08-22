"""Verify the beta-neutral calibration really is flat, rather than smoothed flat.

`backtest.calibrate` applies isotonic (pool-adjacent-violators) smoothing, which
merges bands whenever the score->outcome relationship is non-monotone. On the
beta-adjusted label every band came back with an IDENTICAL mean (-0.0006) and
hit rate (48.14%) — which is exactly what PAVA does to pure noise, and also
exactly what a bug would look like.

So: print the UNSMOOTHED per-band statistics beside the smoothed ones. If the
raw bands wander without ordering, the flat curve is the finding. If they are
cleanly ordered, the smoother is the bug.

The raw-label run is the control — its bands were NOT flattened (-0.0093 to
+0.0085), so `calibrate` demonstrably does not flatten everything.
"""
import sys
import pathlib

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from src.analysis import backtest as B          # noqa: E402
from research import harness, variants, labels  # noqa: E402
from research.universe import EXPANDED          # noqa: E402
import fit_swing_model as FSM                   # noqa: E402

WF = dict(train=FSM.TRAIN, test=FSM.TEST, step=FSM.STEP)


def _raw_bands(comp, y, n_bands=5):
    """calibrate() without the isotonic step."""
    df = pd.DataFrame({"c": comp, "y": y}).dropna()
    df["band"] = pd.qcut(df["c"].rank(method="first"), n_bands, labels=False)
    out = []
    for b, g in df.groupby("band"):
        out.append({"band": int(b), "mean_fwd": float(g["y"].mean()),
                    "hit_rate": float((g["y"] > 0).mean()), "n": int(len(g))})
    return sorted(out, key=lambda d: d["band"])


def main():
    panel, raw_fwd, meta = harness.build_or_load(EXPANDED)
    spy_close = FSM._close(FSM.fetch_daily("SPY", years=meta["years"]))
    hist = FSM.fetch_all(list(EXPANDED))

    parts = []
    for sym in sorted({s for _, s in panel.index}):
        df = hist.get(sym)
        if df is None:
            continue
        s = labels.forward_excess(FSM._close(df), spy_close,
                                  horizon=meta["horizon"], beta_adjust=True)
        sub = panel.index[panel.index.get_level_values("symbol") == sym]
        parts.append(pd.Series(s.reindex(sub.get_level_values("date")).to_numpy(),
                               index=sub))
    adj = pd.concat(parts).reindex(panel.index)

    for name, fwd in (("raw excess (control)", raw_fwd), ("beta-adjusted", adj)):
        comp, y = variants.oos_composite(panel, fwd, **WF)
        raw = _raw_bands(comp, y)
        smooth = B.calibrate(comp, y, n_bands=5)
        print(f"\n=== {name} ===")
        print(" band | UNSMOOTHED mean | hit   | smoothed mean | n")
        for r, s in zip(raw, smooth):
            print(f"   {r['band']}  |    {r['mean_fwd']:+.5f}     | {r['hit_rate']:.2%} |"
                  f"   {s['mean_fwd']:+.5f}    | {r['n']:,}")
        means = [r["mean_fwd"] for r in raw]
        monotone = all(a <= b for a, b in zip(means, means[1:]))
        print(f"  raw band means ordered ascending? {monotone}")
        print(f"  raw top-minus-bottom: {means[-1] - means[0]:+.5f}")


if __name__ == "__main__":
    main()
