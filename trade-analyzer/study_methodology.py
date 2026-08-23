"""Phase 4, tasks 4.3 + 4.4 — regime conditioning and covariance-aware weighting.

Task 4.1's ablation reframed this phase. The composite's OOS IC is +0.0206;
`low_vol` ALONE scores **+0.0730** and the other nine factors together score
**-0.0262**. So the model's entire measured edge is one factor carried on an
INVERTED sign — a high-volatility tilt — and the other nine dilute it.

That makes one question decisive, and it is a regime question: over a 20-day
forward excess return vs SPY, a high-volatility tilt IS a beta tilt, which pays
in a rising tape and reverses in a falling one. If `low_vol`'s IC flips sign by
regime, the model is not a factor model with a quirk — it is a beta bet, and
shipping it as "validated" would be the strongest possible version of the
mistake this repo keeps documenting.

Both tasks share one cached panel so nothing here is confounded with a fetch.

Run manually with the proxy up:
    cd trade-analyzer && ..\\.venv\\Scripts\\python study_methodology.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from src.analysis import backtest as B          # noqa: E402
from src.analysis import regime as RG           # noqa: E402
from research import harness, variants          # noqa: E402
import fit_swing_model as FSM                   # noqa: E402

WF = dict(train=FSM.TRAIN, test=FSM.TEST, step=FSM.STEP)


def _fmt_t(d):
    return f"{d['t']:+.2f}" if d["t"] is not None else "n/a"


def main():
    panel, forward, meta = harness.build_or_load(FSM.UNIVERSE_SECTOR)
    spy = FSM._close(FSM.fetch_daily("SPY", years=meta["years"]))
    regimes = RG.classify(spy)
    dates = panel.index.get_level_values("date").unique()
    reg_on_panel = regimes.reindex(dates)
    print(f"panel {panel.shape}; regimes {reg_on_panel.value_counts().to_dict()}")

    L = ["# Methodology study — Phase 4.3 (regime) + 4.4 (covariance)", "",
         f"Panel: **{meta['universe_n']}** symbols · {meta['years']}yr · horizon "
         f"{meta['horizon']}d · fetched {meta['fetched']} · {panel.shape[0]:,} rows",
         "", "Both tasks run against the SAME cached panel, so nothing below is "
         "confounded with a re-fetch.", "",
         "## 0. Regime mix over the fit window", "",
         "| regime | panel days | share |", "|---|---:|---:|"]
    counts = reg_on_panel.value_counts()
    tot = int(counts.sum())
    for r, n in counts.items():
        L.append(f"| {r} | {n} | {n / tot:.1%} |")
    L.append(f"| _unlabelled (warmup)_ | {int(reg_on_panel.isna().sum())} | — |")

    # ---- 1. the decisive question -------------------------------------------
    L += ["", "## 1. Does `low_vol`'s edge flip sign by regime?", "",
          "Per-regime cross-sectional IC. A high-volatility tilt over a 20-day "
          "forward EXCESS return vs SPY is a beta tilt; if it pays in one regime "
          "and reverses in another, it is exposure, not alpha.", "",
          "| factor | " + " | ".join(RG.REGIMES) + " | all |",
          "|---|" + "---:|" * (len(RG.REGIMES) + 1)]
    lab_on_rows = reg_on_panel.reindex(panel.index.get_level_values("date")).to_numpy()
    per_reg = {}
    for r in RG.REGIMES:
        mask = lab_on_rows == r
        per_reg[r] = (panel[mask], forward[mask])
    for c in sorted(panel.columns,
                    key=lambda k: -abs(B.factor_ic(panel[k], forward)["mean_ic"])):
        cells = [f"{B.factor_ic(per_reg[r][0][c], per_reg[r][1])['mean_ic']:+.4f}"
                 for r in RG.REGIMES]
        allic = B.factor_ic(panel[c], forward)["mean_ic"]
        L.append(f"| {c} | " + " | ".join(cells) + f" | {allic:+.4f} |")

    # ---- 2. regime-conditioned walk-forward ---------------------------------
    pooled = variants.run_variant(panel, forward, label="pooled (shipping)", **WF)
    cond = variants.regime_walk_forward(panel, forward, regimes,
                                        label="regime-conditioned", **WF)
    L += ["", "## 2. Regime-conditioned weights vs the pooled fit", "",
          "Each test date is scored under its own regime's weights, fitted on "
          "that regime's dates inside the same train window. A thinly-trained "
          "regime falls back to pooled, and no test row is dropped — both "
          "variants score the same rows, so the ICs are comparable.", "",
          "| variant | OOS IC | neg folds | rows scored |", "|---|---:|---:|---:|"]
    for r in (pooled, cond):
        L.append(f"| {r['label']} | {r['oos_ic']:+.4f} | "
                 f"{r['negative_folds']}/{r['n_folds']} | {r['n_scored_rows']:,} |")
    d = variants.paired_delta(cond, pooled)
    L += ["", f"Paired: mean per-fold delta **{d['mean']:+.4f}**, t **{_fmt_t(d)}**, "
          f"n {d['n']}.",
          "", f"Regimes that fell back to pooled weights: "
          f"{cond['fallback_regimes'] or 'none'}.", "",
          "Last fold's per-regime weights:", ""]
    for r, w in cond["weights_by_regime"].items():
        top = {k: round(v, 3) for k, v in
               sorted(w.items(), key=lambda kv: -abs(kv[1]))[:5]}
        L.append(f"- **{r}** — {top}")

    # ---- 3. covariance-aware weighting --------------------------------------
    L += ["", "## 3. Covariance-aware weighting (C12)", "",
          "`signed_ic_weights` scores each factor univariately, so the "
          "correlated momentum cluster is paid four times for one signal.", "",
          "| weighting | OOS IC | neg folds |", "|---|---:|---:|"]
    cov = [pooled]
    for name, fit in (("ridge (alpha=1)", B.ridge_weights),
                      ("ridge (alpha=100)", lambda f, y: B.ridge_weights(f, y, alpha=100.0)),
                      ("orthogonalized residual IC", B.orthogonalized_ic_weights)):
        wf = B.walk_forward(panel, forward, fit_fn=fit, **WF)
        cov.append({"label": name, "oos_ic": wf["oos_ic"], "n_folds": wf["n_folds"],
                    "oos_ic_by_fold": wf["oos_ic_by_fold"],
                    "negative_folds": sum(1 for x in wf["oos_ic_by_fold"] if x < 0),
                    "weights": wf["weights"]})
    for r in cov:
        L.append(f"| {r['label']} | {r['oos_ic']:+.4f} | "
                 f"{r['negative_folds']}/{r['n_folds']} |")
    L += ["", "| vs the shipping weighter | mean per-fold delta | t |", "|---|---:|---:|"]
    for r in cov[1:]:
        dd = variants.paired_delta(r, pooled)
        L.append(f"| {r['label']} | {dd['mean']:+.4f} | {_fmt_t(dd)} |")
    L += ["", "Last-fold weights:", ""]
    for r in cov[1:]:
        top = {k: round(v, 3) for k, v in
               sorted(r["weights"].items(), key=lambda kv: -abs(kv[1]))[:6]}
        L.append(f"- **{r['label']}** — {top}")

    out = harness.RESEARCH_DIR / "methodology-study.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
