# Real smoothing for the heat map (design, written as built)

**Date:** 2026-10-11
**Scope:** the Dealer Positioning page only (`webgui/pages/options/gamma.py`,
`gamma_heat.py`) and `config/gamma_heat.toml`. Nothing the options service
publishes changes, and nothing stored is altered.
**Status:** built on the worktree branch; see the changelog for what has shipped.
**Asked for by the user:** after asking how the heat map's smoothing works and
being told it was only the browser blending a stretched picture, "I need real
smoothing in the heatmap."

## What there was, and what was missing

The heat map is drawn as one small image, a pixel per strike per minute, and
stretched to the panel. The browser blends neighbouring pixels as it enlarges.
That softens the EDGES between cells and nothing else: a strike that reads 10,
0, 10, 0 from one minute to the next still draws as stripes, because every one
of those cells is still its own colour.

Real smoothing averages the VALUES, before they are coloured.

## What the operator gets

A **Smooth** picker beside the heat map's other pickers: **Off**, **Light**,
**Medium**, **Strong**. It starts at **Medium**, and the choice is remembered. Off
is the heat map as it was.

The default was Off as first built, by the habit that a new display option is off
until asked for. The user changed it to Medium before it shipped: they had asked
for the smoothing itself, not for an option. Because the public page takes the
shipped defaults, it is smoothed at Medium too.

## How it works

- Each cell becomes a weighted average of the cells around it, with a bell
  curve's weights: most weight on the cell itself, less on each neighbour the
  further away it is, nothing past three widths.
- Two widths, one along time and one across strikes, because they mean different
  things. Averaging over a few minutes removes noise. Averaging over strikes
  merges walls, so that width is kept small.
- `config/gamma_heat.toml [smooth]` holds the widths at **Medium**: `minutes = 3.0`
  and `strikes = 0.8` (each a standard deviation). **Light** is half of them and
  **Strong** twice.
- **A gap stays a gap.** A cell with no reading is neither averaged in as a zero
  nor filled. The weights are shared out again over the cells that exist, which
  is also why an edge or a corner is not dimmed for having fewer neighbours.
- It is applied to the grid **in the frame being drawn** (so in From spot it
  averages cells the same distance from price), and **before** the share is
  taken, so in Share of column each minute still adds up to 100%.
- **The contours are traced from the smoothed cells.** On the 2026-10-09 `$SPX`
  session the Gamma view's contours went from 38 lines, many of them specks, to 6.
- **The forward band is not smoothed.** It is a projection drawn beside the
  session, not a reading in it.

## What it costs the reader, said on the page

Smoothing is a trade, and the picker's own tooltip says so:

- A smoothed peak is **lower and wider** than the real one. On a Locked scale a
  smoothed wall is a little dimmer than the same wall raw.
- **The newest minutes lag.** The last column can only be averaged with the past,
  so a move in the last few minutes shows late and muted. At Strong that is
  several minutes.
- **The cell's number is no longer the stored reading.** Press and hold shows the
  smoothed figure and says "(smoothed)". The by-strike bars are never smoothed,
  and Off gives the exact cell.

## Cost, measured

On that session (381 minutes, 38 strikes): about 27 ms more a repaint at Light,
37 ms at Medium and 59 ms at Strong, in pure Python (the web tier may not import
numpy). Nothing while Off. The size of what is sent does not change.

## Not done

- **No finer picture.** The image is still a pixel a cell, so the browser's blend
  between two cells is still between their colours. Averaging the values first
  makes neighbouring cells close in value, which is what removes the banding.
- **The Term view** is not smoothed: its axis is expiries, not minutes.
- **The ridge plot and the gravity well** draw the raw values.

## Choices that were mine

Three strengths instead of a slider; a bell curve instead of
a moving average; the two widths and their Medium values; smoothing the contours
with the cells instead of leaving them on the raw grid.

## Verified

- `test_gamma_heat.py` (the arithmetic) and `test_options_gamma.py` (where it sits
  in the figure, and the controls).
- Each strength rendered from the real `$SPX` session of 2026-10-09 with the page's
  own builders, in both frames.
- In the page harness on that session: a page whose picker has never been
  touched opens at Medium and draws smoothed. As first built, with Off: choosing
  Medium redraws the cells (the summed minute-to-minute change across the map fell
  to a quarter), the tooltip says "(smoothed)", the bars are unchanged, the
  contours are drawn intact and survive a repaint, Strong smooths further, and the
  choice is restored after a restart.
- Not seen on the running app.

## Where the code is

```
webgui/pages/options/gamma_heat.py   SMOOTHS, SMOOTH_STRENGTH, smooth_args, smooth   (pure)
webgui/pages/options/gamma.py        heatmap_figure(smooth=), HeatControls: the Smooth picker
shared/gamma_heat_config.py          smooth()
config/gamma_heat.toml               [smooth]
```
