# RRG — hand-drawn plot (2026-08-17)

Rebuild of `/sentiment/rrg` from a supplied design (`RRG.html`), the third screen
from the same design project as the [Sector & Industry heat grid](2026-08-17-sector-heat-grid-design.md)
and the [Sector Rotation board](2026-08-17-sector-rotation-board-design.md).
All three share a design family: Instrument Sans + JetBrains Mono, a near-black
ground, and — for the two rotation screens — one quadrant palette and one
warm-neutral ladder, imported rather than restated.

Still a Tier-1 reader of `cache:sentiment:rotation`. No service change.

## What changed

| | Before | After |
|---|---|---|
| Renderer | Highcharts spline scatter | hand-drawn: markers over an SVG trail layer |
| Marker | fixed 7px radius | **area = the sector's S&P weight** |
| Trail | the whole 12-reading tail | **the last 5 readings**, fading *and* thinning |
| Quadrants | faint corner labels on plain ground | four tinted washes + corner labels |
| Axes | Highcharts axes | computed ticks, crosshair pinned at 50%/50% |
| Header | "RRG" + a headline line | eyebrow + "Where every sector sits" + a tinted verdict strip |

**Why drop Highcharts.** The design is a plain scatter with quadrant washes, a
fixed crosshair and per-marker sizing. Highcharts brings a scale model, a
legend, a tooltip engine and a reflow lifecycle none of that needs — against a
documented list of traps in this app (the stock module vs in-place updates,
charts collapsing to 0×0 inside inactive tab panels, `chart.update()` merging
options across a series-type switch). Eleven markers and forty-four line
segments do not justify any of it, and the arithmetic becomes pure and testable
instead of living inside an options dict.

## The three departures from the supplied design

Each was forced by real data, and each is the sort of thing that would have
shipped looking fine and been wrong.

**1. The domain is computed, not fixed.** The design hard-codes RS-Ratio
98.9…101.1 and RS-Momentum 96.6…103.4. Measured against the live payload, the
five-reading tails reach **RS-Ratio 97.28** — a point-and-a-half outside that
window, clipped clean off the plot with no indication anything was missing.
`domain()` derives the window from every plotted point, pads it 8%, and floors
it at the design's numbers so a becalmed session cannot zoom into noise and
render a flat tape as violent rotation.

**Symmetry about 100 is load-bearing.** The washes and the crosshair are drawn
at exactly 50%/50%. An asymmetric window would put the axes somewhere other than
RS-Ratio 100 / RS-Momentum 100, so a sector could sit in the visual "Leading"
quadrant while every other screen in the group called it Improving. Hence
`x_lo + x_hi == 200` as a test, not a comment.

**2. The trails are real.** The design generates a plausible clockwise spiral
with `sampleTail()` and says so in its own footer note. These are the engine's
own `tail` readings, last five — which is also what was asked for. Confirmed
against the payload that `tail[-1]` **is** the sector's current position, so the
trail ends exactly on the marker rather than near it.

**3. No `vector-effect`.** The design scales a 0–100 viewBox with
`preserveAspectRatio="none"` and relies on `vector-effect:non-scaling-stroke` to
stop the stroke stretching with that non-uniform scale. **That attribute is not
in DOMPurify's allowlist**, so `ui.html` silently strips it and every trail
renders thick horizontally and hairline vertically — the string stays correct
server-side, so nothing in a test suite would have seen it. Percentage
coordinates on the `<line>` elements need neither the viewBox nor the rescue:
they resolve straight against the viewport and leave `stroke-width` in real
pixels. Guarded by `test_tail_svg_emits_nothing_dompurify_would_strip`, the same
invariant `rings.py` carries for the same reason.

## Smaller decisions

- **Marker diameter goes as √weight**, so *area* is proportional to weight. A
  linear diameter would show Technology (32.5%) as ~10× Utilities (2.1%) rather
  than the ~16× in area it actually is.
- **Age is encoded twice** on the trails — width and opacity. Either alone is
  ambiguous against eleven overlapping trails; together they read as direction
  without needing an arrowhead.
- **Labels are decluttered per side.** Eleven sectors cluster hard around 100;
  measured live, four of them sit within 3% of each other vertically. Labels
  flip to the marker's left past 78% of the width, and within each side are
  pushed down to a 15px minimum gap. Per side because a left label cannot
  collide with a right one, and treating them as one column would scatter labels
  that were already readable.
- **A marker halo, not a border.** One box-shadow with two stops — a 2px cut in
  the panel colour then a 1px ring — because the wash behind a marker shares its
  hue and the marker would otherwise dissolve into it.
- **Sectors missing a coordinate are dropped**, not drawn at the origin, which
  would plant a marker on the crosshair and read as a real, perfectly neutral
  sector.

## Structure

- **`webgui/pages/rrg_view.py`** — all of it pure: tails, domain, projection,
  ticks, marker sizing, label decluttering, the trail SVG, the verdict strip. 38
  tests in `webgui/tests/test_rrg_view.py`.
- **`webgui/pages/sentiment_rrg.py`** — widgets only.
- Palette imported from **`rotation_view.py`** (quadrant hues, neutral ladder,
  tone map) so the two rotation tabs cannot drift apart.

## Left behind, deliberately

`sentiment_rotation.rrg_scatter_figure`, `_sector_trace`,
`quadrant_label_bands` and `_hex_to_rgba` are now **dead** — nothing imports
them but their own tests, since both consumers have been rebuilt. Left in place
rather than deleted unilaterally: removing shared builders (and the tests that
pin them) is a call for the repo owner, not a side effect of a redesign. Worth
doing as a follow-up.

One existing test changed: `test_render_uses_native_hover_dimming` asserted
`ui.highchart` appears in the RRG render. That is no longer true by design, so
it was rewritten around its durable half — neither rotation page may reintroduce
the per-hover client→server round-trip the original plotly version used.

## Verified live (2026-08-17)

Against prod's `cache:sentiment:rotation` copied into the dev Redis DB, at 9500:

- Plot 609×600. **44 trail lines** (11 sectors × 4 segments), **11 markers**
  sized 13–21px with Technology largest.
- Percentage coordinates survive the sanitizer and resolve correctly:
  `x1="3.70%"` renders at 23px of 609 (= 3.78%); `stroke`, `stroke-width` and
  `opacity` all present on the emitted lines.
- Four quadrant washes in the four corners with distinct hues; **crosshair at
  exactly 50.00% / 50.00%**.
- Corner labels in the right corners with quadrant colours; Y ticks 97–103,
  X ticks 98–102.
- Verdict strip tinted red for Risk-off; both faces loaded; no page-level
  horizontal overflow.
