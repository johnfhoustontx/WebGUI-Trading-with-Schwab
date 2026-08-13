# Momentum Cascade — regime-conditioned momentum across sector / industry / stock

**Status:** design · 2026-07-28
**Companion plan:** [2026-07-28-momentum-cascade-plan.md](2026-07-28-momentum-cascade-plan.md)

## Problem

The Sentiment page sees two levels: SPY and the 11 sector ETFs. Rotation and RRG
answer "what is leading", but neither answers "is the leadership real" (how many
constituents confirm it) or "does the current regime pay for chasing it".

`Sectors_Industries_ETFs.xlsx` gained a **Stocks** tab (2026-07-28): 5 representative
stocks for each of the 74 unique Sector / Industry pairs, 370 rows, columns
`Sector | Industry / Sub-Industry | Rank | Symbol | Company | Reference ETF(s)`.
That gives a third level and makes a participation-confirmed momentum view possible.

## Scope

**In:** a nightly momentum score at three levels (11 sectors, 74 industry ETFs, 311
unique stocks), a regime gate that says whether momentum is tradeable right now, a
SQLite store, an additive bus contract, and a webgui page.

**Out — explicitly:** this does **not** enter the sentiment composite.
`scoring/__init__.py:WEIGHTS` is untouched, no component is added, the bridge
`component_scores` block does not change. Momentum is *context*, published on its own
cache key, consumed by the page and (later, if it earns it) the driver. Anyone who
proposes adding it to WEIGHTS is starting a different, coordinated change.

## Architecture

```
Sectors_Industries_ETFs.xlsx  (Stocks tab)
        │  sectors_ref.load_stocks_data()   ← mtime-cached, mirrors load_sectors_data
        ▼
scoring/momentum.py            PURE. trend/RS/acceleration/path/participation,
                               z-score, blend, percentile. No I/O, no tk.
scoring/momentum_regime.py     PURE. dispersion + crash-risk → favorable /
                               neutral / suppressed, and the lookback weights.
        │
        ▼
services/sentiment_svc/
  momentum_db.py               SQLite. daily_bars + momentum_scores.
  compute.compute_momentum()   proxy → bars → delta-fetch → pure scoring → payload
  scheduler                    ONE nightly slot (~16:20 CT weekdays) + on-demand
  handlers                     writes cache:sentiment:momentum, publishes change
        │
        ▼
webgui/pages/sentiment_momentum.py   quadrant scatter · rank ribbon · leaderboard
```

## The score

Five components, computed identically at all three levels so the numbers are
comparable down the cascade. All inputs are daily closes.

| Component | Weight | Definition |
|---|---|---|
| Trend strength | 30% | Clenow: OLS of `log(close)` on bar index over 90d → annualized `(exp(slope)**252 - 1)`, multiplied by R². The R² term is the point — it discards gappy, news-driven moves that top a raw return screen and then fail. |
| Relative strength | 25% | 63d return minus SPY's 63d return, plus the OLS slope of the normalized RS line over the same window. Absolute return alone just re-ranks beta. |
| Acceleration | 20% | `r21 - (r63 / 3)`. Separates *starting* from *finished*. The single most useful column on the page. |
| Path quality | 15% | % up days over 63d, blended with `return / annualized realized vol`. Smooth advances persist; jagged ones revert. |
| Participation | 10% | Fraction of the industry's 5 constituents above their own 50 DMA. Sector level uses the union of its industries' constituents. |

Each component is z-scored **within its level** (a stock is ranked against stocks, an
industry against industries), clipped to ±3, then weighted and summed. Display value
is the 0–100 percentile rank within level; the raw z-blend is retained in the payload
so the ordering is stable and re-rankable.

Participation is undefined at stock level — renormalize the remaining four weights to
1.0 rather than substituting a neutral value. A synthetic 0 would bias every stock
downward against its own peer group.

**Liquidity filter:** drop any symbol whose 20d average dollar volume is under $5M
before z-scoring. Several small caps on the Stocks tab (`USAR`, `IPX`, `TPIC`,
`AMSC`, and the five OTC cannabis MSOs) will not support a real position and would
otherwise pollute the top of the leaderboard on a thin-volume pop.

## Regime conditioning

Momentum is not one strategy. Both the profitable lookback and the sign change with
conditions, so the score is gated, not just displayed.

`momentum_regime.classify()` returns one of three states plus the lookback weights
that state implies:

**`favorable`** — SPY above its 200 DMA, VIX term structure in contango, dispersion
percentile above 40. Momentum's home turf: emphasize the 63–126d lookbacks, expect
rank persistence week to week.

**`neutral`** — chop. SPY oscillating around the 50 DMA, or dispersion percentile
below 40. Momentum decays and whipsaws here; shorten to 21d emphasis and tighten the
participation floor. Low cross-sectional dispersion is the specific tell — when
correlations converge on 1 there is nothing for a relative-strength screen to pick up.

**`suppressed`** — momentum-crash risk. SPY below its 200 DMA **and** 21d return
positive **and** realized-vol percentile above 80: a rebound off a low, where the
biggest losers rip hardest and a long-winners book gets run over. The page renders an
explicit banner in this state rather than silently serving a leaderboard nobody
should trade.

Inputs come from what the service already computes — SPY closes are on the existing
12-month fetch, VIX term from `scoring/vix.py`, and the market-state block from the
five-state classifier. Dispersion is new: cross-sectional stdev of 5d returns across
all scored constituents, ranked against its own 252d history.

**The current regime, and which lookback is weighted because of it, belongs in a
banner across the top of the page.** It is what makes the rest of the screen
interpretable at a glance.

## Alignment cascade

The highest-conviction rows are where all three levels agree. Each stock row carries
a three-block indicator — sector top-quartile, industry top-quartile, stock
top-quartile — so the table sorts for triple alignment. A stock in the 95th
percentile inside a bottom-decile industry is a single-name story: a fine trade, but
a different one, and the difference should be visible without arithmetic.

The inverse is the early-warning screen: industries whose *median constituent*
momentum is rising faster than the ETF's own score, which tends to lead the ETF
breaking out.

## Contract

Additive only, on its own key. Nothing in the existing sentiment contract changes.

```
cache:sentiment:momentum
{
  "schema": 1,
  "computed_at": "2026-07-28T16:22:04-05:00",
  "session_date": "2026-07-28",
  "regime": {"state": "favorable", "lookback": "63/126",
             "dispersion_pct": 0.62, "crash_risk": false, "reasons": [...]},
  "levels": {
    "sector":   [{"symbol","label","score","percentile","components":{...},
                  "participation","rank","rank_prev"} ...],
    "industry": [... same shape, plus "sector" ...],
    "stock":    [... same shape, plus "sector","industry","alignment":[bool,bool,bool]]
  },
  "excluded": [{"symbol","reason":"liquidity"|"insufficient_bars"|"no_quote"}]
}
```

`shared/contracts/sentiment.py` gains a `MomentumSnapshot` dataclass. Fields are
additive across minor versions, same rule as the bridge.

## Data & cost

385 symbols (311 stocks + 74 industry ETFs, sectors reuse existing fetches), 252 daily
bars each. First run is a full backfill; every run after that fetches only the bars
newer than the stored max date — one trading day for a symbol already in the table.
Roughly 20 MB of SQLite, and the nightly delta is small enough to run through
`services/_parallel.py` against the proxy without a rate-limit problem.

**The scheduler slot is nightly, not on the 120 s tick.** Daily bars change once a
day; recomputing 385 regressions every two minutes would be pure waste and would put
the proxy under load during RTH for no signal. Ranks are recomputed nightly but the
*ribbon* reads from stored history, so rank churn is free to display.

Everything the GUI needs lands in `momentum_scores`; the page never calls the proxy.

## Open questions for implementation

- Weekly rather than daily rank refresh for the leaderboard. Daily reranking mostly
  generates turnover noise; the stored history supports either, so start daily and
  measure rank churn before deciding.
- Whether the dispersion series is worth publishing separately — it is a decent
  regime indicator on its own and would plot naturally against VIX on the Sentiment
  page.
- Symbol drift. The Stocks tab is static reference data; corporate actions are not
  tracked. `excluded` with `reason: "no_quote"` is the detection mechanism — surface
  it in the page footer so a delisted or renamed ticker is visible rather than
  silently dropped.
