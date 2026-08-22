"""Phase 4, task 4.1 — the noise-floor study.

`signed_ic_weights` admits any factor whose |mean IC| clears 0.005. Phase 0's
refit showed the strong factors decaying while the KEPT count rose 6 -> 9: noise
crossing a floor that no longer discriminates. The visible symptom was `rs_spy`
taking a NEGATIVE weight, i.e. the model mildly rewarding a stock for lagging
SPY — backwards for a momentum model, and live in the evidence expander.

Raising the floor is a methodology change, so it is MEASURED here rather than
assumed. Every variant runs against one cached panel (see `panel_cache`) so a
floor change is never confounded with a fetch-date change.

Run manually with the proxy up:
    cd trade-analyzer && ..\\.venv\\Scripts\\python study_noise_floor.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from research import harness, variants          # noqa: E402
import fit_swing_model as FSM                   # noqa: E402

FLOORS = [0.0, 0.005, 0.01, 0.02, 0.03]
WATCH = "rs_spy"          # the factor whose sign flip prompted this study


def main():
    panel, forward, meta = harness.build_or_load(FSM.UNIVERSE_SECTOR)
    print(f"panel: {panel.shape[0]:,} rows x {panel.shape[1]} factors, "
          f"{meta['universe_n']} symbols")

    recs = [variants.run_variant(panel, forward, label=f"floor={f:g}",
                                 min_abs_ic=f, train=FSM.TRAIN,
                                 test=FSM.TEST, step=FSM.STEP)
            for f in FLOORS]

    lines = ["# Noise-floor study — Phase 4.1", "",
             f"Panel: **{meta['universe_n']}** symbols · {meta['years']}yr · "
             f"horizon {meta['horizon']}d · fetched {meta['fetched']} · "
             f"{panel.shape[0]:,} rows", "",
             "Every row below is the SAME panel — only the floor differs, so the "
             "OOS-IC column is a clean read on the floor alone.", "",
             f"| floor | OOS IC | neg folds | kept | {WATCH} weight |",
             "|---|---:|---:|---:|---:|"]
    for r in recs:
        w = r["weights"].get(WATCH, 0.0)
        lines.append(f"| {r['min_abs_ic']:g} | {r['oos_ic']:+.4f} | "
                     f"{r['negative_folds']}/{r['n_folds']} | "
                     f"{r['kept']}/{r['n_factors']} | {w:+.3f} |")

    best = max(recs, key=lambda r: r["oos_ic"])
    lines += ["", f"**Highest OOS IC: {best['label']} ({best['oos_ic']:+.4f})** — but see "
              "the paired test below before reading that as a win.", "",
              "## Is any floor actually different? (paired, vs the incumbent 0.005)", "",
              "The five variants run over the SAME 13 walk-forward windows, so the "
              "folds pair. A paired t is the test; the ranking of five means is not.", "",
              "| variant | mean per-fold delta | std | t | n |", "|---|---:|---:|---:|---:|"]
    base = next(r for r in recs if r["min_abs_ic"] == 0.005)
    for r in recs:
        if r is base:
            continue
        d = variants.paired_delta(r, base)
        t = f"{d['t']:+.2f}" if d["t"] is not None else "n/a"
        lines.append(f"| {r['label']} | {d['mean']:+.4f} | {d['std']:.4f} | {t} | {d['n']} |")

    lines += ["", "## Composition — is this a factor model or one factor?", "",
              "The floor sweep's best score came from a ONE-factor model "
              "(`low_vol` at weight -1.0), so the real question is what the other "
              "nine contribute. Each row is the same panel with a different column "
              "subset.", "",
              "| subset | OOS IC | neg folds | kept |", "|---|---:|---:|---:|"]
    others = [c for c in panel.columns if c != "low_vol"]
    ablations = [
        variants.run_variant(panel, forward, label="all 10 factors",
                             min_abs_ic=0.005, train=FSM.TRAIN, test=FSM.TEST,
                             step=FSM.STEP),
        variants.run_variant(panel[["low_vol"]], forward, label="low_vol ALONE",
                             min_abs_ic=0.005, train=FSM.TRAIN, test=FSM.TEST,
                             step=FSM.STEP),
        variants.run_variant(panel[others], forward, label="everything EXCEPT low_vol",
                             min_abs_ic=0.005, train=FSM.TRAIN, test=FSM.TEST,
                             step=FSM.STEP),
    ]
    for r in ablations:
        lines.append(f"| {r['label']} | {r['oos_ic']:+.4f} | "
                     f"{r['negative_folds']}/{r['n_folds']} | {r['kept']}/{r['n_factors']} |")
    lines += ["", "| comparison | mean per-fold delta | t |", "|---|---:|---:|"]
    for a, b in ((ablations[0], ablations[1]), (ablations[0], ablations[2])):
        d = variants.paired_delta(a, b)
        t = f"{d['t']:+.2f}" if d["t"] is not None else "n/a"
        lines.append(f"| {d['a']} vs {d['b']} | {d['mean']:+.4f} | {t} |")

    lines += ["", "## Weights per floor", ""]
    for r in recs:
        kept = {k: round(v, 3) for k, v in sorted(
            r["weights"].items(), key=lambda kv: -abs(kv[1])) if v}
        lines.append(f"- **{r['label']}** — {kept}")
    lines += ["", "## Per-factor full-sample IC", "", "| factor | mean IC |",
              "|---|---:|"]
    for f, ic in sorted(recs[0]["factor_ic"].items(), key=lambda kv: -abs(kv[1])):
        lines.append(f"| {f} | {ic:+.4f} |")

    out = harness.RESEARCH_DIR / "noise-floor-study.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    for line in lines[6:13]:
        print(line)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
