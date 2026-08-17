# Sector & Industry — heat-tile redesign (2026-08-17)

Rebuild of `/sentiment/sectors` from a supplied design set (a README of design
decisions plus two screenshots, collapsed and expanded). The prior screen was a
row of plain signed percentages with a green/red *text* colour; the new one makes
the **size** of each move the primary encoding.

## What changed, and why each

| | Before | After |
|---|---|---|
| Day / Week / Month | signed text, green/red by sign | three adjacent **filled tiles**, flush right, figure inside the tile |
| Colour meaning | direction only | direction **and** magnitude |
| Scale | none (a global ±0.05% "flat" cut) | **per column**, over sectors + all industries |
| Flat | |pct| < 0.05% | per-horizon band: ±0.50 / ±1.00 / ±1.50% |
| Row | one line, ~24px | 66px with a **rank line** under the name |
| Sorting | fixed Day desc | Day / Week / Month headers sort, click to reverse |
| P/C | green/red like a return | plain number, amber above 1.5 |
| RRG quadrant | a column | **dropped** |
| Industries | same row height | 34px, same tiles, hairline indent rule |

**Why tiles rather than coloured text.** A column of signed numbers makes the
reader do the ranking. Filled adjacent tiles form one continuous band across a
row and down the page, so the shape of the day is visible before any number is
read — which is what this screen is actually for.

**Why per-column normalisation.** Day, Week and Month live on different natural
scales. Sharing one ramp means either the Day column is always pale or the Month
column is always saturated. Each column normalising against its own spread lets
all three carry information at once.

**Why the flat bands widen with the horizon.** A month is *expected* to have
travelled further than a day. A single band paints an unremarkable month as a
move.

**Why P/C keeps a plain number.** It is a ratio, not a percentage change. Giving
it the same fill as the three timeframe columns would invite reading it as a
fourth one. The amber tint above 1.5 marks put-heavy without implying a scale.

**Why RRG went.** `/sentiment/rrg` and `/sentiment/rotation` show the relative-
rotation read properly (a trail through four quadrants, and a ranked map). A
single quadrant word sitting beside a colour band had the same misreading problem
as a heat-tiled P/C, and the data is one tab away.

## The one substantive departure from the supplied design

**The reference normalises each column on its maximum. This ships p90.**

The supplied README is explicit that industry-level rows are *synthetic
placeholders*, and they cluster close to their sectors. Real industry ETFs do
not: measured live over the full 81-row set (2026-08-17), the Month column's
maximum was **+27.46%** against a **3.24%** median. Normalising on that maximum
put all eleven sectors into four of the thirteen steps — every sector rendered
the same near-flat green. That is a direct loss of the property the design names
as its own goal, "a column always uses its full range".

Measured alternatives across the same live set, by how many of the 13 steps each
column actually spends:

| quantile | Day | Week | Month | verdict |
|---|---|---|---|---|
| 1.00 (max, as specified) | 13 | 7 | 8 | Month/Week collapse |
| 0.90 | 13 | 13 | 9 | **shipped** |
| 0.85 | 12 | 12 | 9 | Day's bottom saturates early (three −6s) |
| 0.80 | 12 | 12 | 10 | worse still at the tails |

p90 is the highest quantile that keeps every column spending its full range.
Readings above it saturate at full intensity — `heat_level` already clamps, so
nothing overflows, and an outlier still reads as "the biggest move on screen".

## Structure

- **`webgui/pages/sector_heat.py`** — all of it pure: the oklch→sRGB conversion,
  the 13-class ramp, the column scales, the level map, sorting, ranking, the two
  header strings. 45 tests in `webgui/tests/test_sector_heat.py`.
- **`webgui/pages/sentiment_sectors.py`** — widgets and wiring only. Still a
  Tier-1 reader of `cache:sentiment:sectors`; still repaints on the 2 s version
  poll; still enqueues `cmd:sentiment` on Refresh.
- **`config/theme.toml [sectors]`** — the chrome palette and the two font faces.
- **`webgui/pages/options/theme.py`** — `build_sector_tokens` /
  `build_sector_font_head_html`, following the `[console]` / `[macro]` pattern.

**The heat ramp is deliberately not in `theme.toml`.** It is a data-driven cell
map — the category CLAUDE.md already excludes from the config-driven palette,
alongside the gauge face and the score/heat/P&L zone maps. What a restyle would
actually reach for (ground, greys, amber, fonts) *is* config.

**The ramp is stepped, not continuous.** The Tailwind-first standard requires a
data-driven colour to map onto a fixed finite palette of static classes rather
than a per-datum arbitrary value. Six steps per direction plus neutral is
indistinguishable from a continuous ramp at tile size and keeps the vocabulary
deduped.

**No `ui.add_css` block.** This is the strongest case in the app for the
Tailwind-first standard holding — a screen that is nothing but colour and
measurement — and it needs no escape hatch: fractional column tracks, flush
tiles, truncation and the scroll wrapper are all utilities. The only injected
head content is the font `<link>`.

## Verified live (2026-08-17)

Against prod's `cache:sentiment:sectors` copied into the dev Redis DB, rendered
at 9500:

- 11 sector rows at 66px, 70 industry rows at 34px on Expand all; no console errors.
- Grid template resolves `331px 58px 442px 70px 112px 112px 112px`; the three
  tiles sit exactly 112px apart, so the band is continuous.
- Day band spans full green `rgb(0,35,17)` → neutral `rgb(13,12,10)` → full red
  `rgb(89,9,18)` across the eleven sectors.
- Sorting: clicking MONTH re-sorts and moves the `↓` marker; clicking again gives
  `↑` and worst-month-first; the rank line follows (`RANK 1 OF 11 · MONTH`).
- P/C tints amber at 2.38 / 2.25 / 1.81 / 1.65 and stays grey below 1.5.
- Instrument Sans and JetBrains Mono both load and apply.
- At a 900px viewport the grid scrolls inside its own wrapper (916 > 753) and the
  page body does **not** overflow horizontally.

## Known limits

- The eyebrow stamp is the cache's `sector_at`, so off-hours it reads the last
  refresh rather than "now" — correct, and the page says `AWAITING DATA` rather
  than inventing a time on a cold cache.
- `SCALE_QUANTILE` is tuned against one live session. If a genuinely violent day
  makes the sector rows saturate, that is the knob.
