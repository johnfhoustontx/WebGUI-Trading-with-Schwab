"""Phase 4 — replicate the weighting result over a longer window (more folds).

The methodology study found covariance-aware weighting beating the shipping
univariate signed-IC scheme by a wide margin: +0.0834 vs +0.0206, paired
t = +3.01 over **13 folds**. Two reasons that is not yet enough to act on:

  * 13 paired folds is a small sample, and this phase ran roughly nine
    comparisons in total. A Bonferroni-style correction would want |t| > ~3.4.
  * The 5-year window is one market. A result that only holds there is a
    description of 2021-2026, not of the weighting scheme.

A 10-year window roughly doubles the folds without reaching back into a market
whose microstructure no longer resembles this one. If orthogonalized residual IC
still wins over ~28 folds, the finding is real; if it collapses, the 13-fold
result was the sampling noise it might be.

The regime coverage table is kept as a side note only. Lengthening the window to
POPULATE regimes was the original motive, and that motive is gone: `low_vol`
carries the same sign in all three regimes, so C13's regime-artifact hypothesis
is refuted and regime-conditioned weights measured WORSE than pooled.

Run manually with the proxy up:
    cd trade-analyzer && ..\\.venv\\Scripts\\python study_window.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from src.analysis import backtest as B          # noqa: E402
from src.analysis import regime as RG           # noqa: E402
from research import harness, variants          # noqa: E402
import fit_swing_model as FSM                   # noqa: E402

YEARS = 10
FLOOR = 378 + 63          # research.artifact.default_min_regime_days


def _fmt_t(d):
    return f"{d['t']:+.2f}" if d["t"] is not None else "n/a"


def main():
    panel, forward, meta = harness.build_or_load(FSM.UNIVERSE_SECTOR, years=YEARS)
    spy = FSM._close(FSM.fetch_daily("SPY", years=YEARS))
    lab = RG.classify(spy).dropna()
    print(f"panel {panel.shape}; {meta['universe_n']} symbols; "
          f"regimes {lab.value_counts().to_dict()}")

    WF = dict(train=FSM.TRAIN, test=FSM.TEST, step=FSM.STEP)
    base = variants.run_variant(panel, forward, label="signed IC (shipping)", **WF)

    rows = [base]
    for name, fit in (("ridge (alpha=1)", B.ridge_weights),
                      ("ridge (alpha=100)",
                       lambda f, y: B.ridge_weights(f, y, alpha=100.0)),
                      ("orthogonalized residual IC", B.orthogonalized_ic_weights)):
        wf = B.walk_forward(panel, forward, fit_fn=fit, **WF)
        rows.append({"label": name, "oos_ic": wf["oos_ic"], "n_folds": wf["n_folds"],
                     "oos_ic_by_fold": wf["oos_ic_by_fold"],
                     "negative_folds": sum(1 for x in wf["oos_ic_by_fold"] if x < 0),
                     "weights": wf["weights"]})

    L = ["# Replication over a 10-year window — Phase 4", "",
         f"Panel: **{meta['universe_n']}** symbols · {YEARS}yr · horizon "
         f"{meta['horizon']}d · fetched {meta['fetched']} · {panel.shape[0]:,} rows",
         "", f"The 5-year study found orthogonalized residual IC beating the "
         f"shipping weighter by t = +3.01 over 13 folds. This window has "
         f"**{rows[0]['n_folds']}**, which is the point: 13 paired folds is a "
         "small sample, and this phase ran roughly nine comparisons.", "",
         "| weighting | OOS IC | neg folds | folds |", "|---|---:|---:|---:|"]
    for r in rows:
        L.append(f"| {r['label']} | {r['oos_ic']:+.4f} | "
                 f"{r['negative_folds']}/{r['n_folds']} | {r['n_folds']} |")

    L += ["", "### Paired against the shipping weighter", "",
          "| variant | mean per-fold delta | t | n |", "|---|---:|---:|---:|"]
    for r in rows[1:]:
        d = variants.paired_delta(r, base)
        L.append(f"| {r['label']} | {d['mean']:+.4f} | {_fmt_t(d)} | {d['n']} |")

    L += ["", "## Weights (last fold)", ""]
    for r in rows:
        top = {k: round(v, 3) for k, v in
               sorted(r["weights"].items(), key=lambda kv: -abs(kv[1]))[:6] if v}
        L.append(f"- **{r['label']}** — {top}")

    L += ["", "## Side note — regime coverage at 10 years", "",
          "Kept for the record only. Lengthening the window to POPULATE the "
          "regime keys was the original motive; that motive is gone, because "
          "`low_vol` carries the same sign in all three regimes and "
          "regime-conditioned weights measured worse than pooled.", "",
          f"| regime | days | clears the {FLOOR}-day floor? |", "|---|---:|---|"]
    for r, n in lab.value_counts().items():
        L.append(f"| {r} | {n} | {'yes' if n >= FLOOR else 'no'} |")

    out = harness.RESEARCH_DIR / "replication-study.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
