# Menu Reorganization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Promote Calculator and Gamma to the webgui left rail, rename seven menu
items to plain language, fold Market Dashboard into the sentiment group, and
reorder the Options tabs by workflow.

**Architecture:** The webgui nav is pure data — five module-level lists in
`webgui/main.py` that the drawer and tab strip render from, plus `_NAV_LABEL`
(browser tab titles) and `_TAB_COLOR` (favicons) derived from them. This change
edits those lists and the user-facing text that names them. **No routes, cache
keys, commands, page modules, or engine vocabulary change**, so every cross-page
handoff keeps working untouched.

**Tech Stack:** NiceGUI, pytest. NOTE: this is a git worktree — the venv lives in
the MAIN checkout, so use the absolute
`"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`, not `../.venv/...`.

**Design:** `docs/plans/2026-07-27-menu-reorganization-design.md`

---

## Target structure (the whole change in one picture)

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

**Do NOT rename** (these are the financial domain, not menu labels): "gamma
flip", "GEX", the Charm/DEX/Vanna/Flow/Term view names, the Greek names
Delta/**Gamma**/Theta/Vega/Rho, the "Gamma Explain" / "Gamma Analysis" report
documents, cache keys (`cache:options:gamma*`, `cache:options:matrix`), command
names, or page module filenames (`gamma.py`, `swing.py`, `paper.py`,
`portfolio.py`).

---

## Task 1: Update the shell tests to the target structure (RED)

The nav lists are data, so the existing `test_shell.py` assertions ARE the spec.
Change them first and watch them fail.

**Files:**
- Modify: `webgui/tests/test_shell.py:77-86`, `:378-388`, `:472-498`

**Step 1: Update the group-membership test**

`webgui/tests/test_shell.py:77-86` currently reads `_group_children("/options/gamma")`
— but Gamma becomes a rail page with no group, which would return `None` and
raise `TypeError` on the `in` check. Replace the whole function with:

```python
def test_group_children_maps_routes_to_their_group():
    """The top tab strip shows the active route's group; flat + rail pages have none."""
    import main
    opts = main._group_children("/options/rescue")
    assert ("/", "Market Scanner", "radar") in opts                # Options group
    assert main._group_children("/sentiment/rotation") == main.SENTIMENT_CHILDREN
    assert main._group_children("/market") == main.SENTIMENT_CHILDREN  # folded in
    more = main._group_children("/manuals")                        # Settings child
    assert ("/eod", "EOD Report", "summarize") in more             # merged into More
    assert main._group_children("/trade") is None                  # flat page — no strip
    assert main._group_children("/driver") is None
    # Rail pages are standalone: promoted OUT of the Options tab strip.
    for route, _label, _icon in main.OPTIONS_RAIL:
        assert main._group_children(route) is None, route
```

**Step 2: Update the breadcrumb test**

`webgui/tests/test_shell.py:378-388` — replace the three changed assertions:

```python
def test_breadcrumb_parts_grouped_and_flat():
    import main
    # Grouped page → (group label, page label)
    assert main.breadcrumb_parts("/") == ("Options", "Market Scanner")
    assert main.breadcrumb_parts("/sentiment/rotation") == (
        "Market Trend & Sentiment", "Sector Rotation")
    assert main.breadcrumb_parts("/market") == (
        "Market Trend & Sentiment", "Market Dashboard")
    assert main.breadcrumb_parts("/status") == ("More", "System Status")
    # Flat single page → (page label, "") — no "· Tab"
    assert main.breadcrumb_parts("/trade") == ("Trade Analyzer", "")
    # Rail pages read as standalone sections, same as flat pages.
    assert main.breadcrumb_parts("/options/gamma") == ("Dealer Positioning", "")
    assert main.breadcrumb_parts("/options/calculator") == ("Calculator", "")
```

**Step 3: Update the drawer-icon test's count and comment**

`webgui/tests/test_shell.py:472-498` — the docstring says "the 8 drawer items
(3 groups + the Matrix rail item under Options + FLAT_NAV)". Update the prose to
"the 9 drawer items (3 groups + 3 OPTIONS_RAIL pages + 3 FLAT_NAV pages)" and
change the pinned count:

```python
    assert len(items) == 9, f"expected 9 drawer items, got {len(items)}: {items}"
```

**Step 4: Run the tests to verify they fail**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_shell.py -q
```

Expected: FAIL — assertions reference labels (`Market Scanner`, `Dealer
Positioning`) and a count (9) that `main.py` does not yet produce.

**Step 5: Commit the RED tests**

```bash
git add webgui/tests/test_shell.py
git commit -m "test: pin the reorganized nav structure (red)"
```

---

## Task 2: Rewrite the nav lists (GREEN)

**Files:**
- Modify: `webgui/main.py:214-257` (the five lists), `:334-357` (`_TAB_COLOR`
  comments), `:1102-1270` (the affected `_layout` title strings)

**Step 1: Replace the nav list block**

`webgui/main.py:214-257` — replace `OPTIONS_CHILDREN` through `MORE_CHILDREN`
(leave `SETTINGS_CHILDREN` and `_NAV_GROUPS` as they are) with:

```python
# Options is a menu GROUP; each child is a tab in the strip. Ordered by the
# trading workflow: find → analyze → track → repair. (route, label, icon)
OPTIONS_CHILDREN = [
    ("/", "Market Scanner", "radar"),
    ("/options/swing", "Strategy Finder", "swap_vert"),
    ("/options/simulator", "Simulator", "science"),
    ("/options/expected-move", "Expected Move", "candlestick_chart"),
    ("/options/captured", "Captured Signals", "bookmark"),
    ("/options/paper", "Paper Ledger", "request_quote"),
    ("/options/portfolio", "Paper Account", "account_balance_wallet"),
    ("/options/rescue", "Rescue", "healing"),
]

# Market context is a menu GROUP: the macro tile board first (the broadest lens),
# then the sentiment reads. NOTE ``_nav_group_link`` navigates to children[0], so
# this group's RAIL item lands on /market. (route, label, icon)
SENTIMENT_CHILDREN = [
    ("/market", "Market Dashboard", "dashboard"),
    ("/sentiment", "Sentiment", "insights"),
    ("/sentiment/sectors", "Sector & Industry", "table_chart"),
    ("/sentiment/rotation", "Sector Rotation", "donut_large"),
    ("/sentiment/rrg", "RRG", "scatter_plot"),
]

# Standalone MAIN-MENU (rail) pages shown directly UNDER the Options group. Each
# is its own page with NO tab strip — deliberately NOT Options tab-strip entries.
# (route, label, icon)
OPTIONS_RAIL = [
    ("/options/calculator", "Calculator", "calculate"),
    ("/options/gamma", "Dealer Positioning", "stacked_line_chart"),
    ("/options/matrix", "Opportunity Board", "grid_on"),
]

# Flat top-level items (single-page apps). (route, label, icon)
FLAT_NAV = [
    ("/trade", "Trade Analyzer", "query_stats"),
    ("/portfolio", "Portfolio", "account_balance"),
    ("/driver", "Claude Trades", "smart_toy"),
]

# "More" is a menu GROUP for reports / diagnostics / config. Its tab strip is
# MORE_CHILDREN + SETTINGS_CHILDREN, so User Manuals renders as a flat PEER tab
# of Settings — the old indented sub-group is retired. (route, label, icon)
MORE_CHILDREN = [
    ("/eod", "EOD Report", "summarize"),
    ("/status", "System Status", "monitor_heart"),
    ("/settings", "Settings", "settings"),
    ("/terminate", "Stop All Services", "power_settings_new"),
]
```

Note this also fixes the stale "Settings is itself a nested sub-group (its
children render indented beneath it)" comment, which described the retired
expandable drawer.

**Step 2: Update the `_TAB_COLOR` trailing comments**

`webgui/main.py:334-357` — the colors are keyed by ROUTE and must not change;
only the trailing comments name the old labels:

```python
    "/": "#42a5f5",                       # Market Scanner — blue
    "/options/matrix": "#4dd0e1",         # Opportunity Board — cyan
    "/options/paper": "#66bb6a",          # Paper Ledger — green
    "/options/portfolio": "#26a69a",      # Paper Account — teal
    "/options/swing": "#ec407a",          # Strategy Finder — pink
    "/options/gamma": "#7e57c2",          # Dealer Positioning — deep purple
    "/terminate": "#b71c1c",              # Stop All Services — dark red
```

**Step 3: Update the affected `_layout` title strings**

These are documentation only — `_layout`'s `title` parameter is **unused** (the
browser tab title comes from `ui.page_title(_NAV_LABEL.get(active, ...))`) — but
leaving them stale misleads the next reader. Update these nine lines:

| Line | Was | Now |
|---|---|---|
| 1102 | `"Options · Scanner"` | `"Options · Market Scanner"` |
| 1109 | `"Options · Paper Trades"` | `"Options · Paper Ledger"` |
| 1123 | `"Options · Paper Portfolio"` | `"Options · Paper Account"` |
| 1130 | `"Options · Calculator"` | `"Calculator"` |
| 1137 | `"Options · Swing Scanner"` | `"Options · Strategy Finder"` |
| 1144 | `"Options · Gamma"` | `"Dealer Positioning"` |
| 1172 | `"Matrix"` | `"Opportunity Board"` |
| 1242 | `"Market Dashboard"` | `"Market Trend & Sentiment · Market Dashboard"` |
| 1270 | `"Terminate"` | `"Stop All Services"` |

**Step 4: Run the shell tests to verify they pass**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_shell.py -q
```

Expected: PASS (all of `test_shell.py`).

**Step 5: Commit**

```bash
git add webgui/main.py
git commit -m "feat(webgui): promote Calculator + Dealer Positioning to the rail, rename and reorder the menu"
```

---

## Task 3: Update the per-page help text

**Files:**
- Modify: `webgui/page_help.py`

`test_page_help.py` asserts every nav route has a guide. It passes unchanged
(it iterates `OPTIONS_CHILDREN + OPTIONS_RAIL + SENTIMENT_CHILDREN + FLAT_NAV +
MORE_CHILDREN`), which is exactly the proof that no page lost its help when moved
between lists — run it as the gate.

**Step 1: Fix the stale module docstring**

`webgui/page_help.py:3-4` says the help is "Shown in a hover tooltip on the ``?``
button in the header (see ``main._layout``)". That button no longer exists.
Replace those two lines with:

```
Shown in a 2-second hover tooltip on the nav drawer items and the top tab strip
(``main._help_tooltip``), keyed by the page's active route. Each value is Markdown
```

**Step 2: Rename the guide headings**

| Line | Was | Now |
|---|---|---|
| 12 | `**Scanner — the simple version**` | `**Market Scanner — the simple version**` |
| 27 | `**Swing Scanner — the simple version**` | `**Strategy Finder — the simple version**` |
| 56 | `**Gamma — the simple version**` | `**Dealer Positioning — the simple version**` |
| 125 | `**Matrix — the simple version**` | `**Opportunity Board — the simple version**` |
| 138 | `**Paper Trades — the simple version**` | `**Paper Ledger — the simple version**` |
| 158 | `**Paper Portfolio — the simple version**` | `**Paper Account — the simple version**` |
| 318 | `**Terminate — the simple version**` | `**Stop All Services — the simple version**` |

**Step 3: Fix the body cross-references to renamed pages**

- Line 29: `Like the Scanner, but you set the hunt parameters for one symbol over
  several days.` → `Like the Market Scanner, but you pick one symbol and it ranks
  every strategy family for it — directional, spreads, and neutral.`
- Line 144: `- Use it to test ideas from the Scanner without risk.` →
  `- Use it to test ideas from the Market Scanner without risk.`
- Line 149: `Scanner signals you're **tracking over time** …` →
  `Market Scanner signals you're **tracking over time** …`

**Step 4: Make the Ledger/Account distinction explicit**

This is the point of the rename — the labels now encode which store each page
reads, so say it. Add one line to each guide:

To `/options/paper` (after line 140, `A practice ledger of option trades — **no
real money**.`):

```
This is the **hand-kept ledger** — trades you sent here yourself. The automated
engine's positions live on **Paper Account**.
```

To `/options/portfolio` (after line 160, `The account behind the automated
paper-trading engine.`):

```
This is the **engine's own account** — it opens and closes positions on its own.
Trades you sent by hand live on **Paper Ledger**.
```

**Step 5: Run the help test**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_page_help.py -q
```

Expected: PASS — every nav route still resolves to a guide.

**Step 6: Commit**

```bash
git add webgui/page_help.py
git commit -m "docs(webgui): retitle the page guides for the renamed menu items"
```

---

## Task 4: Rename the Terminate page heading

**Files:**
- Modify: `webgui/pages/terminate.py:38`

**Step 1: Change the on-page heading**

The page's own `<h5>` still says "Terminate":

```python
    ui.label("Stop All Services").classes("text-h5")
```

**Step 2: Run the terminate tests**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_terminate.py -q
```

Expected: PASS. If an assertion pins the old heading string, update it to
`"Stop All Services"` — the label is the thing being renamed.

**Step 3: Commit**

```bash
git add webgui/pages/terminate.py webgui/tests/test_terminate.py
git commit -m "feat(webgui): rename the Terminate page heading to Stop All Services"
```

---

## Task 5: Full suite + lint gate

**Step 1: Run the whole webgui suite**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q
```

Expected: PASS, ~861 tests. If anything fails, it is a label assertion this plan
missed — read the failure, confirm it is pinning an OLD label, and update it to
the new one. **Do not** change a route, cache key, or behavior to make a test
pass.

**Step 2: Lint**

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/menu-reorganize-calculator-gamma-75d25a" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m ruff check webgui
```

Expected: clean.

**Step 3: Commit any fixes**

```bash
git add -u
git commit -m "test: update remaining assertions for the renamed menu items"
```

Skip this commit if steps 1-2 were already green.

---

## Task 6: Update the User Guide manual

The in-app **More → User Manuals** page serves generated HTML built from these
markdown sources. Left alone, the User Guide would tell users to click menu items
that no longer exist.

**Files:**
- Modify: `docs/manuals/user-guide/user-guide.md`
- Regenerate: `docs/manuals/user-guide/user-guide.html`

**Step 1: Update the navigation table (lines ~100-105)**

Replace the Options and More rows, and add the rail pages, so the table matches
the target structure at the top of this plan. The Options row becomes:

```
| **Options** (group) | Market Scanner · Strategy Finder · Simulator · Expected Move · Captured Signals · Paper Ledger · Paper Account · Rescue |
| **Calculator**, **Dealer Positioning**, **Opportunity Board** | Standalone main-menu pages, directly under Options |
| **Market Trend & Sentiment** (group) | Market Dashboard · Sentiment · Sector & Industry · Sector Rotation · RRG |
| **More** (group) | EOD Report · System Status · Settings · Stop All Services · User Manuals |
```

**Step 2: Update the section headings and references**

| Line | Change |
|---|---|
| 77 | `More → Terminate` → `More → Stop All Services` |
| 178 | `## Swing Scanner` → `## Strategy Finder` |
| 234 | `## Gamma` → `## Dealer Positioning` |
| 312 | `## Paper Trades` → `## Paper Ledger` |
| 342 | `## Paper Portfolio` → `## Paper Account` |
| 424 | `Scanner, Swing Scanner` → `Market Scanner, Strategy Finder` |
| 425 | `Scanner, Swing Scanner` → `Market Scanner, Strategy Finder` |
| 426 | `Scanner, Swing, Paper Trades, Captured, Calculator` → `Market Scanner, Strategy Finder, Paper Ledger, Captured, Calculator` |
| 625 | `## Terminate` → `## Stop All Services` |
| 648 | `Gamma shows "no data."` → `Dealer Positioning shows "no data."` |

**LEAVE LINES 277 AND 285 ALONE** — their "Gamma" is the Greek in the list
"price plus Delta, Gamma, Theta, Vega, Rho", not the page name. Renaming those
would be wrong.

Then re-read the whole file for any other prose reference to the renamed pages
(`grep -n "Swing Scanner\|Paper Trades\|Paper Portfolio\|Terminate\|Matrix"`)
and fix what remains.

**Step 3: Rebuild the HTML**

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/menu-reorganize-calculator-gamma-75d25a" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" docs/manuals/build_docs.py
```

Expected: regenerates `docs/manuals/user-guide/user-guide.html`. If the script
takes arguments or rebuilds all three manuals, that is fine — the other two are
unchanged in source, so their output should be byte-identical or trivially
re-stamped.

**Step 4: Verify no stale menu names remain in the user guide**

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/menu-reorganize-calculator-gamma-75d25a" && grep -n "Swing Scanner\|Paper Portfolio\|Paper Trades\|## Terminate\|## Gamma" docs/manuals/user-guide/user-guide.md
```

Expected: no output.

**Step 5: Commit**

```bash
git add docs/manuals/user-guide/
git commit -m "docs: update the User Guide for the reorganized menu"
```

**Note:** `technical-reference.md` and `api-reference.md` are deliberately NOT
touched — their "Gamma" and "Matrix" mentions are the engine/domain terms and
cache keys, which this change does not rename.

---

## Task 7: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md:1591` (webgui structure section), `:1661-1688` (route table)

**Step 1: Update the route-table page names**

Each row's label is the text immediately after the route cell. Change ONLY that
leading label — the long descriptions stay:

| Line | Was (leading label) | Now |
|---|---|---|
| 1661 | `Options · Scanner (` | `Options · Market Scanner (` |
| 1662 | `Matrix (**NEW 2026-07-20**` | `Opportunity Board (**NEW 2026-07-20**` |
| 1663 | `Paper Trades (ledger table` | `Paper Ledger (ledger table` |
| 1665 | `Paper Portfolio (paper account)` | `Paper Account (paper account)` |
| 1667 | `Swing Scanner (**multi-strategy**` | `Strategy Finder (**multi-strategy**` |
| 1668 | `Gamma (GEX/Charm/DEX/Vanna bars` | `Dealer Positioning (GEX/Charm/DEX/Vanna bars` |
| 1688 | `Terminate (guarded` | `Stop All Services (guarded` |

**Step 2: Update the webgui structure section**

`CLAUDE.md:1591` onward describes the drawer as "one item per group (**Options**,
**Market Trend & Sentiment**, **More**) plus the flat Market Dashboard / Trade /
Portfolio / Driver items". Correct it to: three groups, **three** standalone
`OPTIONS_RAIL` pages (Calculator, Dealer Positioning, Opportunity Board), and the
flat Trade Analyzer / Portfolio / Claude Trades items — noting that Market
Dashboard is now the first tab of the Market Trend & Sentiment group, so that
group's rail item lands on `/market`.

**Step 3: Add the "Last updated" entry**

Per the standing requirement at the top of `CLAUDE.md`, prepend a dated entry
summarizing this change: what moved, the seven renames, the workflow reorder, and
that routes / cache keys / commands / the financial vocabulary are unchanged.
Link the design and this plan.

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record the menu reorganization in CLAUDE.md"
```

---

## Task 8: Live verification

Tests cover the data; the browser covers the rendering. **REQUIRED SUB-SKILL:**
Use superpowers:verification-before-completion before claiming done.

**Step 1: Start the webgui**

Use the preview tool (`preview_start` with `{name: "webgui"}`), not Bash. The
webgui reads Redis caches — pages may show "Waiting for … service" placeholders
if the Tier-2 services aren't running. That is expected and NOT a failure of this
change; you are verifying **nav chrome**, not page data.

**Step 2: Verify the rail**

Read the page and confirm nine drawer items in order: Options, Calculator, Dealer
Positioning, Opportunity Board, Market Trend & Sentiment, Trade Analyzer,
Portfolio, Claude Trades, More — each with a distinct icon.

**Step 3: Verify the promoted pages**

Navigate to `/options/calculator` and `/options/gamma`. Confirm each renders with
**no tab strip**, the breadcrumb shows just the page name, and the browser tab
title is the new label. Confirm no console errors
(`read_console_messages` with `onlyErrors: true`).

**Step 4: Verify the reordered Options strip**

Navigate to `/`. Confirm eight tabs in workflow order and that Calculator and
Gamma are **absent** from the strip.

**Step 5: Verify the sentiment group**

Click the Market Trend & Sentiment rail item. Confirm it lands on `/market` and
that the strip shows Market Dashboard · Sentiment · Sector & Industry · Sector
Rotation · RRG.

**Step 6: Verify a handoff still works**

From the Market Scanner, use a row's **Send to Calculator** button. Confirm it
navigates to the Calculator with legs pre-filled — this is the proof that
promoting the page out of the tab strip broke no cross-page routing.

**Step 7: Screenshot the rail and report**

Share a screenshot of the expanded rail. Report honestly which steps passed and
anything that could not be checked (e.g. a page blocked by a service being down).

---

## Rollback

Every task is its own commit and nothing outside `webgui/`, `docs/manuals/`, and
`CLAUDE.md` is touched. To revert the whole change:

```bash
git revert --no-commit <task-2-sha>..HEAD && git commit -m "revert: menu reorganization"
```
