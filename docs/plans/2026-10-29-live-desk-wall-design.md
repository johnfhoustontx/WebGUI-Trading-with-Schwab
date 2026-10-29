# Live Desk: the 3D wall — design

**Date:** 2026-10-29
**Source:** the user's design handoff, `Neural Strike Live redesign.zip`
(`handoff/live.html` + README, option **3a "Wall"**).

## What changes

`deploy/site/live.html` stops being a page of tool cards and three grids of
tiles. The twelve tiles now stand on a slowly turning 3D ring. A caption under
the ring names the screen in front, and the tools are one row of pill links
under that.

- **Hero:** eyebrow with the count, the design's headline *Read the market. Then
  trade it.*, the existing lede, and three group chips on the right.
- **Wall:** drift (paused on hover, focus, a hidden tab, off-screen, or reduced
  motion), drag with snap, click a side tile to bring it front, click the front
  tile to open it, prev/next buttons, ←/→ and Enter on the stage. A chip turns
  the ring to its group's first tile and is marked while that group is in front.
- **Tools:** five pills in table order (Strategy Tools still names Calculator ·
  Simulator · Expected Move).

## Where the handoff was not taken as is

The handoff was written against an older copy of the site. These parts conflict
with standing rules and were adapted, not copied:

| Handoff | Here | Why |
|---|---|---|
| Phosphor icons from unpkg, Inter from Google Fonts | The inline sprite and the self-hosted Inter | The site fetches nothing from anyone (`test_no_page_reaches_an_external_origin`) |
| Its own nav, with a Gallery link | The shared site menu, unchanged | The menu is byte-identical on every page; the Gallery was retired 2026-10-24 |
| Cards built by JS from a `GROUPS` array | Tiles stay in the markup, in their group sections; `live.js` reads them | The page must work with no script, and the tiles are pinned against `webgui/live_screens.py` by `deploy/tests/test_site.py`. A second list in JS would drift |
| Inline `<script>` | `assets/live.js` (replaces the old cursor spotlight) | The page's script list is pinned exactly |
| Its one-line descriptions ("Confirm the wording") | The site's existing descriptions | They were already written and reviewed for each screen |
| Six tool pills (Calculator and Simulator apart) | Five, Strategy Tools as one | The one-card decision of 2026-10-24 stands |
| Absolute `https://neuralstrike.co/live/<slug>.webp` | Relative `live/<slug>.webp` | Same file, same origin |
| No lede | The lede is kept | It carries the published counts that `test_the_live_page_states_the_published_counts_in_words` pins, and it is the page's text with no script |

## Mechanism

One markup, two layouts. With no script the tiles are the grouped grid with
headings. `live.js` adds `.is-on` to `.ns-wall`. Under it the group sections
and their grids are `display: contents`, so every tile is laid out as a direct
child of the `preserve-3d` ring. Each tile gets `rotateY(i·360/N) translateZ(R)`
with R = (card width / 2) / tan(π/N) + 20. The card width is 26% of the stage,
clamped to 200–300 px. The ring gets `translateZ(-R) rotateX(-7°) rotateY(rot)`.
The animation loop schedules a frame only while something moves.

`overflow-x: clip` (not `hidden`) on the page wrapper keeps the ring's side
tiles from widening the page, and keeps the sticky menu sticky.

## Tests

`deploy/tests/test_site.py`: the tool and chip tests now read the pill markup;
the same facts are pinned (table order, Strategy Tools names all three, each
chip reaches and counts its group). A new test pins that the page ships
without `is-on` and that `live.js` names no screen.
