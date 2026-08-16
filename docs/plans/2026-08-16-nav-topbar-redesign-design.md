# Nav rail + top bar redesign (NEURALSTRIKE) — design

**Date:** 2026-08-16
**Status:** approved, implementing
**Source:** a supplied design set — `Menu.dc.html` (264 x 764), `Menu Collapsed.dc.html`
(68 x 764), `Top Bar.dc.html` (1400 x 56) plus its README. Those files are a
`dc-runtime` design doc, not shippable code; this is the port into the NiceGUI
shell (`webgui/main.py`).

## What changes

The rail keeps its hover-to-expand behaviour and every one of its ten entries.
What moves is the ORGANISATION (three captioned sections replacing one flat
list), the GEOMETRY (68/264 instead of 64/248), the FOOTER (a live service-status
card above the system block) and the TOP BAR (breadcrumb moves left, behind the
wordmark).

Two elements of the supplied design are deliberately **not** ported: the `⌘K`
search pill and the notification bell. Both were considered and dropped — see
"Rejected" below.

## 1 · Section-grouped rail

Today `_layout` hand-orders the drawer body: Options group, Strategy Tools group,
the three `OPTIONS_RAIL` pages, the sentiment group, `FLAT_NAV`, the More group,
then a separator and `SYSTEM_RAIL`. That reading order is implicit in the
sequence of calls, so it can only be changed by editing render code.

Replace it with data — a `NAV_SECTIONS` list of `(caption, entries)`, where each
entry is either a GROUP (renders via `_nav_group_link`) or a RAIL page (renders
via `_nav_link`):

| Caption | Entries |
|---|---|
| **MARKETS** | Dealer Positioning · Opportunity Board · Flow Alerts · Trend & Sentiment |
| **STRATEGY** | Strategy Tools · Options · Trade Analyzer · Claude Trades |
| **ACCOUNT** | Portfolio · More |

`SYSTEM_RAIL` (System Status · Settings · Stop All Services) stays pinned to the
foot, unchanged in role.

Three consequences worth stating:

- **`"Market Trend & Sentiment"` renames to `"Trend & Sentiment"`.** It is the
  group label, so it is also what `breadcrumb_parts` returns — a pinned test
  carries the old string.
- **`FLAT_NAV` stops driving order.** It survives as the flat-route registry that
  `_NAV_LABEL` and the icon-distinctness test iterate; the drawer reads
  `NAV_SECTIONS`. Its three members now sit in two different sections.
- **Caption counts are derived** (`f"{len(entries):02d}"`), never written down.
  The design's `04 / 04 / 02` falls out of the lists; adding a page cannot leave
  a stale number behind.

The Options group sits under STRATEGY while three closely related pages (Dealer
Positioning, Opportunity Board, Flow Alerts) sit under MARKETS. That split is
intentional and was confirmed: those three are market-wide reads, whereas the
Options group is the per-signal find → analyze → track → repair workflow.

### Collapsed state

A caption is unreadable at 68px, so in the rail each section renders a 1px
`rgba(255,255,255,.07)` divider instead. This is the exact inverse of the
mechanism already in `_NAV_CSS`: `.nav-title`/`.nav-label` sit at `opacity: 0`
and fade in under `.nav-drawer:hover`, `:focus-within` and `.nav-pinned`. The new
`.nav-sep` starts visible and fades OUT under the same three selectors — one more
rule in the same block, no JS, no second render path.

## 2 · Geometry

`NAV_WIDTH_RAIL` 64 → **68**, `NAV_WIDTH_OPEN` 248 → **264**.

Three coupled sites, and the coupling is already partly enforced: the constants
themselves; the `width: …px !important` literal inside `_NAV_CSS` (a test asserts
it interpolates `NAV_WIDTH_OPEN`, so it follows automatically); and
`test_drawer_width_pinned_vs_rail`, which pins the raw integers `248`/`64` and
must be updated by hand.

## 3 · Badges — one deliberate deviation from the design

The design shows an inline count pill (right-aligned, mono 10px, rose on
`rgba(229,72,77,.16)`) when the rail is expanded, degrading to a 6px dot at the
icon's top-right when collapsed.

We keep the existing Quasar `floating` badge on the icon corner in BOTH states,
unchanged in position **and** colour. Reasoning: that badge already works
collapsed and expanded, it is the element the 2 s watcher updates through
`_badge_refs`/`_group_badge_refs`, and four tests pin its position inside the
`relative` wrapper (Quasar's `floating` is `position:absolute`, so it anchors to
the nearest positioned ancestor). A second, state-dependent badge element per row
would double the watcher's update surface for a purely cosmetic gain.

The design's softer rose (`#ff8f92` on `rgba(229,72,77,.16)`) is also not
adopted: `_count_badge` serves the tab strip as well as the rail, so recolouring
it is an app-wide change, and a translucent 6px badge on a dark rail is harder to
read than the solid one it would replace.

The static **AI** pill on Claude Trades has no such cost — it is a fixed label,
not watcher state, so it is built once and never registered for the tick — and it
is ported as designed.

**Stop All Services becomes a danger-outlined button** rather than a third
navigation row in the footer, and moves last in `SYSTEM_RAIL` (today it sits
between System Status and Settings). It is the one item in the rail that does
something irreversible to a running stack; it should not be reachable by aiming
at Settings and missing.

## 4 · Footer status card

A card above `SYSTEM_RAIL`: a pulsing green dot, "Data feed live" / "Data feed
degraded", a mono subline "N services · M ms", and a right-aligned red count.

Every number is real and none of them costs a new probe. `_watcher_compute`
already calls `_probe_services_health`, which fans out to each Tier-2 `/health`
at most once per `_HEALTH_PROBE_INTERVAL_SEC` (30 s) and caches the result:

- **N** — services reporting up, out of `SERVICE_URLS`.
- **M** — the measured round-trip of that fan-out. `_svc_health_cache` gains a
  `latency_ms` key; this is additive to a probe that already runs.
- **count** — `len(alerts.unhealthy_keys(freshness, health))`, which is exactly
  the number already driving `_NAV_BADGES["/status"]`. The card and the System
  Status badge cannot disagree, because they are the same computation.
- **live vs degraded** — that count being zero.

Flow: `_watcher_compute` stashes a small `_STATUS_CARD` dict (it already holds
both inputs at that point); `_tick` writes the three labels in place and swaps
the tone class via `.classes(remove=…, add=…)`, so repeated repaints cannot stack
conflicting colour classes — the documented reactive-recolour idiom.

Collapsed, the card renders as the dot alone, centred.

The pulse is a `@keyframes` animation, which no Tailwind utility expresses; it
goes in `_NAV_CSS` alongside the other Quasar-internal rules — the standard
single escape hatch, not a new exception.

**Degradation:** an unreadable bus or a failed probe must render the card in an
honest unknown state (grey dot, no counts), never a confident "live". This is the
same failure class the repo has been bitten by before — a defensive default that
reads as a real measurement.

## 5 · Top bar

- The breadcrumb moves to the **left**, after the wordmark, behind a 1px x 22px
  hairline divider. The market-status pill keeps the right edge alone.
- Its separator becomes a `›` caret (`chevron_right`) in place of today's 4px
  dot; section text `#5d6a88`, page text `#e6ecf9` at 600.
- The logo mark needs no new plumbing: `brand_mark_src()` already resolves
  `[brand].mark` and returns `""` when the file is genuinely absent, so a missing
  asset still renders the wordmark alone rather than a broken-image icon.
  `theme.build_brand_css`'s `.brand-mark` rule already sets 28px / radius 8; it
  gains only the design's hairline border.
- `breadcrumb_parts` is untouched in shape — only its rendering position moves.

## Rejected

**`⌘K` search pill and notification bell.** Both appear in the supplied top bar
and both were dropped on review. A search palette over ~26 route labels is real
work (overlay, keyboard handling, fuzzy matcher, tests) for a rail that is
already one hover away, and the bell would duplicate what the System Status badge
and the existing toast/chime path already say. Shipping them as decoration was
rejected outright: a control that looks live and does nothing is worse than its
absence.

## Testing

Updated invariants: the group rename in `breadcrumb_parts`; the 68/264 geometry;
`test_drawer_icons_are_present_and_distinct`, whose scope becomes `NAV_SECTIONS`;
and `test_a_group_only_renders_if_the_drawer_builds_it`, which counts
`_nav_group_link(` occurrences in `_layout`'s source — once the body is a loop
that guard must instead assert every `_NAV_GROUPS` entry appears in
`NAV_SECTIONS`.

New coverage:

- `NAV_SECTIONS` is a **partition** — every rail route and every group appears
  exactly once, and together they cover the old drawer body with nothing dropped.
  This is the guard that a regrouping cannot silently lose a page.
- Caption counts derive from list length rather than literals.
- The status card reports unknown (not "live") when the probe or the bus fails,
  and its count matches `_NAV_BADGES["/status"]`.
