# The ridge plot (design, written as built)

**Date:** 2026-10-10
**Scope:** the Dealer Positioning page only (`webgui/pages/options/gamma.py`, a new
`gamma_ridge.py`) and `config/gamma_heat.toml`. Nothing the options service
publishes changes.
**Status:** built on the worktree branch; see the changelog for what has shipped.
**Comes from:** the third of the "further out" mock-ups made on 2026-10-09 beside
the [value, scale and frame design](2026-10-09-gamma-heatmap-value-scale-frame-design.md).
The other two are in [contour lines and the gravity well](2026-10-10-gamma-contours-and-well-design.md).

## What it is for

The by-strike bars show the profile now. The heat map shows every minute, but as
colour, and colour is poor at showing how much a strike has *grown*. The ridge
plot is the bar panel laid out in time: one profile every half hour, stacked front
to back, the earliest at the top and now in front. A ridge that gets taller down
the stack is positioning building at that strike. The white dot on each row is
the price at that time, so the dots read top to bottom as the path price took
across the profile.

## Rules it follows

The same three as the contours and the well.

1. **Off by default, and a switch.** It hides, replaces and overrides nothing.
2. **No new data.** The ridges are the heat map's own history rows through the
   heat map's own `heatmap_matrix`, over the bars' own strike window.
3. **No claim the data cannot support.** It is a record of the session so far.
   Nothing is projected.

## How a ridge is drawn

- **Which readings.** The first reading of each clock half hour (`[ridge]
  every_min = 30`), and always the latest reading, so the front ridge is now. A
  half hour with no reading on its mark takes the next one in it.
- **Height is the size of the value; colour is its sign.** A strike with negative
  net gamma is a hill too, in the negative colour. Drawn downward instead, a
  negative strike would dig into the ridge in front of it and the stack would stop
  reading as a stack. The colour changes where the value crosses zero, on the
  straight line between two strikes.
- **One scale for every ridge.** A ridge growing is the profile growing, never the
  scale moving.
- **The typical ridge sets that scale, not the tallest.** The median of the ridges'
  peaks spans `[ridge] overlap` rows (2). First built to the tallest, and on the
  stored `$SPX` session of 2026-10-09 the 15:00 reading, an expiry close pinned on
  7,810, was 4.5 times the median peak on the square-root scale: every other ridge
  was under a row high and the net view read as flat lines. On the median the
  day reads, and the pin is a tower in front, which is what happened.
- **A limit on the chart, not on the data.** The top of the chart is at most two
  typical peaks above the top row (`RIDGE_HEADROOM`). A peak taller than that runs
  off the top. Without the limit one outlier in proportion (twenty times the rest
  on the linear scale that day) would squeeze every row into the bottom of the
  panel. The tooltip gives the real figure.
- **Square-root height by default**, as the well's and for the same reason, with
  `[ridge] height = "linear"` to draw in proportion. The title says which.
- **The strikes are the bars' window** at the current price, so the ridges and the
  bars never show different strikes. The published history is cropped around
  price as it was, so on a day price travelled an early ridge can stop short at
  one edge. Nothing is filled in.
- **It lines up with the gravity well.** Asked for by the user on seeing the two
  together: the well's axis ran 7,720 to 7,910 from the left edge and the ridge
  plot's 7,720 to 7,900 from 56 px in, so a price sat at a different place in
  each. Both now take `_strike_axis`: the same two margins and an axis that is
  the bars' window exactly. Checked in the browser on the 2026-10-09 `$SPX`
  session: 7,750, 7,800, 7,850 and 7,900 at the same pixel in both.
- **It follows the Value picker** (Net, Calls, Puts, Size). A session stored
  before cells carried a call and a put is drawn in net, and the title then says
  net. It does not follow Show, Scale or Frame: those are about the heat map's
  colour and axis.
- **The four Greek views.** Unlike the well it is not a statement about gamma, so
  it is offered on Gamma, Charm, Delta and Vanna. The switch hides on the others.

## How it is built

- `gamma_ridge.py` is pure: `slots`, `pick`, `ridges`. It imports `math` only.
- `ridge_figure` is one `arearange` series a ridge and one scatter series of price
  dots. The chart updates in place, so it ALWAYS holds `slots(every_min)` ridge
  series (15 at half an hour), empty until their time comes. `arearange` needs
  `highcharts-more`, which NiceGUI's element loads for every chart.
- `RidgePanel` is the switch and the panel. `UnderCharts` holds it and the
  `WellPanel`, so `gamma.render` still has three lines for the panels under the
  charts and did not grow.
- The time of each ridge is a label on its baseline, in the left margin. The
  value axis carries no numbers.

## Cost, measured

On that session (381 minutes, 38 strikes in the window): 10 ms to build in net
and 30 ms in size, and 47 KB to send, per repaint, with the switch on. Nothing
with it off.

## Choices that were mine

Not asked, so each is easy to change: half-hour spacing; the median as the scale
and two rows for it; square-root height; negative strikes drawn upward in their
colour; the panel at full width under the charts, 440 px tall, below the well
when both are on; offering it on all four Greek views.

## Verified

- `test_gamma_ridge.py` (the arithmetic), and the figure, the panel and the three
  render lines in `test_options_gamma.py`.
- In the local page harness (there is no dev environment): the switch is off by
  default; on, the panel mounts at full width with 16 series; the Charm view draws
  its own ridges; the Flow view hides the panel and the switch; choosing Puts
  redraws every ridge in puts and the title says so.
- Built from the real published `$SPX` snapshot of 2026-10-09 with the page's own
  builders, in all four values.
- Not seen on the running app.

## Where the code is

```
webgui/pages/options/gamma_ridge.py  slots, pick, ridges                 (pure, new)
webgui/pages/options/gamma.py        ridge_model, ridge_figure, RidgePanel
                                     UnderCharts (the well and the ridge plot)
shared/gamma_heat_config.py          ridge()
config/gamma_heat.toml               [ridge]
```
