# Momentum — guided page (2026-08-17)

Rebuild of `/sentiment/momentum` from a supplied design (`Momentum.html`), the
fourth screen from that design project after the
[heat grid](2026-08-17-sector-heat-grid-design.md), the
[rotation board](2026-08-17-sector-rotation-board-design.md) and the
[RRG plot](2026-08-17-rrg-plot-design.md).

Still a Tier-1 reader of `cache:sentiment:momentum`. **No service change** — the
design binds to the payload exactly as it already exists.

## The shape change

The page stops being a dashboard and becomes a numbered argument:

1. **Is momentum worth trading today?** — all three regime states side by side
   with the live one enlarged, then the dispersion reading behind it.
2. **Three levels, and where they agree** — how much of each universe sits in
   its own top quartile, plus the count of stocks with industry *and* sector
   confirming.
3. **Where the names sit** — the four quadrants as counts and chips.
4. **What a score is made of** — one worked example, decomposed into its five
   z-scores.
5. **Rank over recent sessions** — who is actually climbing.

Then the limitations, stated on the page rather than buried in a manual.

**Showing all three regime states at once is the point of section 1.** The old
banner named the live state and left the reader to remember what the other two
would have meant — but the entire premise of this page is that momentum is only
tradeable in some conditions, and that is a comparison, not a label.

## What was dropped, and what was kept

| | Before | After |
|---|---|---|
| Regime | one banner line | three cards, live one enlarged, each with an instruction |
| Dispersion | a sentence in the banner reasons | a percentile, a 0–100 bar and the sentence |
| Levels | implicit in the level selector | explicit √-scaled tracks + top-quartile counts |
| Alignment | a ▮▯▮ glyph column | a headline count of stocks where all three agree |
| Quadrants | a Highcharts scatter | four count panels with the strongest names |
| Score anatomy | 5 numeric columns per row | one worked example with diverging z-score bars |
| Rank history | a 12-series Highcharts ribbon | a 5-series hand-built SVG on a shared date axis |
| **Leaderboard** | top/bottom 15, always open | **kept, behind a collapsed expander** |

The leaderboard was the open question. Deleting it, as the design does, turns a
screener into an orientation page and leaves nowhere to see *which* names to
act on. Keeping it open makes the argument above it look like preamble.
Collapsed is the compromise the page owner chose: the orientation read is the
default, the ranked list is one click away.

## The example row is the top-ranked row

Deliberately deterministic, and it has a nice property: the anatomy card and the
leaderboard's first line are always the same name, so the worked example is
literally "the current leader, explained" rather than an unrelated illustration.
Verified live — the card reads ARKG / Health Care Innovation / Genomics and the
board's row 1 is the same.

## Three things that would have shipped broken

Each found by checking the design's assumptions against the live payload.

**1. `rank_history` is ragged.** Symbols carry 15, 10, 7 or **5** sessions, not
a uniform 15. The design maps index→x as `i/(len-1)*100`, so a five-session
symbol would stretch across the full width and read as a full-length trend.
`rank_chart` puts every series on **one shared date axis**; a short history now
occupies only the right-hand end. Verified live: AWAY has 10 of 15 sessions and
starts at x=35.7%, not 0.

**2. The rank domain is computed, not capped at 21.** The design hard-codes a
1…21 window. Live industry ranks reach 61 and stock ranks reach 272 — and the
single most interesting name on the page is usually the one climbing from deep.
Today that is **GDX 60th→23rd** (industries) and **TER 272nd→52nd** (stocks),
both of which a 21-deep chart would have cut off entirely. A late fix also
appends the bottom tick, because a 1…272 axis whose last label said 205 invites
the reader to take 205 for the floor.

**3. `vector-effect` again.** The design draws `<polyline>` in a 0–100 viewBox
with `preserveAspectRatio="none"`, rescued by
`vector-effect:non-scaling-stroke`. Verified against the shipped DOMPurify:
`polyline`, `points`, `stroke-width` and `stroke-linejoin` are all allow-listed
but **`vector-effect` is stripped**, so the lines would render stretched. Note
`points` cannot take percentages, which is why the chart is percentage-addressed
`<line>`s rather than a polyline.

## Smaller decisions

- **Track width scales with √size.** Linear would make the stock track 27× the
  sector track and squash the smaller two into slivers.
- **Component bars diverge from a centre line** because these are z-scores: the
  sign is the reading, and a left-anchored bar would render −2 and +2 as "small"
  and "large" instead of "opposite". They clamp at ±3, which is where the
  service caps the z-scores anyway.
- **`rank_story` returns "" when nothing has moved.** A manufactured highlight
  on a flat board is worse than no sentence.
- **All four quadrant panels render even when empty** — a quadrant nobody is in
  is information.
- **The limits cards are on the page.** Every one of them is a way this screen
  can be confidently wrong, and they were previously only in the manual.

## Structure

- **`webgui/pages/momentum_view.py`** — all the new arithmetic, pure. 59 tests
  in `webgui/tests/test_momentum_view.py`.
- **`webgui/pages/sentiment_momentum.py`** — widgets, plus the leaderboard's own
  transforms (`leaderboard_rows`, `leaderboard_columns`, `_display_row`, …),
  which survive unchanged because the board does.
- Palette and the warm-neutral ladder imported from `rotation_view.py`.

## Left behind, deliberately

`quadrant_figure`, `ribbon_figure`, `ribbon_subset`, `quadrant_label_bands` and
`_zero_line` are now **dead** — sections 3 and 5 replaced both charts. They are
kept only because their tests still pin them, the same call made for the RRG's
`rrg_scatter_figure`. Worth one cleanup pass across both pages.

## Verified live (2026-08-17)

Against prod's `cache:sentiment:momentum` copied into the dev Redis DB, at 9500:

- **Section 1**: three cards render, `Neutral · now` active with the live 21/63
  lookback interpolated; dispersion **12th** with the service's own sentence.
- **Section 2**: Sectors 3 of 11, Industries 17 of 69, Stocks 74 of 296; tracks
  19.3 / 48.3 / 100.0%; **26** stocks with all three aligned.
- **Section 3**: Leading 9 (13%) · Improving 17 (25%) · Weakening 24 (35%) ·
  Lagging 19 (28%) — sums to 69, bars scale against the fullest.
- **Section 4**: ARKG, score 1.55, pctl 99, Δ+1, Weakening; five component bars
  with Trend/RS clamped at +3.00 and Accel at −1.45 drawn left of centre.
- **Section 5**: 65 line segments, two stroke widths (2.60 highlighted / 1.40),
  labels at their final rank; ticks 1…61 (industries) and 1…272 (stocks).
- **Leaderboard** collapsed by default; opens to 2 tables × 15 rows with every
  component column, row 1 matching the example card.
- Level switch verified via `?level=stock`: heading becomes "296 stocks",
  example row gains "1 of 3 align", story switches to TER.
- No console errors; no page-level horizontal overflow.
