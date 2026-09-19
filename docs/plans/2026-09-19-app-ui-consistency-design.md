# One look and one behaviour for every screen (design)

**Date:** 2026-09-19
**Scope:** every NiceGUI route in `webgui/` (the private app and the public live
screens, which render the same page modules), plus Settings → Appearance.
**Decisions (the operator's, 2026-09-19):**

| Question | Answer |
|---|---|
| How far does "the same" go? | **One style, keep the charts.** Every screen shares background, panels, font, header, fields, buttons, loading/empty states, tables and dialogs. Charts, heatmaps, gauges and every data-driven colour stay. |
| Which style? | **A — the navy dashboard** (`theme.py` `PAGE`/`CARD`/`BTN*`, IBM Plex, `config/theme.toml` `[palette]`). |
| Where do buttons sit? | **Page actions top-right, the Go button right after the fields.** |
| Where do selected-row actions go? | **In the row's detail panel.** |
| And the per-row icon buttons (Send to Calculator / Paper trade / Expected Move)? | **Into the panel too** — one rule, and the icon column goes. A page with no panel (Income Window) keeps its one per-row button. |
| Does every page show a title? | **Yes — one line, shared with the page actions.** |
| Settings → Appearance | **Update it to reflect the new standard.** |

## Problem — what the screens do today (measured 2026-09-19)

A read-only survey of all ~35 routes (five parallel inventories, file:line
evidence) plus screenshots of the 14 public screens found:

- **Seven visual families.** Navy dashboard (Scanner, Opportunity Board, Flow,
  Shares, Income, Simulator), `[console]` (Sentiment, Desk, Symbol), `[calc]`
  (Calculator), `[macro]` (Macro Board), `[sectors]` (Sector & Industry),
  `[rotation]` (Bull/Bear, Rotation, RRG, Momentum), and the Trade "Signal Desk"
  `terminal_theme`. A dozen pages (Status, Driver, Portfolio, Paper, Captured,
  Account, Rescue, Gamma, Expected Move, EOD, Manuals, Terminate) use default
  Quasar cards and fields with no theme scope at all — the public Gamma screen is
  a grey unthemed page. Five web fonts: IBM Plex, Rajdhani, JetBrains Mono,
  Manrope, Instrument Sans.
- **Four places action buttons sit.** Top-right aligned to the table (Scanner,
  Captured), top-right across the page (Account), below the table left-aligned
  (Paper Ledger), inside result cards (Rescue); plus "leftmost" (Status,
  Portfolio, EOD) and "below the content" (Trade Plan). The primary action is
  rightmost on one page, in the middle on two, third of five on another.
- **Confirm dialogs order their buttons both ways.** Confirm-then-Cancel in a
  left-aligned row (Paper, Captured, Account, the Paper/Open hand-off dialogs,
  Settings); Cancel-then-Confirm right-aligned (Rescue, Configuration, Terminate,
  Driver). Cancel renders "CANCEL" (Quasar default caps) beside mixed-case
  confirms. A destructive Reset is danger-red in Settings and primary-blue in
  Configuration.
- **Destructive actions without a confirm.** Paper Ledger's Delete and "Delete all
  closed"; the Status page's Restart.
- **Five Symbol-field behaviours.** `bind_symbol_load` (Finder, Expected Move,
  Rescue, Symbol); hand-written Enter + focusout with an unseeded dedup, so tabbing
  through the default SPY loads it (Calculator, Simulator); `blur` (which the
  repo's own comments say never reaches the listener) + Tab + Enter-always, no
  select-all (Trade Signal Desk); a pick-list that loads on selection (Gamma).
  Labels are floating on some pages, an eyebrow above on others, placeholder-only
  on others, and both at once on one Settings field.
- **Three save models under one Settings menu.** General saves instantly,
  Appearance needs Save, Configuration has a staged footer.
- **Freshness in five formats and three time zones** (CT chip, ET eyebrow,
  machine-local status line, date-only eyebrows, none at all), and a permanent
  "STREAMING" dot on the Macro Board that nothing turns off.
- **Loading shown five ways** (full-screen overlay, region spinner, a local
  `ui.spinner`, a toast, nothing) — and on five Trend & Sentiment pages and Rescue
  the region spinner is mounted on a container the repaint `clear()`s, so it is
  most likely deleted on the first paint and never shows.
- **Three green/red sets for profit and loss** (`#66bb6a/#ef5350`,
  `#34d399/#f87171`, `#2e7d32/#c62828`) beside the theme's own `TXT_POS/TXT_NEG`,
  and zero is grey on one page, uncoloured on another, `#9e9e9e` on a third.
- **Settings → Appearance reaches about half the app.** Its "3D buttons" tab
  edits eight colours of which one (`red_mid`) is read by anything; the page-scoped
  palettes are not in it at all; and its menu `accent` does not reach the active
  nav pill, tab fills or icon, which are hard-coded in `main._NAV_CSS`.

## The standard

### Surface and type
- The **shell** paints the navy page background once (the `[palette]`
  `page_bg1/2/3` radial) on the content area. A page holds `CARD`s directly — no
  second bordered `PAGE` panel inside the page.
- **IBM Plex Sans everywhere**, tabular figures (`[typography] numeric`). Mono is
  for code and logs only. Rajdhani, JetBrains Mono, Manrope and Instrument Sans are
  no longer loaded.
- **Sentence case** for labels, buttons, headings and table headers. No
  `REFRESH` / `ADD LEG` / `RATE MY TRADE`. Standard trader acronyms and tickers
  stay as they are (see the "whole words" rule).
- **Full width.** Form pages (Settings, Manuals, Stop All Services) cap at a
  readable width.

### Header line — every page
`[Title]  ·····  [Updated 10:42 CT] [secondary actions] [primary action]`

- Title on the left (`[typography] titles`, `LABEL` colour). One line; no
  description line — the page's hover help (`page_help.py`) already explains it.
- **Freshness stamp** on the right for every page that shows service data:
  `Updated 10:42 AM CT` (the view's `:ts` side key — when its publisher last
  confirmed it current); `Waiting for data` before the first read. Only a view
  published on a **schedule** can turn amber (`Stale · updated 10:00 AM CT`,
  past the nav badge's own `alerts.stale_after` threshold) — an on-demand or
  once-a-day view is never called stale, because its age says nothing. Central
  time everywhere. Only a stamp the page can back — no static "live" claims.
- **Page actions** after it — actions on the whole page (Refresh, Run scan, Run
  entry cycle, Generate). Primary rightmost.
- A page with a Symbol control bar has **no header Refresh**: its Load button is
  the refresh.

### Control bar — pages with fields
`CARD`: `[label↑ field] [label↑ field] … [Go] status text`

- Fields left to right, each with a **small label above it** (sentence case,
  `EYEBROW`). No floating labels, no placeholder-only fields; a placeholder is only
  an example value.
- The **Go button right after the last field**, status text right after it.
- Less-used fields in a collapsed **"More options"** row under the bar, styled the
  same.

### Field behaviour
- **Symbol field (one helper, everywhere a ticker is typed):** uppercase; the whole
  ticker selected on click or tab-in; **Enter loads**; **tab-out loads only when the
  symbol changed** (dedup seeded from the starting value); the Go button **always**
  reloads; a symbol written from code (a hand-off) marks itself loaded. An unknown
  ticker is reported **under the bar**, not in a toast.
- **Filters on what is already on screen** (dropdowns, toggles, switches) apply
  immediately.
- **Inputs to a slow or paid request** (scan settings, "More options") take effect
  on Go or Enter. The one exception is the Calculator's live re-price (0.3 s
  debounce), which is that page's purpose.
- **Number fields** validate on blur; the error shows in red under the field and the
  Go button is disabled until it is fixed.
- **Enter in any text field = Go.**

### Buttons
| Kind | Look | Use |
|---|---|---|
| Primary | solid accent fill | the one main action of an area |
| Secondary | navy fill, hairline border | everything else |
| Danger | red outline; **solid red only inside its confirm dialog** | destructive actions |
| Quiet | text only | small links ("Why no trade?", "Change") |

- Sentence case, verb first. An icon plus label for header and Go buttons;
  icon-only only for per-row buttons, always with a tooltip.
- One word for re-reading a page: **Refresh** (retires Reload, "Refresh now",
  `REFRESH`).
- A button that starts work shows **its own spinner and stays disabled** until the
  result lands or its timeout passes — no double submits, no orphan status labels.

### Rows and the detail panel
- Row click **selects** the row (accent left edge on the row) and opens the
  **360 px detail panel on the right**. The row's actions (Close trade, Delete,
  Analyze, Reprice, and the sends — Calculator, Paper trade, Expected Move) sit
  in the **panel footer**, right-aligned: danger leftmost, primary rightmost. No
  "Click a trade row first" warnings are possible, and the per-row icon column
  goes.
- On pages without a panel, the **symbol cell is the link** (to `/symbol`, or the
  page the row belongs to) and row click does nothing. Flow Alerts' row click
  (which today jumps to Dealer Positioning) becomes a symbol link.
- **Tables:** dense rows, sticky header (the existing app-wide `TABLE_CSS`),
  numbers right-aligned, every data column sortable, the default sort stated in the
  column. Paging: none under ~500 rows; the Strategy Finder keeps its server paging.
- **One profit/loss colour rule:** `TXT_POS` / `TXT_NEG`, muted for zero.

### Loading, empty states, dialogs, toasts
- **Loading:** first load and every refresh show a spinner over the **region**
  being replaced (`busy.py`), mounted so the repaint cannot delete it. The
  full-screen overlay stays only where loading a new symbol invalidates every
  control on the page: Calculator, Simulator, Symbol.
- **Empty state:** one component, muted text centred in the region. "Nothing
  published yet" (a feed that has said nothing — `copy.py` `WAITING_*`) and
  "nothing to report" (a feed that is fine and quiet) stay worded differently.
- **Confirm dialog:** title + one sentence; buttons right-aligned, **Cancel then
  confirm**; the confirm is primary, or danger for a destructive action. Enter
  confirms, Esc cancels. **Every destructive action confirms.** An information
  dialog has a close ✕ top-right and no footer.
- **Toasts** only report the outcome of an action (and the shell's app-wide
  alerts). One position, a type always set, fixed timeouts. Validation is shown
  inline; "Refreshing — the page updates when…" toasts go, because the spinner
  says it.

### Charts keep their data colours
Highcharts figures, the Gamma plasma heatmap, the Flow panels, the sector heat
ramp (`sector_heat`), the quadrant hues (`rotation_view` palette root), the regime
colours, the Macro Board risk-on/off tile colours, score/heat zone maps. The
surfaces **around** them become navy; the encodings do not change.

## How it is built

### `webgui/pages/ui_kit.py`
A Tier-1 module importing only `nicegui`, `pages.options.theme`, `pages.busy` and
`pages.options.inputs` (so it sits inside the Tier-1 allow-list and is safe for the
public process). Pure decisions live in module-level functions, unit-tested
without a browser; widget builders stay thin.

| Piece | Role |
|---|---|
| `page(width="full"\|"form")` | the page column (no second frame) |
| `header(title)` → handle | title, freshness slot, `.actions` row; `.set_updated(ts, stale_after)` |
| `freshness_text(ts, now, stale_after)` | PURE: the stamp text + state (`fresh` / `stale` / `waiting`) |
| `control_bar()` | the field card; `field(label)` wraps any control with its label above |
| `symbol_field(label, value, on_load)` | the one Symbol behaviour (`select_all_on_focus` + `bind_symbol_load` + inline error slot) |
| `button(text, kind, icon, on_click)` | the four kinds; `.busy(until=…)` for the self-spinner |
| `region(text)` → `(outer, content)` | spinner on `outer`, repaints clear `content` |
| `detail_panel()` → handle | the 360 px right panel with a `.footer` action row |
| `table(columns, rows, …)` | table defaults, numeric alignment, sortable, selected-row class |
| `empty(text)` | the empty-state line |
| `confirm(title, body, confirm_text, on_confirm, danger=False)` | the one confirm dialog |
| `toast(kind, text)` | the one notify call |

### Tokens (`pages/options/theme.py`)
- Add: `BTN_QUIET`, `ROW_SELECTED`, `FIELD_LABEL`, `TXT_STALE` (the warning colour).
- The boxed field CSS in `QUASAR_INTERNAL_CSS` becomes **app-wide**, injected once by
  the shell; the `.calc-v2` scope class is dropped from pages as they migrate.
- `BTN_3D` / `BTN_3D_DANGER` (legacy aliases of primary / danger) are removed once
  no page uses them.
- `[buttons_3d]` is retired. Its one live key (`red_mid`, the solid danger fill)
  becomes `[palette] danger`; `load_theme` reads an old `[buttons_3d] red_mid` from
  a local override as the fallback for `danger`, so a saved override keeps working.
  No other TOML section moves: the Appearance groups below are a **display mapping
  over existing keys**, not a restructure of the file.
- `main._NAV_CSS`'s active pill, tab fills and active icon are built from the theme
  accent (raw CSS may interpolate any value — the Tailwind JIT limit does not
  apply), so the menu accent reaches them.

### Page-scoped palettes
`[console]`, `[macro]`, `[sectors]`, `[rotation]`, `[calc]` and
`pages/terminal_theme.py` lose their background, text, font and button values as
their pages migrate; only data-colour keys survive (regime colours, heat/quadrant
hues, flow/gamma chart colours, macro tile colours). Their `*_FONT_HEAD_HTML` and
page `ui.add_css` blocks go. `EOD_CSS` and the Deep Dive / AI Query standalone
pages take the navy background, IBM Plex and the button look in their own inline
CSS (they are raw documents, outside the kit).

### Guard tests
- **`webgui/tests/test_ui_kit_guard.py`** — AST over every page module: fails on a
  direct `ui.button(`, `ui.dialog(`, `ui.notify(`, `ui.table(` or a Google-font
  `<link>` other than the app font, outside `ui_kit.py`. It starts with an
  **allow-list naming every current offender by file and count**; each phase
  deletes its pages' entries, and the list must be empty at the end (the pattern
  `test_no_inline_style.py` used for the Tailwind migration).
- **Dialog order** — `confirm` is the only dialog builder with footer buttons, and
  its test pins Cancel first.

## Settings → Appearance

- **Its own Settings tab**: General · Appearance · Configuration (it is a large
  editor in a narrow card today).
- **Groups rebuilt around the standard:** Surfaces · Text · Fields (fill, border,
  text, focus) · Buttons (primary, secondary, danger — replaces "3D buttons") ·
  Status colours (profit, loss, warning, neutral; warning is also the Stale colour)
  · Charts · Type · Menu.
- **A live preview beside the editor**, built from the kit: a header line with an
  Updated stamp and Refresh, a control bar with a Symbol field and Load, all four
  button kinds, a two-row table with one row selected, an empty-state line and a
  sample toast. It repaints as colours are picked, before saving — honest, because
  every screen is now built from these pieces.
- **One edit restyles every screen**, including the menu accent (now reaching the
  active nav pill, tab fills and icon).
- **Saving matches Configuration:** a sticky Discard / Save changes footer, then the
  same "Restart now" banner. Reset to shipped values goes through `confirm` (Cancel,
  then a danger Reset). The copy stops claiming it saves to `config/theme.toml` — it
  writes the `config/local/` override.

## Rollout

| Phase | Scope |
|---|---|
| 0 | kit, tokens, app-wide field CSS + background, menu accent, guard test with allow-list, the new Appearance tab |
| 1 | Options boards: Scanner, Paper Ledger, Captured, Paper Account, Shares, Income, Opportunity Board, Flow Alerts, Rescue (+ `handoff` dialogs, `detail` panel footer) |
| 2 | Options tools: Calculator, Simulator, Strategy Finder, Expected Move, Dealer Positioning (+ `entry_panel`, `leg_editor`) |
| 3 | Trend & Sentiment: Sentiment, Bull/Bear, Sectors, Rotation, RRG, Momentum |
| 4 | Desk, Symbol, Macro Board |
| 5 | Trade (Overview, Evidence, Rank Board, Trade Plan, Deep Dive, AI Query), Claude Trades, Portfolio |
| 6 | Status, Settings General + Configuration, Stop All Services, EOD, Manuals; then CLAUDE.md (theme section + page-scoped palette notes), `docs/webgui-routes.md`, the User Guide + Reference Guide, `page_help.py`, CHANGELOG |

Each phase ships on its own and ends with its pages' allow-list entries deleted.

### Verification
- **Tests:** the webgui suite and the guard; compare the failing **set**, not the count.
- **No dev environment:** each phase is checked in the local page harness (fake Bus +
  real `options_svc` handlers, per the 2026-09-11 Simulator precedent) on Windows,
  with before/after screenshots of every page in the phase, a keyboard check of the
  Symbol field (Enter, Tab, select-all) and one confirm dialog.
- **After promote** (15:25–16:15 CT — a promote stops the whole target): the 14
  public screens can be checked directly; the private pages need the operator to
  click through (the app is behind a login Claude does not enter).

## Bugs fixed on the way (found by the survey)
- The region spinner on Sentiment, Sectors, Rotation, RRG, Momentum and Rescue is
  mounted on a container the repaint clears (read from code; confirm in the harness
  before claiming it).
- Paper Ledger Delete / Delete all closed and Status Restart have no confirm.
- The Trade Signal Desk never shows analysis `errors`, listens on `blur`, and its
  30 s spinner backstop is shorter than a ~96 s analysis (the unrouted `trade.py`
  learned 300 s).
- The Trade Plan note is built outside its container and probably accumulates per
  repaint; the Overview rail marker's `left-[x%]` classes pile up.
- The Macro Board's static "STREAMING" dot.
- Captured's spinner says "Repricing…" for Reload; Simulator's fetch timeout leaves
  "Loading chain…" showing; Rank Board's Rebuild spinner is never hidden by the
  board update.

## Trade-offs
- The August/September redesigns (console, heat grid, rotation board, Macro
  instrument, Calculator, Signal Desk) lose their own surfaces and fonts; their
  layouts and data colours stay.
- The public live screens change with the private ones (same modules).
- The three tracked Simulator gallery images (`deploy/site/assets/shots/image16-18`)
  do not regenerate and must be recaptured by hand.
- Tests that pin exact class strings will churn.
- `trade.py` (the old single-page Trade Analyzer) is unrouted and only a helper
  library for the Signal Desk; its widgets are not migrated, only its helpers kept.
