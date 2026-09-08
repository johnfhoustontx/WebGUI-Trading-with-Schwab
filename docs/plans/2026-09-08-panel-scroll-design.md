# Panels that scroll instead of pages that do — design

**Date:** 2026-09-08
**Status:** approved, not yet built

The dashboard screens are authored for a wide monitor and clip below it. Today
the whole **document** scrolls sideways to reach what is cut off, which costs the
reader the panel title and the symbol column — the two things that say what they
are looking at. This moves that scroll **into the panel** and pins the column
that identifies the row.

It is a scrolling fix, not a redesign. Nothing a panel *contains* changes.

## Why now

`live.neuralstrike.co` publishes fourteen read-only screens by rendering **the
real page modules the private app renders**, so whatever the app does at a given
width, the public site does too. Measured 2026-09-08 across the 74 non-test page
files: **one** Tailwind responsive prefix in the entire app. There are no
breakpoints because the pages were never built to reflow — `main.py` even opts
out of Quasar's mobile drawer explicitly (`behavior=desktop`, with a comment
saying so).

The static marketing site is the opposite and needs nothing here: `clamp()` type,
five `repeat(auto-fit, minmax(…))` grids that reflow with no breakpoint at all,
three real media queries, and `overflow-x: auto` on its one wide table.

## ⚠ This reverses a documented decision, and the reversal has to be argued

`webgui/pages/desk.py` (the `_GAP` note) states the current position outright:

> `overflow-x-auto` was deliberately not used as the fallback: a dashboard you
> scroll sideways to read defeats the page's purpose.

That reasoning was sound and its conclusion did not hold. **Rejecting per-panel
scroll did not prevent sideways scrolling — it relocated it to the document**,
where it is strictly worse: the page carries the panel headings and the leftmost
identity column off-screen with everything else, so the reader ends up on
unlabelled numbers, which is precisely the failure the note was trying to avoid.

So the choice was never scroll versus no-scroll. It is **where** the scroll
happens, and a panel-level scroll that keeps the symbol pinned is the version
that honours the original objection.

⚠ **The note must be corrected in place, not annotated.** CLAUDE.md's
maintenance rule is explicit: edit the sentence, never leave superseded reasoning
underneath a warning.

## What the panels actually are

Not Quasar tables — `desk.py` contains **zero** `ui.table(` calls. Each panel is
a set of CSS grids (a head row and one grid per data row) sharing a single
`grid-template-columns` string of `minmax()` tracks. `desk.py` notes that
identity is load-bearing: the same string in both places is "the only thing
keeping the labels over their columns".

That matters because the two mechanisms already in the tree do **not** cover it.
`shell.TABLE_CSS` gives `.q-table thead tr th` a sticky top and caps
`.q-table__middle` at `65vh` — vertical, and only for Quasar tables. A hand-built
grid gets neither.

**The measured boundary, from `desk.py`'s own comments:** at 1877px the panels
measure 839px and that sum "is the boundary rather than an estimate of it"; below
it the page clips, because a CSS grid will not shrink a track under its
`minmax()` floor. At 1600px: Positions over by 134px, the Board by 78, Dealer
Positioning by 52. Only Flow, with four tracks, still fits.

## The mechanism

Each panel becomes its own horizontal scroll container. Three parts, and the
first two are what stop it being a no-op:

1. **The panel's grids need a `min-width` equal to the sum of their `minmax()`
   floors.** Without one the tracks keep shrinking and there is nothing to
   scroll. ⚠ **Derived, never typed.** `desk.py` already carries the warning that
   applies — "keep this sum current when a floor moves; it is the number the next
   track is sized against" — and a typed copy silently stops matching the first
   time a floor changes. `PANEL_BUDGET_PX` is the precedent: it is computed from
   `DESK_WINDOW_PX - DESK_SCROLLBAR_PX - DESK_CHROME_PX - PANEL_GUTTER_PX`,
   halved, rather than written down.
2. **The first cell of every row is `position: sticky; left: 0`.** Head row and
   data rows are separate grid elements sharing the template string, so the rule
   keys off the first child within the shared row class rather than off any one
   panel. This is the part that answers the objection above.
3. **Overflow is contained at the panel**, so the document stops scrolling
   sideways and the page heading stays where it is.

## Where the code goes

A shared CSS block in **`webgui/shell.py`**, beside `TABLE_CSS` and `SUBTAB_CSS`.
That is the same category those two already occupy — styling widgets a PAGE
mounts rather than nav chrome — and both entrypoints already inject them, so the
public screens and the private app cannot diverge. Panels opt in by class.

No new runtime dependency. No change to any page's content, so the
"a published screen cannot drift from the private one" invariant is untouched.

## Scope: measure before changing fourteen screens

Only the Desk's clipping is established, from its own comments and from
screenshots. **The other thirteen are unmeasured**, and screens built on
`ui.table` may already contain their overflow inside `.q-table__middle`. The
plan opens with a measurement pass and changes only what it finds.

## Testing, and the one new dependency

⚠ **The measurement gap is real and it is why a tool is being added.** Chrome's
`--headless --screenshot` cannot report element widths, and the Claude Browser
pane returns `viewport: 0` on this app — the trap CLAUDE.md already documents
("always print the viewport beside chart size"). A design entirely about widths
cannot be verified by either.

**Selenium goes in `requirements-dev.txt` — never `requirements.lock`.** Prod's
venv installs the lock, and prod does not test layout, so the lock must not move.
Selenium over Playwright for one concrete reason: it drives the Chrome already
installed on the box (the same binary `capture_gallery_shots.py` finds) instead
of downloading a second ~150 MB browser onto a 4-vCPU host that already runs
Chrome for the wall stream.

Three layers:

- **Pure unit test** — the min-width is derived from the track floors. Mutate a
  floor and assert the value moves; a typed constant would not. This is the
  discriminating test, and per the CLAUDE.md config-extraction note, asserting
  `derived == literal` proves nothing because the literal it replaced had the
  same value.
- **Measurement harness** — load each live screen at a set of widths and assert
  the DOCUMENT no longer overflows while the panel DOES. Both halves matter: an
  assertion that nothing overflows anywhere would pass over a panel that clips.
- **Existing guards keep holding** — `test_no_inline_style.py` (this is CSS in
  the one documented `shell.py` escape hatch, not `.style()`), and the shell-seam
  tests.

## Failure modes

| Failure | Result |
|---|---|
| A floor moves and min-width does not follow | The unit test fails. That is the whole reason it is derived. |
| A panel opts in but its first column is not the identity column | Scrolling pins the wrong thing — worse than not pinning. The plan checks each panel's first track before applying the class. |
| Sticky cell paints under a scrolling neighbour | A stacking-context bug, visible immediately; the sticky cell needs a background and a z-index above its row. |
| A screen already contains its overflow (Quasar table) | Left alone. Measured, not assumed. |
| Selenium's Chrome and the box's Chrome diverge | Dev-only, so prod is unaffected; Selenium Manager resolves the driver. |

## Deliberately not built

- **Reflowing or dropping columns** so panels fit with no scrolling. That was the
  original design's strategy (qualifiers ride as a second line inside a cell) and
  extending it is real layout work on four panels across fourteen screens, each
  needing a judgement about which qualifier moves.
- **Phone-width support.** The pages are authored for a monitor; 375px is a
  different design problem, not a scrolling one.
- **Highcharts reflow on resize.** The missing `ResizeObserver` is a real and
  documented trap — charts never re-fit after first paint — but it is a separate
  fault from panel clipping and mixing them would hide which fix did what.
- **Touching the static marketing site.** Measured as already fluid.
