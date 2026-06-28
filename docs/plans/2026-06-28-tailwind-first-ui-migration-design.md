# Tailwind-first UI migration — design

**Date:** 2026-06-28
**Branch:** `Using_Highcharts`
**Status:** design approved — implementation not started

## Goal

Make **Tailwind utility classes via `.classes()` the single, mandatory way to style
every NiceGUI component** in `webgui/`. Today the app is mostly there (607 `.classes()`
calls) but still carries **106 `.style()` calls across 15 files** and **~13
`ui.add_css()` raw-CSS blocks** (the `DASHBOARD_CSS` theme, the `_NAV_CSS` shell, and
per-page blocks like `SCAN_CSS`/`PAPER_CSS`/`RESCUE_CSS`/`CAPTURED_CSS`/`CALC_CSS`/
`EXPLAIN_CSS`/`DRIVER_CSS`). This migration removes the `.style()`/inline-style usage and
collapses the per-page CSS into a shared **Tailwind-class-string token vocabulary**,
keeping only an irreducible escape hatch for Quasar internals.

The standard itself is recorded in the root `CLAUDE.md` → **"UI styling standard —
Tailwind-first"**. This doc is the migration *plan*.

## Decisions (locked)

1. **Scope: pragmatic.** Tailwind `.classes()` is mandatory for all component styling;
   `.style()`/inline-style/fixed-px are banned. A **single documented `ui.add_css` block
   per theme** survives **only** for Quasar-internal / teleported DOM that component
   classes physically cannot reach (`q-field__control`, `q-tab*`,
   `.nicegui-expansion-content` gap, the body-mounted `.strat-menu-navy` popup).
   Standalone `HTMLResponse` documents (EOD `summary/detail.html`, Gamma Explain/Analyze
   infographics) and Highcharts option dicts are **out of scope** (not NiceGUI
   components).
2. **Intent: convert + light polish.** Preserve today's dark-navy look so nothing
   regresses; standardize it into the token vocabulary; fix obvious inconsistencies as
   each screen is converted. No gratuitous redesign.

## Why tokens, not CSS classes

Tailwind ships bundled in NiceGUI, but **custom theme colors are not trivially
configurable** (no easy `tailwind.config` extend), so the exact navy palette is carried
as **`[#hex]` arbitrary values**. Rather than repeat long utility strings, the theme
becomes **Python string constants**:

```python
# webgui/pages/options/theme.py  (target shape)
PAGE        = "rounded-[14px] border border-[#1d2942] p-[18px] text-[#cdd8ee] " \
              "bg-[radial-gradient(130%_90%_at_50%_-20%,#16243f_0%,#0c1424_55%,#0a0f1c_100%)]"
CARD        = "bg-[#101a30] border border-[#213152] rounded-xl px-4 py-3.5"
EYEBROW     = "text-[#8794b4] text-xs tracking-wide"
LABEL       = "text-[#eaf0fb]"
MUTED       = "text-[#7f8db0]"
BTN         = "bg-[#15213b] hover:bg-[#1b2950] text-[#cdd8ee] border border-[#2a3a5c] rounded-[9px] min-h-[40px] font-medium"
BTN_PRIMARY = "bg-[#2563eb] hover:bg-[#1d4fd1] text-white rounded-[9px] min-h-[40px] font-semibold"
```

Applied as `with ui.column().classes(CARD): ...` / `ui.button(...).classes(BTN_PRIMARY)`.
The palette is the canonical hex list in the `CLAUDE.md` "App theme" section — tokens
encode it 1:1 (convert + light polish: same colors, expressed as Tailwind).

**What stays in `ui.add_css` (the escape hatch).** Anything addressing Quasar's
internal DOM, which `.classes()` on a NiceGUI element cannot reach:
`q-field__control` (boxed inputs incl. the compact `leg-row` variants), `q-field__label`/
`q-field__native` colors, `q-tab*`, `.nicegui-expansion-content` gap override, and the
teleported `.strat-menu-navy` menu (mounts on `<body>`). This is roughly half of today's
`DASHBOARD_CSS`; the other half (cards, buttons, eyebrow) becomes tokens.

## Migration approach (chosen: A)

**A — Foundation → shared helpers → screens.** Build the token module + convert the nav
shell first (the "top-level menu" the user asked to start with), then the shared
`pages/options/*` helpers (imported by ~10 screens, so converting them propagates), then
screens leaf-by-leaf grouped by shared structure. Honors the requested ordering and
front-loads the highest-leverage work. (Rejected: B vertical-slices — re-touches shared
helpers repeatedly; C mechanical-sweep — big risky diffs, no per-screen verification.)

**Every phase ends with the same gate:** `cd webgui && ..\.venv\Scripts\python -m
pytest -q` green **and** a browser verification (preview screenshot of the converted
screen against the pre-conversion look — convert + light polish means it should look the
same or cleaner, never broken). Each phase is independently shippable; safe to stop
between phases.

**Per-screen method — convert by logical group.** Within a screen, convert one logical
UI group at a time, in this order: (1) page wrapper / header strip → (2) primary cards →
(3) tables → (4) detail / chart panels → (5) action bar / footer. Screenshot after each
group if the screen is large. Replace `.style("k:v")` with the Tailwind equivalent
(scale utility, or `[...]` arbitrary value if exact); replace `.classes("calc-card")` +
CSS rule with `.classes(CARD)`.

## Phases

### Phase 0 — Foundation: the token vocabulary
- New token constants in `webgui/pages/options/theme.py` (`PAGE`, `CARD`, `EYEBROW`,
  `LABEL`, `MUTED`, `BTN`, `BTN_PRIMARY`, `STRATEGY_BTN`, …) encoding the canonical
  palette as Tailwind arbitrary-value strings.
- Split `DASHBOARD_CSS`: keep **only** the Quasar-internal rules in a slimmed
  `ui.add_css` block (renamed e.g. `QUASAR_INTERNAL_CSS`); delete the card/button/eyebrow
  rules now covered by tokens.
- Unit-test the token strings exist + are non-empty (cheap guard against accidental
  deletion). **No visual change** — Calculator/Simulator still render identically.

### Phase 1 — Top-level menu / shell  ← *"top-level menu item first"*
- `webgui/main.py`: the drawer nav, `_NAV_CSS`, header strip, proxy-down banner, count
  badges, active-pill / hover states → Tailwind tokens + `.classes()`.
- Keep in `ui.add_css` only the `.nicegui-expansion-content` gap override (Quasar
  internal). Everything else (link padding, badge alignment, active pill) → utilities.
- Verify: nav renders, groups expand, badges show, active highlight correct.

### Phase 2 — Shared Options helpers
- `pages/options/`: `theme.py` consumers, `detail.py` (the collapsible Trade detail
  panel reused by every signal table), `header.py` (quotes/VIX/sentiment strip),
  `leg_editor.py` (compact leg table — its `leg-row` Quasar-internal bits stay in the
  escape hatch; layout/spacing → utilities), `strategy_menu.py`, `overlay.py` (the
  full-screen wait overlay → Tailwind `fixed inset-0` etc.).
- Converting these propagates partial conversion into all ten Options screens.

### Phase 3 — Options screens *(split into 3a/3b/3c by shared structure)*

**Phase 3a — Signal-table screens** (all mount the shared `detail.py` panel; mostly
`ui.table` + per-row action slots): **Scanner**, **Swing**, **Captured**, **Paper
Trades**, **Paper Portfolio**, **Rescue**. Per-page CSS to fold into tokens / escape
hatch: `SCAN_CSS`, `PAPER_CSS`, `CAPTURED_CSS`, `_RESCUE_CSS` (table sticky-header /
compact-column rules are Quasar-internal → escape hatch; tints/badges → utilities).

**Phase 3b — Builder / analytic screens** (share `leg_editor` + `strategy_menu` + the
`theme` heavily; already on `.calc-v2`): **Calculator**, **Simulator**. Fold `CALC_CSS`;
swap `.calc-card`/`.cv2-btn*` usages to tokens; keep tabs/field internals in the escape
hatch. (Biggest `.style()` concentration after Phase 2.)

**Phase 3c — Chart-heavy standalone screens**: **Gamma**, **Expected-Move**. Convert the
page chrome (controls, status bar, panel wrappers) to Tailwind; **Highcharts option
dicts stay untouched** (out of scope). `EXPLAIN_CSS` belongs to the standalone Explain
*document* (out of scope) — leave it; only the in-app page chrome converts.

### Phase 4 — Trade  (`trade.py` — 10 `.style()` calls)
Groups: header → the **Position / Investor / Markov** verdict row (3 equal cards) →
MTF / momentum / sector cards → Fundamentals card → swing-evidence + legacy expanders.
Uses `DASHBOARD_CSS` today (`.calc-v2`) → switch to tokens; `DRIVER_CSS`-style page CSS
n/a here.

### Phase 5 — Sentiment + Rotation  (`sentiment.py` 45 `.style()`, `sentiment_rotation.py` 13)
Highest `.style()` count in the app. Groups: gauges row (sentiment + trend) →
component table / 2×2 tiles → 30-day history expansion → full-width sector table (the
inline `ui.add_css('''…''')` block at `sentiment.py:448` → tokens + escape hatch for the
`.sent-sectors` table internals) → rotation page (quadrant table + RRG chrome).

### Phase 6 — Portfolio  (`portfolio.py` 3 `.style()`)
Groups: proxy/stream status bar → Holdings / Sectors / Performance tabs → suggestion
detail pane.

### Phase 7 — Driver  (`driver.py` 2 `.style()`, `DRIVER_CSS`)
Groups: control bar (Enable/Disable/STOP/Run-now) → day-P&L progress + open positions →
decision-log audit → performance scorecard card. Fold `DRIVER_CSS` into tokens / escape
hatch.

### Phase 8 — Utility pages
**EOD** (in-app `render()` shell only — the exported `summary/detail.html` + `EOD_CSS`
are out of scope), **Status** (28 `.classes()`, 3 `.style()`), **Settings**,
**Terminate**, **Manuals**. Small, mostly already `.classes()`.

## Risks & mitigations
- **Quasar-internal styling can't move to utilities.** Mitigated by the explicit escape
  hatch — the design names exactly what stays (`q-field__control`/`q-tab*`/expansion
  gap/teleported popup). The goal is *minimal* `ui.add_css`, not zero.
- **Shared helpers are load-bearing (Phase 2 regressions hit many pages).** Mitigated by
  doing them as their own phase with full screenshot verification before any screen phase
  depends on them; they're also the most-tested modules.
- **Exact-pixel `.style()` → arbitrary values reads ugly** (`w-[37px]`). Accepted —
  convert + light polish lets us round to Tailwind scale where it doesn't change the
  look; only keep `[...]` when an exact value matters.
- **Visual regression.** Every phase gated on a before/after screenshot; convert + light
  polish means same-or-cleaner, never different-for-its-own-sake.

## Acceptance (per phase)
- `webgui` pytest suite green.
- Browser screenshot of each converted screen matches (or improves on) the prior look.
- `grep` shows the phase's files free of `.style(`/inline-`style=`; any surviving
  `ui.add_css` content is Quasar-internal only.
- `CLAUDE.md` "UI styling standard" status line + the App-theme section updated as
  tokens replace `DASHBOARD_CSS`.

## Out of scope (explicit)
Standalone `HTMLResponse` documents (EOD exports, Gamma Explain/Analyze infographics),
Highcharts option dicts, and any non-`webgui` app folder.
