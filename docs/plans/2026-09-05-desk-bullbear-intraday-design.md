# Desk Bull / Bear strip — an intraday horizon

**Status:** designed 2026-09-05, approved. Implementation plan to follow.

## The problem

The Desk is a short-term trading screen. Its Bull / Bear sector strip is not:
three of the four signals on every chip are a **quarter-length** read that
cannot change until the nightly cascade runs at 16:20 CT.

| chip element | source | horizon | moves intraday? |
|---|---|---|---|
| quadrant (the fill colour) | `raw.trend` x `raw.excess` | `TREND_WINDOW=90` / `RS_WINDOW=63` bars | **no** |
| left→right order | `bullbear.by_strength` | nightly composite | **no** |
| breadth bar | `participation` | nightly | **no** |
| day % text | live `/quotes` | today | yes, ~30 s |

So the one thing that moves during a session is the smallest text on the chip,
and the colour a trader actually reads at a glance is describing the last three
months. That is the right answer for `/sentiment/bullbear`, whose whole purpose
is an honest structural read; it is the wrong answer for the Desk.

## What makes this cheap

`merge_live` already attaches `day_pct` to every row from **one batched
`/quotes` call** — measured 2026-08-19, all 374 symbols return in a single
request, not one per name. The missing half of an intraday relative axis is the
benchmark, and `bullbear_symbols` derives purely from tree rows, so **SPY is not
in that fan-out**. Adding it is one more symbol on a call that already happens:
no new request, no new schedule, no new cost.

## Decisions taken

1. **Today drives the colour**; the structural quadrant survives as a secondary
   mark. Not a horizon toggle — a control the reader must operate is a control
   they will misread the state of.
2. **Order by today's move**, strongest first.
3. **No usable day data → fall back to the structural quadrant, and say so.**
4. **No deadband on the colour** — strict `> 0`, identical to `quadrant()`.
5. **The structural mark is a left border stripe**, not a glyph or a third line.

## Design

### Data (Tier 2, `services/sentiment_svc`)

* `bullbear_symbols` also yields the benchmark, so `SPY` is quoted alongside the
  tree.
* `merge_live` attaches `day_excess = day_pct - spy_day_pct`, computed **only
  when both sides are real numbers**, else `None`.
* The payload gains top-level `benchmark_day_pct`.

`merge_live` keeps its existing contract exactly: `None` means *the proxy
omitted this symbol*, never *unchanged*. `day_excess` inherits that, which is
why it is `None` — never `0.0` — whenever either side is missing.

### Classification (pure, `webgui/pages/bullbear.py`)

A new `row_day_axes(row)` reads the top-level live fields and feeds the
**existing** classifier:

```
quadrant(*row_day_axes(row)) -> rising_leading | rising_lagging
                              | falling_leading | falling_lagging | unknown
```

Ties go to the cautious side; a missing axis yields `unknown` rather than a
default bucket.

⚠ **Built as `row_day_axes` alone.** A `day_quadrant` wrapper was specified here
and dropped during implementation, deliberately. Its only future justification
would be a deadband on the noisier intraday axis — which decision 4 above
rejects — so the seam would exist for a change already ruled out. And a parity
test over a body of `return quadrant(...)` **cannot fail**: it would read as if
it pinned the invariant while pinning nothing. **One classifier makes rule parity
structural** — the two horizons cannot diverge by rule because there is only one
rule, so a difference between the strip and the map is always a difference in
*horizon*.

`row_day_axes` must NOT fall back to the `raw` block when the live fields are
absent. That fallback would paint the structural reading in today's colours —
precisely the outcome this feature exists to prevent.

### The live / structural switch

**It does not infer "no data" from zeros.** `SchwabProxyClient._extract_change_pct`
falls through to a literal `0.0` when every percent field is missing or zero, so
a naive `day_pct > 0` test would classify all eleven sectors `falling_lagging`
every pre-open, every weekend, and through any proxy hiccup — a confident,
maximally bearish reading of nothing, which is the exact failure class CLAUDE.md
documents five separate times.

Instead the page asks the calendar. `shared.market_calendar` is on the Tier-1
allow-list, so the rule is stated positively:

> Colours go live when there has been a regular session today **and** both axes
> are real numbers. Otherwise the chip paints its structural quadrant and the
> strip labels itself.

⚠ The fallback is **not** a neutral or empty state. Pre-open is exactly when the
strip is being read to plan the session, so it must still say something true.

### Rendering (Tier 1, `webgui/pages/desk.py`)

* Chip **fill and text** = today's quadrant.
* **Thin left border stripe** = the structural quadrant, in the same finite
  palette. No chip height is spent: the chips sit on a `min-w-[124px]` floor in a
  `flex-wrap` row, so the stripe costs only a few horizontal px.
* The hover tooltip spells the structural quadrant out in words, so the stripe
  is never the *only* carrier of that meaning.
* Colours map from the known finite quadrant set to static Tailwind classes, per
  the Tailwind-first standard. No runtime-built colour classes.

### Ordering

Day-ranked, strongest first, **with hysteresis**: a chip changes position only
when its day move crosses its neighbour's by a margin. Without it a strip that
repaints every ~30 s reshuffles on sub-basis-point noise, and a glanceable strip
that moves under the eye is not glanceable.

### Headline

`headline_line` counts **today's** quadrants when live and **names the horizon**
— "4 of 11 rising and leading today" against "… on the quarter". An unlabelled
count that silently changes meaning at the opening bell is worse than either
count alone. It continues to report counts and never a regime verdict:
`payload["regime"]` stays unread, for the reason recorded in `bullbear_chips`.

## The invariant this deliberately breaks

`by_strength` is public with an explicit rationale — *"two screens ordering the
same rows differently is a defect neither shows"* — and the Desk strip will now
order by a different quantity than `/sentiment/bullbear`.

This is accepted, because the two screens are answering different questions: the
strip asks *what is working today*, the map asks *what has been working this
quarter*. Ordering by different quantities is honest **only if the strip says
what it is sorted by**, so the strip carries a small "by today's move" label. A
future reader who finds these two orderings disagreeing should find this
paragraph, not conclude one of them is broken.

## Traps guarded by test

* **The all-bearish trap.** A payload with zero or missing day data must never
  render eleven `falling_lagging` chips; it must fall back to structural. This
  gets its own test driven from a producer-shaped payload, because a
  consumer-side guard proves nothing until a test drives it from the producer.
* **`day_excess` is `None`, never `0.0`**, whenever either side is missing.
* **Rule parity**: `quadrant(*row_day_axes(row))` agrees with `quadrant(pct, excess)`
  over a grid straddling zero on both axes, so the axes reader cannot start
  altering what it feeds the classifier.
* **No structural fallback**: `row_day_axes` on a row with no live fields returns
  `(None, None)`, never the `raw` pair.
* `test_no_inline_style.py` already covers `desk.py`.

## Out of scope

* `/sentiment/bullbear` itself is unchanged. It remains the structural read.
* The industry and stock levels are unchanged; only the sector strip is re-keyed.
* No change to the nightly cascade, its windows, or its scoring.
* The RRG / rotation engines are untouched. See CLAUDE.md on the two
  RS-momentum definitions before assuming any relationship between these screens.
