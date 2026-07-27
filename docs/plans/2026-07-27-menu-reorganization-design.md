# Menu reorganization — rail promotions, plain-language renames, workflow ordering

**Date:** 2026-07-27
**Scope:** `webgui` navigation only. No routes, cache keys, commands, or engine
vocabulary change.

## Problem

Three distinct issues, found by examining the nav lists in `webgui/main.py`
against the pages behind them:

1. **The Calculator and Gamma pages are buried in a 10-tab strip** despite being
   two of the most-used tools. The user wants them promoted to the left rail
   (the pattern Matrix already uses), and "Gamma" renamed to something a reader
   understands without knowing the greek.

2. **Two name collisions actively mislead.**
   - The Scanner page has its own **Swing** subtab (0-DTE / Swing / Directional),
     so the menu item named "Swing Scanner" is *not* where the Scanner's swing
     signals live. `/options/swing` is also no longer swing-specific — its own
     docstring calls it a *multi-strategy* scanner (directional, spreads,
     neutral) for one symbol.
   - "Paper Trades" and "Paper Portfolio" are backed by **different databases**
     (`trades.db` ledger vs `paper_account.db` engine account) that settle
     expiration differently. The labels give no way to tell which one a
     "Send to Paper" button writes to.

3. **The Options tabs aren't in workflow order** — the two finding tools
   (Scanner, Swing) are separated by three position-tracking pages.

Plus two lower-severity items: "Terminate" is a vague name on an action that
stops the entire local stack, and "Matrix" is opaque next to the plain-language
names the rest of this change moves toward.

## Final structure

```
RAIL (9 items)                TAB STRIP
├ Options ──────────────────  Market Scanner · Strategy Finder · Simulator ·
│                             Expected Move · Captured Signals · Paper Ledger ·
│                             Paper Account · Rescue
├ Calculator
├ Dealer Positioning
├ Opportunity Board
├ Market Trend & Sentiment ─  Market Dashboard · Sentiment · Sector & Industry ·
│                             Sector Rotation · RRG
├ Trade Analyzer
├ Portfolio
├ Claude Trades
└ More ─────────────────────  EOD Report · System Status · Settings ·
                              Stop All Services · User Manuals
```

### Renames

| Route | Was | Now | Why |
|---|---|---|---|
| `/options/gamma` | Gamma | **Dealer Positioning** | Says what it tells you — where dealers must hedge. Matches the page's own help text. |
| `/` | Scanner | **Market Scanner** | Disambiguates from the single-symbol scanner. |
| `/options/swing` | Swing Scanner | **Strategy Finder** | It scans strategy *families* for one symbol; "swing" is no longer what distinguishes it. |
| `/options/paper` | Paper Trades | **Paper Ledger** | Names the actual backing store (the flat ledger). |
| `/options/portfolio` | Paper Portfolio | **Paper Account** | Names the actual backing store (the engine account). |
| `/options/matrix` | Matrix | **Opportunity Board** | Says what it's for — spotting opportunities across the watchlist. |
| `/terminate` | Terminate | **Stop All Services** | Vague name on a destructive action. |

### Moves

- **Calculator + Dealer Positioning → `OPTIONS_RAIL`**, ordered
  Calculator → Dealer Positioning → Opportunity Board. Rendered by the existing
  `_nav_link` loop; no new rendering code.
- **Market Dashboard → `SENTIMENT_CHILDREN`**, first tab. It does the same
  "what is the market doing right now" job as Sentiment/Rotation/RRG, and
  folding it in drops a rail item.
- **Options tabs reordered** find → analyze → track → repair.

## Consequences worth naming

- **Calculator and Dealer Positioning become standalone pages** — no tab strip,
  and the breadcrumb changes from `Options · Gamma` to just `Dealer Positioning`.
  That is inherent to "main menu item, not subtab"; Matrix already made this
  trade.
- **The Market Trend & Sentiment group's landing page changes** from `/sentiment`
  to `/market`, because `_nav_group_link` navigates to `children[0]`. Reasonable
  (the dashboard is the fastest-glance page) but it is a behavior change.
- **Drawer items go 8 → 9.** Three groups + three rail pages + three flat pages.
  `test_shell.py`'s icon-distinctness test pins the count and must be bumped.
  `calculate` and `stacked_line_chart` collide with no existing rail icon.
- **Routes are unchanged**, so every cross-page handoff (Send to Calculator,
  Expected Move, `/options/explain`, `/options/analyze`, the briefing history
  links) keeps working with zero edits.
- `_NAV_LABEL` is derived from these lists, so browser tab titles and the
  2-second hover tooltips follow the renames automatically. `_TAB_COLOR` is keyed
  by route, so the favicon colors are untouched (their trailing comments name the
  old labels and need updating).

## Explicitly NOT renamed

The financial vocabulary stays. This change touches menu labels, not the domain:

- "gamma flip", "GEX", and the Charm / DEX / Vanna / Flow / Term view names.
- The **Gamma Explain** and **Gamma Analysis** report documents — those titles
  are also the persisted briefing history's identity (`gamma_briefings.db`).
- Cache keys (`cache:options:gamma*`, `cache:options:matrix`), command names,
  and page module filenames (`gamma.py`, `swing.py`, `paper.py`, `portfolio.py`).

Renaming any of those would be a data-layer change wearing a UI change's
clothes.

## Files touched

| File | Change |
|---|---|
| `webgui/main.py` | The five nav lists; `_TAB_COLOR` comments; the seven affected `_layout` title strings; the stale `MORE_CHILDREN` nesting comment; the `OPTIONS_RAIL` comment. |
| `webgui/page_help.py` | Seven guide headings, body cross-references to renamed pages, and the stale module docstring. |
| `webgui/pages/terminate.py` | The on-page `ui.label("Terminate")` heading. |
| `webgui/tests/test_shell.py` | Nav-list assertion (l.81), three breadcrumb assertions (l.381-388), the drawer-item count (l.488) and its comment. |
| `docs/manuals/user-guide/user-guide.md` | Page-name references (Swing Scanner ×4, Paper Portfolio ×3, Paper Trades ×4, Terminate ×4, Matrix ×1, plus Gamma *menu* references), then rebuild via `build_docs.py`. |
| `CLAUDE.md` | Route table + the webgui nav-structure section. |

The technical and API reference manuals are **not** touched — their "Gamma"
mentions are the engine/domain term, which is deliberately unchanged.

## Two stale comments fixed on the way through

- `main.py:251` claims Settings is "a nested sub-group (its children render
  indented beneath it)". That describes the retired expandable drawer; in the tab
  strip `MORE_CHILDREN + SETTINGS_CHILDREN` renders User Manuals as a flat peer.
- `page_help.py:3` says help appears "in a hover tooltip on the `?` button in the
  header". That button is gone; help now hangs off nav tabs and drawer items via
  `_help_tooltip`.

## Testing

The changed surface is pure data (label/order lists) plus doc text, so the
existing `webgui` suite is the gate:

- Update the four assertions in `test_shell.py` that pin the old labels, counts,
  and breadcrumbs.
- `test_page_help.py` already iterates `OPTIONS_CHILDREN + OPTIONS_RAIL` and
  asserts every route has a guide — it passes unchanged and proves no page lost
  its help when moved between lists.
- Full `webgui` suite green (861 baseline), then a live browser pass over the
  rail, both promoted pages, the reordered Options strip, and the sentiment group
  landing on `/market`.

## Watch-item (no action)

Three of nine rail items are now Options-domain pages sitting as peers of the
Options group itself. Fine at three; if more get promoted the rail stops meaning
"one row per app area."
