"""Phase 4 — the decisive test: is this model alpha, or is it beta?

Everything Phase 4 improved, it improved by concentrating weight on ONE effect.
On the expanded panel the risk cluster — `low_vol`, `semivol`, `downside_beta`,
`vol_adj_mom`, `max_effect` — carries **68% of the model's absolute weight**, and
every one of them points the same way: high-volatility names outperformed.

The label is a 20-day forward EXCESS return vs SPY. A high-beta name earns
positive excess return whenever the market RISES, so in a five-year window that
was mostly a bull market, "high volatility outperforms" and "high beta plus a
rising tape" are the same measurement. One is an edge; the other is exposure
that reverses in exactly the drawdown a 1-8 week options position cannot sit
through.

The regime split does NOT settle this. `highvol` there is a VOLATILITY regime,
so a violent rally and a violent selloff both land in it.

So split the sample on the market's own forward return instead and measure the
factor in each half.

⚠ This is a DIAGNOSTIC, not a factor. Conditioning on the forward market return
is look-ahead and could never be traded. It is legitimate here because the
question is not "what should we buy" but "what does this model do when the
market falls" — a property of the model, knowable only by looking.

Run manually with the proxy up (uses the cached panel):
    cd trade-analyzer && ..\\.venv\\Scripts\\python study_beta_check.py
"""
import sys
import pathlib

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from src.analysis import backtest as B          # noqa: E402
from research import harness, variants          # noqa: E402
from research.universe import EXPANDED          # noqa: E402
import fit_swing_model as FSM                   # noqa: E402

HORIZON = 20
RISK_CLUSTER = ["low_vol", "semivol", "downside_beta", "vol_adj_mom", "max_effect"]


def main():
    panel, forward, meta = harness.build_or_load(EXPANDED)
    spy = FSM._close(FSM.fetch_daily("SPY", years=meta["years"]))
    spy_fwd = (spy.shift(-HORIZON) / spy - 1.0).dropna()

    dates = panel.index.get_level_values("date")
    mkt = spy_fwd.reindex(dates.unique())
    row_mkt = mkt.reindex(dates).to_numpy()
    up = row_mkt > 0
    down = row_mkt < 0
    n_up = int(pd.Series(mkt).gt(0).sum())
    n_down = int(pd.Series(mkt).lt(0).sum())

    L = ["# Alpha or beta? — Phase 4 decisive diagnostic", "",
         f"Panel: **{meta['universe_n']}** symbols · {meta['years']}yr · "
         f"{panel.shape[0]:,} rows · fetched {meta['fetched']}", "",
         "Everything this phase improved, it improved by concentrating weight on "
         "one effect: high-volatility names outperformed. The label is a 20-day "
         "forward EXCESS return vs SPY, so in a mostly-rising window that is "
         "indistinguishable from a beta tilt — and beta reverses in exactly the "
         "drawdown a 1-8 week position cannot sit through.", "",
         "⚠ Splitting on the market's FORWARD return is look-ahead and could "
         "never be traded. It is a diagnostic: the question is not what to buy, "
         "but what this model does when the market falls.", "",
         f"Sessions with SPY's forward {HORIZON}-day return **up: {n_up}** · "
         f"**down: {n_down}**.", "",
         "## Per-factor IC, split on the market's forward direction", "",
         "| factor | market UP | market DOWN | sign flips? |",
         "|---|---:|---:|---|"]

    flips = []
    for c in panel.columns:
        ic_u = B.factor_ic(panel[c][up], forward[up])["mean_ic"]
        ic_d = B.factor_ic(panel[c][down], forward[down])["mean_ic"]
        flip = "**YES**" if ic_u * ic_d < 0 else "no"
        if ic_u * ic_d < 0:
            flips.append(c)
        star = " **(risk cluster)**" if c in RISK_CLUSTER else ""
        L.append(f"| {c}{star} | {ic_u:+.4f} | {ic_d:+.4f} | {flip} |")

    L += ["", f"Factors whose sign flips with the market: "
          f"**{', '.join(flips) if flips else 'none'}**.", "",
          "## The composite itself", "",
          "Weights fitted on the whole sample, then the composite's IC measured "
          "separately in up and down markets. A model that only works when the "
          "market rises is a leveraged index position with extra steps.", "",
          "| weighting | IC (market up) | IC (market down) |", "|---|---:|---:|"]

    z = B.zscore_by_date(panel)
    for name, w in (("signed IC (shipping)",
                     B.signed_ic_weights({c: B.factor_ic(panel[c], forward)
                                          for c in panel.columns})),
                    ("orthogonalized residual IC",
                     B.orthogonalized_ic_weights(panel, forward))):
        comp = B.composite(z, w)
        icu = B.factor_ic(comp[up], forward[up])["mean_ic"]
        icd = B.factor_ic(comp[down], forward[down])["mean_ic"]
        L.append(f"| {name} | {icu:+.4f} | {icd:+.4f} |")

    L += ["", "## What a down-market half would weight instead", "",
          "Weights fitted on the down-market rows ALONE (in-sample, and not "
          "tradable — again a diagnostic). Where these disagree with the pooled "
          "weights is where the model is exposed.", ""]
    w_down = B.signed_ic_weights({c: B.factor_ic(panel[c][down], forward[down])
                                  for c in panel.columns})
    w_up = B.signed_ic_weights({c: B.factor_ic(panel[c][up], forward[up])
                                for c in panel.columns})
    L += ["| factor | weight (up) | weight (down) |", "|---|---:|---:|"]
    for c in sorted(panel.columns, key=lambda k: -abs(w_up.get(k, 0.0))):
        L.append(f"| {c} | {w_up.get(c, 0.0):+.3f} | {w_down.get(c, 0.0):+.3f} |")

    out = harness.RESEARCH_DIR / "alpha-or-beta.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    for line in L[10:]:
        print(line)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
