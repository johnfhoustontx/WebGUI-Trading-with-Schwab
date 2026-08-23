"""Phase 4, tasks 4.2 (universe) + 4.5 (short factors) + 4.6 (OOS calibration).

One fetch answers all three, because the cache makes subsetting free: fetch 174
names with the full 14-factor registry, then

  * the 78-name cells are a ROW subset of that panel — same dates, same bars, so
    universe is the only thing that differs;
  * the 10-factor cells are a COLUMN subset — so the factor slate is the only
    thing that differs.

Four cells from one panel, each pair differing in exactly one dimension. Fetching
a second panel for the small universe would have reintroduced precisely the
fetch-date confound the harness exists to remove.

Task 4.6's question — "is the bottom band's edge real?" — cannot be answered from
the shipped artifact, whose bands are calibrated IN-SAMPLE on the same rows the
weights were fitted on. It is answered here on walk-forward composites instead.

Run manually with the proxy up:
    cd trade-analyzer && ..\\.venv\\Scripts\\python study_universe_factors.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from src.analysis import backtest as B          # noqa: E402
from src.analysis import factors as F           # noqa: E402
from research import harness, variants          # noqa: E402
from research.universe import EXPANDED          # noqa: E402
import fit_swing_model as FSM                   # noqa: E402

WF = dict(train=FSM.TRAIN, test=FSM.TEST, step=FSM.STEP)
ORIGINAL_10 = ["mom_12_1", "mom_6_1", "pth", "str_5d", "low_vol", "vol_adj_mom",
               "trend_quality", "rs_spy", "rs_sector", "turnover"]
NEW_4 = ["max_effect", "semivol", "downside_beta", "below_200ema"]


def _fmt_t(d):
    return f"{d['t']:+.2f}" if d["t"] is not None else "n/a"


def _rows_for(panel, symbols):
    lvl = panel.index.get_level_values("symbol")
    return panel[lvl.isin(set(symbols))]


def main():
    panel, forward, meta = harness.build_or_load(EXPANDED)
    got = sorted({s for s in panel.index.get_level_values("symbol").unique()})
    base = [s for s in FSM.UNIVERSE_SECTOR if s in got]
    print(f"panel {panel.shape}; {len(got)} symbols "
          f"({meta['requested_n']} requested); base subset {len(base)}")

    L = ["# Universe + short-factor study — Phase 4.2 / 4.5 / 4.6", "",
         f"Panel: **{len(got)}** symbols of {meta['requested_n']} requested · "
         f"{meta['years']}yr · horizon {meta['horizon']}d · fetched "
         f"{meta['fetched']} · {panel.shape[0]:,} rows · "
         f"{panel.shape[1]} factors", ""]
    if meta.get("missing"):
        L.append(f"No data returned for: {', '.join(meta['missing'])}.")
    L += ["", "Every cell below is a subset of that ONE panel — the 78-name rows "
          "and the 10-factor columns are the same bars, so each comparison "
          "differs in exactly one dimension.", "",
          "## 1. Universe size x factor slate", "",
          "| universe | factors | OOS IC | neg folds | kept |",
          "|---|---|---:|---:|---:|"]

    cells = {}
    for uname, syms in (("78 (shipping)", base), (f"{len(got)} (expanded)", got)):
        for fname, cols in (("10 original", ORIGINAL_10),
                            (f"{len(panel.columns)} (with short slate)",
                             list(panel.columns))):
            sub = _rows_for(panel, syms)
            cols = [c for c in cols if c in sub.columns]
            rec = variants.run_variant(sub[cols], forward.reindex(sub.index),
                                       label=f"{uname} / {fname}", **WF)
            cells[(uname, fname)] = rec
            L.append(f"| {uname} | {fname} | {rec['oos_ic']:+.4f} | "
                     f"{rec['negative_folds']}/{rec['n_folds']} | "
                     f"{rec['kept']}/{rec['n_factors']} |")

    L += ["", "### Paired tests (same folds)", "",
          "| comparison | mean per-fold delta | t |", "|---|---:|---:|"]
    keys = list(cells)
    shipping = cells[keys[0]]
    for k in keys[1:]:
        d = variants.paired_delta(cells[k], shipping)
        L.append(f"| {cells[k]['label']} vs {shipping['label']} | "
                 f"{d['mean']:+.4f} | {_fmt_t(d)} |")

    # ---- 2. do the new factors carry anything? ------------------------------
    L += ["", "## 2. The short-side slate on its own merits", "",
          "Full-sample cross-sectional IC on the expanded universe. A factor "
          "that cannot clear the noise floor here has nothing to add to the "
          "composite, whichever side it was built for.", "",
          "| factor | mean IC | ICIR | in the expanded fit? |",
          "|---|---:|---:|---|"]
    big = _rows_for(panel, got)
    big_fwd = forward.reindex(big.index)
    kept_w = cells[(f"{len(got)} (expanded)",
                    f"{len(panel.columns)} (with short slate)")]["weights"]
    for c in NEW_4 + ORIGINAL_10:
        ic = B.factor_ic(big[c], big_fwd)
        w = kept_w.get(c, 0.0)
        mark = f"yes, w={w:+.3f}" if w else "no"
        L.append(f"| {c}{' **(new)**' if c in NEW_4 else ''} | "
                 f"{ic['mean_ic']:+.4f} | {ic['icir']:+.3f} | {mark} |")

    # ---- 3. long/short split, out of sample ---------------------------------
    L += ["", "## 3. Out-of-sample calibration — is the BOTTOM band real?", "",
          "The shipped artifact calibrates on the full-sample composite, i.e. on "
          "the rows its weights were fitted on, so its printed band statistics "
          "are optimistic by an unknown amount. These bands see only "
          "walk-forward test windows.", ""]
    for label, cols in (("10 original", ORIGINAL_10),
                        ("with short slate", list(panel.columns))):
        cols = [c for c in cols if c in big.columns]
        comp, y = variants.oos_composite(big[cols], big_fwd, **WF)
        bands = B.calibrate(comp, y, n_bands=5)
        L += [f"### {label} ({len(got)} names)", "",
              "| band | score range | mean fwd (OOS) | hit-rate | n |",
              "|---:|---|---:|---:|---:|"]
        for b in bands:
            L.append(f"| {b['band']} | [{b['score_lo']:+.2f}, {b['score_hi']:+.2f}] | "
                     f"{b['mean_fwd']:+.4f} | {b['hit_rate']:.2%} | {b['n']:,} |")
        if bands:
            L += ["", f"Top-minus-bottom spread: "
                  f"**{bands[-1]['mean_fwd'] - bands[0]['mean_fwd']:+.4f}** "
                  f"over {meta['horizon']} days.", ""]

    out = harness.RESEARCH_DIR / "universe-factor-study.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
