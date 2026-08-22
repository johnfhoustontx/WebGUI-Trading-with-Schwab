"""Phase 4 — re-measure the whole model on a beta-adjusted label.

The diagnostic found the composite's IC at +0.16 in up markets and -0.11 in down
markets, with nine of fourteen factors flipping sign. The cause is the LABEL:
``r_symbol - r_SPY`` pays a high-beta stock for leverage alone, so a fit over a
mostly-rising window learns to chase volatility.

``r_symbol - beta * r_market`` removes exactly the part leverage explains. This
rebuilds the panel's labels both ways and re-runs the comparison. The question is
blunt: with beta priced out, is there anything left?

An honest negative here is worth more than every positive number in this phase,
because those numbers are what a beta bet looks like from the inside.

Run manually with the proxy up:
    cd trade-analyzer && ..\\.venv\\Scripts\\python study_beta_label.py
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
RISK = ["low_vol", "semivol", "downside_beta", "vol_adj_mom", "max_effect"]


def _fmt_t(d):
    return f"{d['t']:+.2f}" if d["t"] is not None else "n/a"


def _label(hist, spy_close, index, horizon, beta_adjust):
    parts = []
    for sym in sorted({s for _, s in index}):
        df = hist.get(sym)
        if df is None:
            continue
        s = labels.forward_excess(FSM._close(df), spy_close, horizon=horizon,
                                  beta_adjust=beta_adjust)
        sub = index[index.get_level_values("symbol") == sym]
        parts.append(pd.Series(s.reindex(sub.get_level_values("date")).to_numpy(),
                               index=sub))
    return pd.concat(parts).reindex(index)


def main():
    panel, raw_fwd, meta = harness.build_or_load(EXPANDED)
    print(f"panel {panel.shape}; refetching closes to build the second label ...")
    spy_close = FSM._close(FSM.fetch_daily("SPY", years=meta["years"]))
    hist = FSM.fetch_all(list(EXPANDED))
    adj_fwd = _label(hist, spy_close, panel.index, meta["horizon"], True)
    print(f"beta-adjusted label: {adj_fwd.notna().sum():,} of {len(panel):,} rows")

    spy_fwd = (spy_close.shift(-meta["horizon"]) / spy_close - 1.0)
    row_mkt = spy_fwd.reindex(panel.index.get_level_values("date")).to_numpy()
    up, down = row_mkt > 0, row_mkt < 0

    L = ["# The beta-adjusted label — Phase 4's root-cause test", "",
         f"Panel: **{meta['universe_n']}** symbols · {meta['years']}yr · "
         f"horizon {meta['horizon']}d · {panel.shape[0]:,} rows", "",
         "`r - r_SPY` pays a high-beta stock for leverage alone. "
         "`r - beta*r_SPY` does not. Same panel, same factors, same folds — only "
         "the label differs.", "",
         "## 1. Does the up/down asymmetry survive the fix?", "",
         "The symptom was a composite scoring +0.16 in up markets and -0.11 in "
         "down markets. If the label was the cause, that gap should close.", "",
         "| label | IC (market up) | IC (market down) | gap |",
         "|---|---:|---:|---:|"]

    z = B.zscore_by_date(panel)
    fits = {}
    for name, fwd in (("raw excess (shipping)", raw_fwd),
                      ("beta-adjusted", adj_fwd)):
        w = B.signed_ic_weights({c: B.factor_ic(panel[c], fwd) for c in panel.columns})
        comp = B.composite(z, w)
        icu = B.factor_ic(comp[up], fwd[up])["mean_ic"]
        icd = B.factor_ic(comp[down], fwd[down])["mean_ic"]
        fits[name] = w
        L.append(f"| {name} | {icu:+.4f} | {icd:+.4f} | {abs(icu - icd):.4f} |")

    L += ["", "## 2. What is left of each factor", "",
          "A factor whose IC collapses under the adjusted label was measuring "
          "beta. One that survives was measuring something else.", "",
          "| factor | IC (raw) | IC (beta-adjusted) | kept? |",
          "|---|---:|---:|---|"]
    adj_w = fits["beta-adjusted"]
    for c in sorted(panel.columns,
                    key=lambda k: -abs(B.factor_ic(panel[k], raw_fwd)["mean_ic"])):
        ic_r = B.factor_ic(panel[c], raw_fwd)["mean_ic"]
        ic_a = B.factor_ic(panel[c], adj_fwd)["mean_ic"]
        star = " **(risk cluster)**" if c in RISK else ""
        w = adj_w.get(c, 0.0)
        L.append(f"| {c}{star} | {ic_r:+.4f} | {ic_a:+.4f} | "
                 f"{f'w={w:+.3f}' if w else 'dropped'} |")

    L += ["", "## 3. Out-of-sample, on the honest label", "",
          "| label | weighting | OOS IC | neg folds |", "|---|---|---:|---:|"]
    recs = {}
    for lname, fwd in (("raw excess", raw_fwd), ("beta-adjusted", adj_fwd)):
        r = variants.run_variant(panel, fwd, label=f"{lname} / signed IC", **WF)
        recs[(lname, "signed")] = r
        L.append(f"| {lname} | signed IC | {r['oos_ic']:+.4f} | "
                 f"{r['negative_folds']}/{r['n_folds']} |")
        wf = B.walk_forward(panel, fwd, fit_fn=B.orthogonalized_ic_weights, **WF)
        recs[(lname, "orth")] = {
            "label": f"{lname} / orthogonalized", "oos_ic": wf["oos_ic"],
            "oos_ic_by_fold": wf["oos_ic_by_fold"], "n_folds": wf["n_folds"],
            "negative_folds": sum(1 for x in wf["oos_ic_by_fold"] if x < 0)}
        L.append(f"| {lname} | orthogonalized | {wf['oos_ic']:+.4f} | "
                 f"{recs[(lname, 'orth')]['negative_folds']}/{wf['n_folds']} |")

    d = variants.paired_delta(recs[("beta-adjusted", "signed")],
                              recs[("raw excess", "signed")])
    L += ["", f"Paired (signed IC, adjusted vs raw): mean per-fold delta "
          f"**{d['mean']:+.4f}**, t **{_fmt_t(d)}**, n {d['n']}.", ""]

    L += ["## 4. Calibration on the honest label, out of sample", "",
          "| band | score range | mean fwd (OOS) | hit-rate | n |",
          "|---:|---|---:|---:|---:|"]
    comp, y = variants.oos_composite(panel, adj_fwd, **WF)
    bands = B.calibrate(comp, y, n_bands=5)
    for b in bands:
        L.append(f"| {b['band']} | [{b['score_lo']:+.2f}, {b['score_hi']:+.2f}] | "
                 f"{b['mean_fwd']:+.4f} | {b['hit_rate']:.2%} | {b['n']:,} |")
    if bands:
        L += ["", f"Top-minus-bottom spread: "
              f"**{bands[-1]['mean_fwd'] - bands[0]['mean_fwd']:+.4f}** over "
              f"{meta['horizon']} days, beta-neutral."]

    out = harness.RESEARCH_DIR / "beta-adjusted-label.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    for line in L[6:]:
        print(line)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
