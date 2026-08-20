# Bull / Bear Map — where the market is strong and weak (2026-08-19)

A new tab at `/sentiment/bullbear` answering one question directly: **where is the
market bullish, and where is it bearish** — across sectors, industries and the
stocks inside them. Plus an 11-chip sector strip on the Desk that clicks through
to it.

The organising claim is that "bullish" is **two facts, not one**, and that
collapsing them is what makes existing rotation screens misleading in a falling
tape.

## Why this is mostly a rendering job

Checked against the live payloads before designing, because the expensive version
of this feature — computing a three-level cross-section — already exists and runs
nightly.

`cache:sentiment:momentum` (the 16:20 CT cascade) publishes `levels.sector`,
`levels.industry` and `levels.stock`. Measured on the 2026-08-19 payload:

| level | rows | carries |
|---|---|---|
| `sector` | 11 | `symbol`, `label`, `score`, `percentile`, `rank`, `rank_prev`, `participation`, `components`, `raw` |
| `industry` | 69 | the above **plus `sector`** (its parent) |
| `stock` | 296 | the above **plus `sector` and `industry`**, and `alignment` |

So the hierarchy, the parent links and the scores are all already there. Two
fields inside `raw` are the whole design:

- **`raw.trend`** — `momentum.trend_strength`: the annualised exponential
  regression slope of `log(close)` scaled by R². **Signed and absolute.** Positive
  means price is genuinely rising, independent of any benchmark.
- **`raw.excess`** — `momentum.relative_strength`: excess return vs `SPY`.
  **Signed and relative.**

Both exist at all three levels today. **The two-axis design therefore costs no new
computation and no new history fetches.** What is missing is a view that reads them
as a map.

## The idea: two axes, never blended

`/sentiment/momentum` and `/sentiment/rrg` both rank on *relative* strength. The
Reference Guide already warns why that is only half an answer:

> *relative* to the universe; in a falling market the top-ranked stock still falls.

So every row shows **three independent marks**, and no combined score:

| mark | source | answers |
|---|---|---|
| **Trend** | `raw.trend` sign + magnitude | Is price actually rising? |
| **vs SPY** | `raw.excess` sign + magnitude | Is it beating the index? |
| **Today** | live quote, `netPercentChange` | Is today confirming or contradicting? |

Trend × vs-SPY gives four quadrants:

| quadrant | reading |
|---|---|
| **Rising & Leading** | unambiguous strength — the real bullish bucket |
| **Rising & Lagging** | going up, but the index is going up faster |
| **Falling & Leading** | **the trap.** Down, but down less. A relative-only screen calls this bullish |
| **Falling & Lagging** | unambiguous weakness — the real bearish bucket |

Measured on the 2026-08-19 payload, the trap bucket is not hypothetical:

```
sector     n= 11   rising+leading   5   rising+lagging  3   falling+leading  0   falling+lagging   3
industry   n= 69   rising+leading  34   rising+lagging  6   falling+leading  1   falling+lagging  28
stock      n=296   rising+leading 128   rising+lagging 44   falling+leading 19   falling+lagging 105
```

**19 stocks and 1 industry currently sit in the bucket a relative-only view would
paint bullish.** Note also that the market-wide picture differs by level: the
sector row is 5-of-11 constructive, while the stock row is 105 names in outright
decline — a split that a single blended score would average away.

## Participation is the third dimension, and it is load-bearing

`participation` (present on sector and industry rows; `None` on stocks) is the
share of constituents confirming the move. It separates two rows that look
identical on trend alone. From the same live payload:

| sector | trend | excess | participation |
|---|---|---|---|
| Energy | **+0.004** (flat) | +0.0122 (leading) | **0.96** |
| Utilities | −0.017 | −0.0372 | **0.16** |
| Real Estate | +0.044 (rising) | −0.0189 | **0.23** |

Real Estate is rising on less than a quarter of its constituents. That is a
fragile advance, and a map that hides it is worse than no map. Participation
renders as a breadth bar on every sector and industry row.

## The deliberate omission: no regime headline

CLAUDE.md documents an open defect — `/sentiment/sectors` and `/sentiment/rotation`
print **opposite** risk-on/risk-off verdicts from quantities that are not
commensurable (a daily return spread vs an RS-momentum spread, on different
scales). Measured 2026-08-17: `+0.37` rendered "Risk-on" on one tab while `−1.52`
rendered "Risk-off" on its neighbour.

**This page will not add a third verdict.** Its headline is the **quadrant counts**
— "5 of 11 sectors rising and leading" — which is an arithmetic fact about rows on
screen, not a regime label competing with two others. A reader can disagree with the
interpretation; they cannot disagree with the count.

This is the same discipline the Market Regime direction axis uses: name a direction
only when independent reads agree, and otherwise report what is actually measured.

## Freshness: fully live, at one API call

All three levels carry a live day-move. This sounded like the expensive option and
is not: **`/quotes` batches, and all 374 distinct symbols return in a single call**
— verified against the running proxy at 100, 200 and 374 symbols.

At a ~30 s RTH cadence that is roughly **780 calls/day** against a current baseline
of ~68–76k/day: about 1%. Off-hours throttles like every other poller.

The **scores** stay nightly regardless — momentum needs months of history, so there
is no such thing as an intraday `raw.trend`. The live layer answers a narrower and
honest question: *is today confirming last night's map?*

## Tier ownership

`sentiment_svc` owns it and publishes **one** merged view,
`cache:sentiment:bullbear`.

The alternative — the page reading `cache:sentiment:momentum` and a live quote view
and joining them — was rejected: a 374-row join is Tier-2 work, and Tier-1 reading
two views that tick at different cadences would let the tree and the day-moves
disagree mid-repaint.

`sentiment_svc` rather than `market_svc` because the tree, the scores and
`sectors_ref` are already its data; market_svc would have to import the momentum
payload to know which symbols to poll.

## Rendering

- **No Highcharts.** It is a tree of rows. This also sidesteps the documented
  mount-hidden collapse trap, which a tab-mounted chart is exactly prone to.
- **Lazy stock rows.** Default screen is 11 sector rows. Industries render on
  sector expand, stocks on industry expand. 376 rows are never all in the DOM.
- **Tailwind-first**, per the house standard; quadrant → a fixed finite palette
  class via a pure lookup, never a runtime-built colour.
- Pure functions (`classify`, `quadrant_counts`, `rollup`, ordering, breadth-bar
  geometry) live in their own module and are unit-tested without a browser; the
  page module is widgets and wiring only — the pattern the four 2026-08-17
  sentiment rebuilds established.

## The Desk strip

Eleven sector chips: label, quadrant colour, live day-move, participation. It reads
the same `cache:sentiment:bullbear` view the tab does, so the two cannot drift, and
it adds one key to the Desk's existing batched `read_versions` rather than a new
poll. Click → the tab.

## Out of scope

- **No new scoring.** The cascade's weights, windows and z-basis are untouched.
- **No trade recommendations.** The map says where strength is; it does not say
  what to sell. The Scanner and Strategy Finder own that.
- **No history.** Quadrant membership over time is a different (and much larger)
  feature; `rank_history` already exists on the momentum payload for whoever wants
  it next.
- **Retiring the overlap** with `/sentiment/sectors` is explicitly *not* attempted
  here. Two screens will briefly answer adjacent questions; consolidating them is a
  product decision to take after this one is in use.
