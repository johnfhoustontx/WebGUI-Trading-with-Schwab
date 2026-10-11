# Dealer Positioning colours in Settings (design, written as built)

**Date:** 2026-10-11
**Scope:** `config/theme.toml [gamma]`, `webgui/pages/options/theme.py`,
`gamma_heat.py`, `gamma.py`, and Settings -> Appearance (`pages/appearance.py`).
Nothing the options service publishes changes.
**Status:** built on the worktree branch; see the changelog for what has shipped.
**Asked for by the user:** "I need to be able to select the put and call colors in
the settings and the grading of the colors."

## What the operator gets

A **Dealer Positioning** group in **Settings -> Appearance** with eight colours:

```
puts:   Put peak   Put   Put mid   Put low        (largest values -> smallest)
calls:  Call low   Call mid   Call   Call peak    (smallest values -> largest)
```

- **Put** and **Call** are the two main colours. Besides their place on the heat
  map they are the by-strike bars, the call and put walls, the hedge panel, the
  gravity well, the ridge plot and the contour lines.
- The other six are the **grading**: the shade the heat map passes through below
  and above each main colour. Read left to right, the eight are the colour scale
  from the largest put value to the largest call value.
- The preview on the right of the Appearance page draws the heat map's own
  gradient from the unsaved colours, so a grading can be judged before it is saved.
- As with every Appearance change: **Save changes**, then **Restart now**.

## How I read "the grading"

As the SHADES the scale passes through, four a side, which is what a colour picker
can choose. It is not a control for how quickly the scale brightens: where each
grade sits on the colour axis (puts at 0.48, 0.28, 0.12 and 0.00, calls mirrored
at 0.52, 0.72, 0.88 and 1.00, zero clear at 0.50) and how see-through it is stay
in code (`gamma_heat.RAMP`). Those two
were tuned for the plasma design, and a brighter or darker shade at a grade
already moves how fast the map lights up. If a control for the positions is
wanted, `RAMP` is the one place it would come from.

## How it is built

- `theme._DEFAULTS["gamma"]` holds the eight colours, shipped as the exact
  colours the stops held as literals. `theme.gamma_colors(theme)` hands them back
  as `#rrggbb`, with the shipped colour standing in for anything missing or not a
  hex (the editor accepts `#abc`; a chart does not, so it is widened there).
- `gamma_heat.ramp_stops(colours)` builds the colour axis's nine stops from them.
  With the shipped colours the list is identical, string for string, to the old
  literal (a test pins it).
- `gamma.palette(colours)` is everything else the page draws in one of the two
  main colours. The module's constants (`POS_COLOR`, `NEG_COLOR`, `HEAT_STOPS`, the
  well's fills, the ridge plot's fills, the contour colours, the wash) are its
  output for the running theme, resolved once at import like the rest of the theme.
- The wash behind the heat map is a dark tint of each side's LOWEST grade. Found
  in the harness: with green calls the plot kept a blue cast at the top.

## What moved at the shipped colours

Nothing an operator would see, but not nothing:

- The heat map's stops, the bars, the walls, the well's fills and the wash are
  identical to the digit.
- The **contour lines** and the **ridge plot's fills** were hand-picked tints and
  are now computed from the main colour (a 78% tint, and 40% of its brightness),
  so they follow a chosen colour. At the shipped colours they moved by at most
  8 parts in 255 on one channel.

## Not done

- **The Flow and Net Prem tabs do not follow.** Their cyan and magenta are also
  written into about twenty literals in `flow_panels.py` (rings, rails, hairlines).
  A heat map in new colours beside those two tabs in the old ones disagree until
  those are derived too. The group's own description says so.
- **The picture pushed to Telegram and Discord** is drawn by a separate renderer
  and keeps its colours.
- **The public site** reads the same theme file, but its process restarts on its
  own: a colour saved in Appearance shows there after that unit restarts or after
  the next promote, not after "Restart now".
- **No control for the grade positions or opacity** (see above).

## Choices that were mine

Reading "grading" as the shades; four grades a side; the group living in
Appearance with the other colours instead of on the page; deriving the contours,
ridge fills and wash instead of adding six more pickers; leaving the Flow tabs.

## Verified

- `test_theme_gamma.py`, and additions to `test_gamma_heat.py`,
  `test_options_gamma.py` (including a reload of the page module under a changed
  theme, so the colours are shown to be READ) and `test_appearance.py`.
- In the page harness: the group draws its two rows of four; one colour picked
  through the real picker repainted its tile and the preview ramp and counted as
  one unsaved change; **Save changes** wrote `[gamma] call` to the override file.
  A full green and orange set, saved through the same function, then drew on a
  freshly started heat map (real `$SPX` session of 2026-10-09): stops, bars,
  walls, legend, contours, well, ridge plot and wash.
- Not seen on the running app.
