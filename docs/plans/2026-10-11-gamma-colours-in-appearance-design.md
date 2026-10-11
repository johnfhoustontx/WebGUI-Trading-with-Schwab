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

## The Flow and Net Prem tabs follow

Left out of the first slice and asked for by the user the same day. Those two
panels are hand-built HTML and SVG (`flow_panels.py`) with a palette of their own,
`[flow]`, whose call and put pair was the plasma pair by design.

- **Five `[flow]` keys follow `[gamma]`** (`theme.FLOW_FOLLOWS`): `call` and `put`
  take Call and Put, `call_deep` and `put_deep` (the far stop of each ribbon)
  take Call mid and Put mid, and `ice` (chip rings, the cursor, the bar grounds)
  takes Call peak. Those were already the same five colours.
- **A chosen colour wins; the shipped one does not.** `flow_colors` replaces a
  `[flow]` key only when its `[gamma]` colour is not the shipped one. So the
  shipped palette is untouched to the character, and a `[flow]` value set by
  hand in the file keeps its say until the matching colour is chosen in
  Appearance.
- **About twenty literal tints became one function.** `rgba(53,200,255,.12)` and
  its like, for the call, put and ice colours, were written into the markup and
  into the client script. They are all `flow_panels._tone(key, alpha)` now. The
  alpha is pasted as written, so at the shipped colours every string is the one
  it replaced (a test pins thirteen of them).
- **The client script takes its tints from the payload.** `_SCRUB_JS` is one
  constant string that draws the strike ladder in the browser, so the five tints
  it needs travel in the payload's `col` (`callInk`, `callDim`, `putInk`,
  `putDim`, `rowBg`), built on the server from the palette.
- **A test reads the module's source** and fails on a call, put or ice colour
  written into it again.
- Not followed, because they are not call or put colours: the panel's dark wash,
  the spot line, the labels, and the Net Prem tab's one colour per symbol.

## Not done

- **The picture pushed to Telegram and Discord** is drawn by a separate renderer
  and keeps its colours.
- **The public site** reads the same theme file, but its process restarts on its
  own: a colour saved in Appearance shows there after that unit restarts or after
  the next promote, not after "Restart now".
- **No control for the grade positions or opacity** (see above).

## Choices that were mine

Reading "grading" as the shades; four grades a side; the group living in
Appearance with the other colours instead of on the page; deriving the contours,
ridge fills and wash instead of adding six more pickers; and, for the Flow tabs,
letting a colour chosen in Appearance win over a `[flow]` value set by hand.

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
- The Flow tab, freshly started with the same green and orange set on the real
  `$SPX` session: no cyan, magenta or ice left anywhere in the page, the strike
  ladder (drawn by the client script) included. The Net Prem tab's frame was seen
  the same way; its populated state has no data in the harness seed and is
  covered by the test that renders it under a changed palette.
- Not seen on the running app.
