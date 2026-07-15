# Nav icon rail — design (2026-07-15)

Branch: `Using_Highcharts`

## Problem

The webgui drawer is a flat main menu of seven items (**Options**, **Market Trend &
Sentiment**, **Market Dashboard**, **Trade Analyzer**, **Portfolio**, **Claude Trades**,
**More**); each group's child pages render as the top tab strip, not in the drawer.

Every nav item already carries a Material icon in its tuple (`_NAV_GROUPS` / `FLAT_NAV` /
the `*_CHILDREN` lists), but `main._nav_link` **does not render it** — the Deep Slate
redesign replaced the icon with a colored dot and left the `icon` argument dead:

```python
# Deep Slate: a dot indicator (blue when active, faint when not) — the
# ``icon`` arg is retained in the signature but no longer rendered.
ui.element("div").classes("nav-dot " + ("nav-dot-active" if is_active else "nav-dot-idle"))
```

The drawer is also permanently open (`ui.left_drawer(value=True)`), costing ~250px of
width on every page.

## Goal

Render curated per-item icons, and make the drawer a **collapsed icon rail that expands on
hover and collapses again after a selection**.

## Decisions

1. **The icon replaces the dot.** Active state is carried by the existing navy pill wash
   (`.nav-active`) plus the icon tinting to the accent. The collapsed rail is then the
   expanded rail minus labels — no element swap between states.
2. **Expanding overlays the content** (does not push it). Reflowing on every hover would
   resize charts, and this app's Highcharts elements have no ResizeObserver — they would sit
   mis-sized until their next repaint.
3. **Hover always wins after a selection.** Navigation is a full page load, so the next page
   paints collapsed; if the cursor is still over the rail it re-expands. No JS latch, no
   stuck state.
4. **The header hamburger becomes pin / unpin**, persisted in `app_settings`.

## Icons

Icons are the only affordance in the collapsed rail, so each must name its page at 24px and
be distinct from the other six. Five are already right; two are changed.

| Item | Now | Proposed | Rationale |
|---|---|---|---|
| Options | `candlestick_chart` | keep | Reads as the trading workspace. |
| Market Trend & Sentiment | `insights` | **`speed`** | `insights` is a generic trend line colliding with Trade Analyzer's `analytics`. The page *is* four speedometer gauges. |
| Market Dashboard | `dashboard` | keep | The tile grid matches the page's tile grid. |
| Trade Analyzer | `analytics` | **`query_stats`** | `analytics` says "charts", not "analyze one symbol". A magnifier over bars is the actual job. |
| Portfolio | `account_balance` | keep | Bank building = real money, separating it from the *paper* portfolio under Options. |
| Claude Trades | `smart_toy` | keep | Robot = autonomous driver. |
| More | `more_horiz` | keep | Standard overflow convention. |

Scope is the **drawer only**. The top tab strip keeps its text-only pills (its child icons
stay unrendered, as today).

## Mechanism

A narrow drawer plus one CSS hover rule. No Quasar mini-mode, no JS, no hover round-trips.

`ui.left_drawer` has **no `width` kwarg** (verified against the installed NiceGUI), so the
width goes through Quasar's own prop: `.props("width=64")`. Quasar then sets the page's left
offset to 64px and renders `<aside class="q-drawer" style="width:64px">`.

A stylesheet rule widens it on hover:

```css
.nav-drawer:hover { width: 248px !important; }
```

Author `!important` beats Quasar's inline `style="width:64px"` (CSS cascade), so this wins
without fighting the framework. Because Quasar's **layout** still believes the drawer is
64px, `.q-page-container`'s padding never changes — the expanded menu floats over the
content, giving the overlay behavior for free. Labels live in a `nowrap` / `overflow:hidden`
row so they clip when narrow and fade in on hover; a `transition` on width and label opacity
smooths it.

Quasar mini-mode is deliberately **not** used: it assumes `q-item` / `q-item__section avatar`
structure these hand-built `ui.link` rows do not have, and it would need reactive prop
toggling on hover.

## Badges

The red count badges (new scanner signals, captured signals, rescue) sit at `ml-auto` today
and would be clipped by the collapsed rail. They move to a small chip anchored to the
**icon's top-right corner in both states**, so a collapsed rail still reports "3 new
signals" and no element swaps between states. `_badge_refs` / `_group_badge_refs` and the 2s
watcher are untouched.

## Pin

`app_settings` gains `nav_pinned` (default `false`). The hamburger toggles it:

- pinned → `drawer.props("width=248")` + a `nav-pinned` class that disables the hover rule
- unpinned → back to `width=64`

Quasar recalculates the content offset reactively, so no page reload. The setting persists
across pages and restarts.

## Testing

- Pure `drawer_width(pinned)` in `main.py`, unit-tested in `test_shell.py`.
- A guard asserting all seven drawer icons are non-empty and mutually distinct — the thing
  that silently rots as nav items are added.
- CSS lands in `_NAV_CSS`, the documented Quasar-internal escape hatch: overriding an inline
  width requires `!important` and cannot be expressed as a Tailwind class, so this is
  consistent with the Tailwind-first standard's stated exception.
- Live browser check of four states: collapsed, hover-expanded, pinned, and a badge on a
  collapsed icon.
