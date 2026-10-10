# Contour lines and the gravity well (design, written as built)

**Date:** 2026-10-10
**Scope:** the Dealer Positioning page only (`webgui/pages/options/gamma.py`,
`gamma_heat.py`, a new `gamma_well.py`) and `config/gamma_heat.toml`. Nothing the
options service publishes changes.
**Status:** built on the worktree branch; see the changelog for what has shipped.
**Comes from:** two of the three "further out" mock-ups made on 2026-10-09 beside
the [value, scale and frame design](2026-10-09-gamma-heatmap-value-scale-frame-design.md).
The user picked them on 2026-10-10 after seeing the mock-ups again. The third,
the ridge plot, was asked for later the same day: [its design](2026-10-10-gamma-ridge-plot-design.md).

## What each one is for

**Contour lines.** Colour tells you how much positioning sits at a strike. It is
poor at telling you where that amount *changes*: two bands that differ by a
factor of two can look alike. Lines of equal value mark the edges. Lines packed
together mean the exposure steps sharply across a few strikes; a lone outer line
shows how far a band reaches.

**The gravity well.** The by-strike bars redrawn as ground, to answer one
question at a glance: is price sitting somewhere that holds it (positive gamma,
where dealer hedging damps moves) or somewhere that lets go of it (negative
gamma, where hedging amplifies them)?

## Rules both follow

1. **Off by default, and a switch.** Each is a switch the reader turns on. Neither
   hides, replaces or overrides an existing control. This is the lesson of the
   From spot frame, which first hid the Level movement switch and was reversed
   the day it shipped.
2. **No new data.** Contours are drawn from the heat map's grid exactly as it is
   drawn. The well is drawn from the bars' own net values in the bars' own
   window. Neither can disagree with the panel it sits beside.
3. **No claim the data cannot support.** See "What the well is not".

## Contour lines

- **Where the levels sit.** The top of the colour scale and halvings below it:
  the full scale, a half and a quarter as shipped (`[contours] steps = 3`), above
  zero and mirrored below it. Tying the levels to the colour scale means a line
  is the same share of the scale in every Value, Show, Scale and Frame, and in
  Locked it means a fixed dollar amount all session.
- **How they are traced.** Marching squares over the cells between neighbouring
  strikes and neighbouring minutes, in `gamma_heat.contours`, pure Python (the web
  tier may not import numpy). A cell with a gap in any corner is skipped, so no
  line is interpolated through a value that was not measured; the From spot frame
  has such gaps.
- **How they are drawn.** Two more series on the heat map's chart, one per sign,
  each all of that sign's lines with a break between them. The chart updates in
  place and its series count must not change, so both always exist and are empty
  when the switch is off. The count goes from nine to eleven. They are `scatter`
  series with a line width, not `line`: a contour doubles back in time, and a
  line series needs its x values in order.
- **Rebuilt, never merged.** Found by the user the day it shipped: on a page
  loaded with the switch on, the lines were joined by long straight strokes
  until the switch was turned off and on. Updated in place, Highcharts matches
  a series' old and new points by x and appends the rest, and a contour is
  drawn in the order of its points. Each contour series now carries a stamp of
  its points in `pointStart`, and a changed `pointStart` makes Highcharts
  rebuild the series. My check before shipping only turned the switch on from
  off, which is the one case that was never wrong.
- **Kept small.** Points on a straight run are dropped (within 3% of a strike
  step, so a line is never visibly bent), a line spanning fewer than
  `min_columns` minutes (3) is left out as a speck, and past `max_points` (6,000 a
  sign) the shortest lines go first.
- **Cost, measured** on the stored `$SPX` session of 2026-10-09 (381 minutes):
  about 27 ms more to build the figure and 35 KB more to send, per repaint, with
  the switch on. Nothing with it off.

### Not done, and why

- **No zero contour.** The mock-up drew one and called it the gamma flip. That
  was wrong: the flip is where *total* gamma, as a function of price, changes
  sign. The zero contour of the per-strike grid is only where one strike's net
  changes sign, and far from price, where every cell is near zero, it wanders
  with the noise. The page already draws the real flip.
- **No labels on the lines.** The colour under a line says which level it is.

## The gravity well

- **The ground.** Height at a strike is minus its net gamma, so positive gamma is
  a valley and negative gamma a hill. One `area` series split by a zone at zero
  and shaded between the ground and the zero line only, so a colour always means
  that sign of gamma. (Shaded to the bottom of the plot, as first built, the
  valley colour ran under every hill.)
- **The ball** is price, on the straight line between the two strikes around it.
  The series is a plain `area`, not a spline, for that reason: on a curve the
  ball would float off the ground.
- **Downhill.** From price toward the lower of the two strikes around it, then on
  to the first strike that is lower than both its neighbours. That strike is the
  low point, marked with a diamond; the ball's label is an arrow and its price.
- **One sentence underneath**: which kind of ground price is on, where the low
  point is and how far, and, when it applies, that the low point is itself still
  negative gamma (between two hills it is).
- **Height is the signed square root of net gamma by default.** Measured on
  2026-10-09: at the close one strike held about $50B and drawn in proportion it
  was a single spike on flat ground; at 09:30, 11:30 and 13:30 the proportional
  version was readable but showed none of the negative-gamma ground at the lower
  strikes. The square root keeps every strike's side of zero and the order of
  the strikes. It does not keep depths in proportion, the title says so, and each
  strike's tooltip gives the real figure. `[well] height = "linear"` restores
  proportion.
- **The caption reads the net values, never the drawn height,** so "positive
  gamma" is the same on either scale.
- **Gamma view only.** Valleys and hills are a statement about gamma. The switch
  itself hides on the other views.
- **Ground with no shape says so.** Found on prod the Saturday it shipped: an
  index's net gamma reads zero at every strike outside market hours (its open
  interest is published as zero), and the caption named "the low point" on a
  flat line. Now every-net-zero draws no ball and says there is no ground to
  draw, and level ground (no downhill, and no higher ground beside price either)
  is told apart from a low point.

### What the well is not

It is not a forecast, and the page says so in the caption, the tooltip, the page
guide and the manuals. The ball does not move, nothing is simulated, and the
arrow is the slope of today's positioning, not a prediction of the next print. It
knows nothing about order flow, time or volatility. The mock-up's wording, "the
direction dealers' hedging pushes", claimed more than a gamma profile can say and
was not carried over.

## Where the code is

```
webgui/pages/options/gamma_heat.py   contour_levels, contours            (pure)
webgui/pages/options/gamma_well.py   terrain, read, arrow, caption       (pure, new)
webgui/pages/options/gamma.py        heatmap_figure(contours=)
                                     HeatControls: the Contours switch
                                     well_points, well_figure, WellPanel
                                     view_data (moved out of render)
shared/gamma_heat_config.py          contours(), well_height()
config/gamma_heat.toml               [contours], [well]
```

`gamma.render` gained three lines for the well and lost ten to `view_data`, so it
is smaller than before and its ceiling is lowered.

## Verified

- Both pure modules' suites, with the contour and well figures pinned in the
  page's suite.
- In the local page harness (there is no dev environment): both switches appear
  off, the well's panel is hidden until its switch is on and then mounts at full
  width with its caption, the contour series fill when theirs is on, and both
  choices are restored after a restart. The pane stopped drawing part-way, so
  the last check was read from the DOM.
- On real data: the figures were built from the published `$SPX` snapshot of
  2026-10-09 with the page's own builders and rendered outside the app.

Not verified: either feature on the running app with live data, and any symbol
other than `$SPX`.

## Decisions that were mine

None of these was asked for; each is a setting or a small change if wrong.

1. Three contour levels, halving from the top of the colour scale.
2. The square-root height as the well's default.
3. The well as a panel under both charts, at 250 pixels.
4. No zero contour.
