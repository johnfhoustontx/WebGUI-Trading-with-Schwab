# App UI consistency — Phase 5 (Trade · Claude Trades · Portfolio) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move the four Signal Desk screens, Claude Trades and Portfolio onto the page kit. Every chart, P&L colour, grade ramp and quadrant hue is untouched.

**Architecture:** Phase 0 built `pages/ui_kit.py`; Phase 1 migrated the nine Options boards; Phases 3 & 4 the Trend & Sentiment screens and the three entry points; Phase 2 the Options tools. This phase is the last of the *feature* screens — Phase 6 takes the system pages.

**Tech Stack:** NiceGUI 3.13, pytest.

**Prerequisite:** Phases 0–4 are done, green and promoted (`c3fe4c1`). Read the design first: [`2026-09-19-app-ui-consistency-design.md`](2026-09-19-app-ui-consistency-design.md), then the Phases 3 & 4 plan for the **ten rules every page task applies** — especially rule 10 (a render test must be scoped to its own render, and proved red against the pre-migration page) — and the Phase 2 plan's "CORRECTED after doing it" notes.

---

## What makes this phase different

**1. It is the cheapest phase for tests, and the most expensive for tables.** No test in these eleven suites selects "the first button" or keys a dict by button text — the element-harness shapes that made Phase 2 slow live in `test_appearance`, `test_expected_move`, `test_leg_editor*`, `test_market` and `test_sentiment*`, none of which this phase touches. Against that, Phase 5 migrates **eight `ui.table`s** where Phases 1–4 together migrated a handful, and `kit.table` changes two things about every one of them (below).

**2. No public-origin work at all.** None of the six is a published live screen and none carries a capability gate — zero `may_enqueue` / `can_navigate` / `is_public` across all six. `/driver` and `/trade` are in `test_live_screens.py`'s `FORBIDDEN`; the rest name no `Screen`. This is the one phase with no `if _may_enqueue:` branch to preserve.

**3. One file is migrated by deleting it.** See below.

**4. One shared frame restyles four screens at once.** `trade_shell.page(build)` is the frame for Overview, Evidence, Rank Board and Trade Plan. Task 1 changes all four in one commit — the RRG situation, and it gets the RRG treatment: **ship it alone and show the operator before Task 2.**

## `trade.py`'s `render()` is DELETED, not migrated

`pages/trade.py` is 1,142 lines, of which `render()` is 725–1142. **Nothing routes it.** `main.py` routes `/trade`→`trade_overview`, `/trade/evidence`→`trade_evidence`, `/trade/board`→`trade_board`, `/trade/plan`→`trade_plan_screen`. The only reference to `trade.render` anywhere in the repo is `tests/test_trade.py:105 assert callable(trade.render)`.

What the module is *for* is its pure helpers, and those are live — sixteen names imported by five siblings (`trade_evidence`, `trade_overview`, `trade_plan_screen`, `trade_shell`, `trade_terminal`), every one used at least twice. **Keep all of them.** Delete `render()`, `_BREAKDOWN_COLS`, `_SWING_COLS` and the imports only `render` needed.

Three things fall out with it, and they are the reason this is a task rather than a footnote:
- **`.calc-v2` retires.** `pages/trade.py:743` is the only `.calc-v2` class left under `pages/`; every other hit is a comment or the `theme.py` default.
- **`QUASAR_INTERNAL_CSS` becomes dead** — `pages/trade.py:56,727` is its last import and injection. `theme.APP_FIELD_CSS = build_quasar_css(THEME, scope=".ns-app")` is the byte-identical block, injected app-wide by both entrypoints, so nothing is lost.
- **A 300-second analyze backstop, written for the page nobody can reach.** `trade.py` carries `TYPICAL_ANALYZE_SEC` / `ANALYZE_TIMEOUT_SEC = 300` / `analyzing_label`, pinned by `tests/test_trade.py:607-618`, while the four reachable screens run `build_busy`'s **30-second default against a measured 96-second analysis**. ⚠ **Move those three to `trade_shell.py` in Task 1, before Task 6 deletes their file.**

## Decisions settled up front

**`stale=False` on every Phase 5 header**, and each for its own reason — write the reason at the call site, do not copy one sentence three times:

| screen | view | why not `stale=True` |
|---|---|---|
| the four Signal Desk screens | `trade:analysis` | `services/trade_svc/app.py:4` — *"on-demand only — there is no scheduler"*. Every trade view is request/response. This is the Strategy Finder's reason exactly. |
| Claude Trades | `options:driver_paper_account` | its publisher (`manage_due`) is gated on trading day **and** 08:00–15:15 CT, with no `STALE_OVERRIDES` entry and no `RTH_ONLY_VIEWS` membership — so `stale=True` goes amber every evening and all weekend. The `options:gamma` situation Phase 2 settled. |
| Portfolio | `portfolio:positions` | ⚠ **`webgui/alerts.py` measures this view's off-hours cadence twice and the two disagree** — line 116 says *620 s against 600 s* on a Sunday, line 146 says *23 s (2 s loop)* on **that same Sunday**. A stamp that may go amber nightly on a healthy stack is worse than no stamp; the nav badge's threshold is where that contradiction gets settled, and changing it is out of this phase. **Record it as a follow-up in the CHANGELOG.** |

⚠ **Not `driver:autonomous` as Claude Trades' header view** — it is published only while a cycle runs (the comment at `driver.py:446-447` says so), so its stamp would freeze between cycles. `options:driver_paper_account` is the widest-reach view the page reads.

**`kit.table` changes two things about all eight tables. Both are silent.**
1. **Alignment inverts.** Quasar's column default `align` is **right**; `ui_kit.table_columns` writes `c.get("align", "left")`. Every column with no explicit `align` renders right today and will render **left** unless named in `numeric=`. **Name every numeric column on every table.** ⚠ On the driver's five, `_PNL_CELL_SLOT` hardcodes `class="text-right"` on the `q-td`, so the body stays right while the header flips left — the two disagree visibly and no test sees it.
2. **Every column becomes sortable** (`table_columns` defaults `sortable: True`) — first time for all eight. That is the Momentum-leaderboard precedent and is wanted; say so in the commit.

**Paging is a non-issue.** `kit.table`'s default `rows_per_page=0` → `_pagination(0, None)` returns `None`, byte-identical to the `pagination=None` these tables pass today. **No `rows_number=` anywhere in Phase 5** — no page here holds rows back.

**Titles: the nav's word wins.** Three pages call themselves something the rail does not: `driver.py:597` says *"Claude Driver"* where nav and breadcrumb say **Claude Trades**; `portfolio.py:123` says *"Portfolio Analyzer"* where they say **Portfolio**; `trade_board.py:291` says *"Rank board"* where they say **Rank Board**. The header takes the nav's word in each case. ⚠ Overview and Evidence have **no title at all** today — the shell draws only the family name "Signal desk" — so they gain one.

**`scorecard.PNL_GREEN/PNL_RED/PNL_NEUTRAL` are NOT touched.** `scorecard.py` is shared with `pages/options/portfolio.py`, migrated in Phase 1, and the identical pair survives in `options/captured.py` and `options/paper.py`, also Phase 1. Phase 1 deliberately did not consolidate the P&L palette; reaching backwards into three finished pages is a separate decision with its own commit. `test_scorecard_shared.py:52-54` pins the hexes with a comment saying they moved verbatim on purpose — **leave it.**

`portfolio.py`'s own `UP_COLOR`/`DOWN_COLOR`/`MUTED_COLOR` are a different case: they are the **proxy-up/down and stream-live/manual** state indicators, not P&L, they are used by this page only, and `#888888` is a pure neutral. Those map to `TXT_POS`/`TXT_NEG`/`MUTED`.

## Corrections this survey made to the record

- ⚠ **`driver.py` has the cleared-container spinner bug**, and it is the sixth instance. `monitor_busy = _busy.build_busy(monitor, "Running…")` mounts the scrim **inside** `monitor` (`driver.py:614`), and **`_render_monitor` opens with `monitor.clear()` at line 698**, first run at 993. The scrim is deleted on the first paint; `monitor_busy.show()` at 905 and 926 has reached a deleted element ever since. The design predicted five; this is a sixth it did not.
- ⚠ **`driver.py`'s `status` label is an orphan.** Written at 906 (`"Stopping…"`) and 927 (`"Repricing…"`) and **never reset** — those are its only two writes. The text stays on screen for the rest of the session.
- ⚠ **Claude Trades tells the operator the wrong cadence.** `driver.py:21` and `:624-626` say the book "updates every 5-min manage cycle"; `options_svc/scheduler.py:186` is `_MANAGE_INTERVAL_MIN = 1`. `handlers.py:1388` carries the same stale docstring. Fix all three.
- ⚠ **The STOP dialog's body contradicts the current latch rule.** It says *"Enable re-arms it (clears the halt)"*; `driver.is_risk_halt` (269-279) is the rule, and a **manual** STOP is cleared by Enable while a **risk** halt is not. That distinction is the whole reason the Resume today button exists one line below.
- ⚠ **`_when_text` (driver.py:127-135) slices an ISO string** — `s[:10] + " " + s[11:16]` — does no conversion and adds no zone label, while its docstring asserts "already CT" without a check. It feeds the Opened/Closed columns of two tables. **Flag it; do not change the format** without reading `paper_account_db`'s writer.
- **`driver._pnl_color` is half-live**: written into every position row and read by no renderer (the slot reads `_pnl_class`). `tests/test_driver.py:146-148` pins it. Wire it or delete it — do not leave it.
- **`driver.DRIVER_CSS`'s sticky-thead half is redundant with and fights `shell.TABLE_CSS`**, which is app-wide and already does sticky thead, row dividers and the 11px/600 head. Keep at most its one `max-height` rule.
- **`trade_shell`'s wait scrim covers the whole screen including the symbol box** (`absolute inset-0` on `shell`) — a full-screen overlay on a page the design does not list among the three that get one.
- **`test_no_inline_style.py` covers neither `driver.py` nor top-level `portfolio.py`** (line 27's `"portfolio.py"` is `pages/options/portfolio.py`). Both are clean today, as is `scorecard.py`. **Add all three** — it is free.

## Seven vacuous-by-deletion tests to fix, not leave

Each asserts a name is ABSENT from a source; once the name is gone none can fail. Replace with the positive form `test_theme.py:541` uses for `[rotation]`:

`test_driver_monitor.py:434` · `:448` · `:460-464` · `test_scorecard_shared.py:62-64` · `test_trade_recommendation.py:217-218` · `test_page_help.py:127-131` (already vacuous — it greps `pages/trade.py` for four `markov_*` names that no longer exist) · `test_theme.py:34,35,39`.

⚠ **`test_theme.py:520` will FAIL, not go vacuous** — it iterates `(".calc-v2", theme.QUASAR_INTERNAL_CSS)`. Rewrite it.
⚠ **`test_driver_monitor.py:448` asserts `"read_version(" not in src`** and must keep passing: the kit reads `bus_client.read_meta` inside `ui_kit.py`, not in the page, so it does — **verify, do not assume.**

## The source-grep tests that constrain the rewrite

`test_driver_monitor.py:424-448` reads `driver.render`'s own source and requires `read("options:driver_paper_account")`, `async def _poll`, `read_versions(`, `run.io_bound` to appear in it. **This is the gamma situation** — `with kit.page():` would re-indent the whole body and move those lines under a `with`. Use `page_col = kit.page()` re-entered, the pattern `95adc5d` established. The same shape binds `test_trade_help.py:149-175` (each screen must contain `"trade_help"` and `"tip("`; `trade_overview` must contain `clearance_help` **exactly twice**) and `test_trade_recommendation.py:215-239`.

---

## Task 1 — The Signal Desk shell, alone

**Files:** `pages/trade_shell.py`, `pages/terminal_theme.py` (surface half only), `tests/test_trade.py`, new `tests/test_trade_shell.py`.

`page(build)` gains a `title` argument; each of the four callers passes its own screen name. Inside: `kit.page()` + `kit.header(title, view="trade:analysis", stale=False)`, the "Signal desk" lockup and `model_stamp` folding into the header, the ticker box to `kit.symbol_field` (keeping `should_commit`'s draft/committed semantics), and the two report buttons to `kit.button(kind="secondary")` in `head.actions`.

- **The report status label becomes `kit.set_busy`.** `report_status` is set to `f"Running {label} for {sym}…"` and cleared when `_watch_reports` opens the tab — the Phase 2 gamma case exactly (its three tab-opening toasts became `set_busy`, not `toast`). ⚠ `_watch_reports` is module-level and sees only `state`, so stash the handles: `state[f"{cmd}_btn"] = btn`.
- **The wait becomes `kit.region("Analyzing…", timeout=ANALYZE_TIMEOUT_SEC)`** with `TYPICAL_ANALYZE_SEC` / `ANALYZE_TIMEOUT_SEC` / `analyzing_label` **moved here from `trade.py`**, and the scrim scoped to the results, not the whole screen. Re-aim `tests/test_trade.py:607-618` at the shell.
- **`T.FONT_HTML` is deleted** — Manrope 400–800 + JetBrains Mono 400/500/700 stop loading. This is the guard's `add_head_html` entry and it is a **font link**, the one thing the guard's docstring allows no reason for.
- ⚠ **`test_busy_coverage.py:77-79` requires the source to literally contain `.busy.show(`** if `build_busy(` goes. `trade_shell.py:175` currently writes `sp.show(…)` — change it.
- ⚠ **`_REPORTS` is a 4-tuple unpacked in three places and in `tests/test_trade_help.py:88-89`.** Keep the shape.

**Guard:** `trade_shell.py` **deleted**.

**⚠ STOP after this task and show the operator.** It restyles Overview, Evidence, Rank Board and Trade Plan in one commit.

## Task 2 — Rank Board

**Files:** `pages/trade_board.py`, `tests/test_trade_board.py`.

`kit.header("Rank Board", view="trade:rank_board", stale=False)`; `meta_line(b)` → `kit.status_line()`; the amber exposure line → `kit.notice`; **Rebuild** → `kit.button(kind="primary", icon="refresh")` in `head.actions` + `kit.set_busy` — which also moves it out of `filters`, the container `_paint` clears at 319, and that is what makes the busy timer safe.

⚠ **Hide gated stays raw**, with a written reason: it carries **both** a selected state (`FILTER_ON`/`FILTER_OFF`) **and a label that changes with it** (`"Hide gated"` ⇄ `"Showing ungated only"`). `kit.button`'s four kinds express neither.

⚠ **Do not convert the two hand-drawn grids to `kit.table`.** The comments at 44-62 record that every column width was measured in the page's faces so two nine-column panes fit side by side, and `test_trade_help.py:64-73` pins `_HEAD` as the help-coverage key.

**Guard:** `trade_board.py` → `{"button": 1}` with the toggle's reason. **This is the phase's one new permanent entry.**

## Task 3 — Trade Plan

**Files:** `pages/trade_plan_screen.py`, `tests/` (new).

`kit.header("Trade Plan", view="trade:analysis", stale=False)`; both buttons → `kit.button` (`Find strikes` primary, `Open in calculator` secondary) **kept inside the plan card**, not in `head.actions` — `plan_card.set_visibility(actionable)` hides them when there is no plan, and header actions would imply they are always available.

The notify at 189-190 (*"This plan has no options structure to model."*) is a refusal the page knows before the click: **disable the button in `_paint`** with `btn.set_enabled(bool(tt.calculator_handoff(a)))`. ⚠ **`kit.gate` will not bite** — it reads `field.validation`, and a button is not a field (Phase 2's measured finding). Either way the notify count reaches zero.

**Guard:** `trade_plan_screen.py` **deleted**.

## Task 4 — Overview and Evidence

**Files:** `pages/trade_overview.py`, `pages/trade_evidence.py`.

Titles they have never had (the shell now provides them), `kit.section_title` for the panel heads, app tokens for the neutral ladder. Neither has a guard entry and neither builds a raw button, table, dialog or notify — verified. ⚠ `test_trade_help.py:149-175` requires `clearance_help` to appear in `trade_overview` **exactly twice**, and `test_trade_recommendation.py:215-239` requires `ui.label("Short Term")` and `ui.label("Long Term")` and forbids `ui.label("Position")`.

## Task 5 — `terminal_theme` retires its surface half

Delete `FONT_HTML`, `PAGE`, `SHELL`, `PANEL`, `MONO`, `EYEBROW`, `PANEL_TITLE`, `SCREEN_TITLE`, `SUBTLE`, `NOTE`, `BODY`, `LABEL`, `VALUE`, `HAIRLINE`, `RULE`, `BTN_PRIMARY`, `BTN_GHOST`, `TOOLTIP`.

**Keep** `POS`/`NEG`/`WARN`/`DIM`/`OFF`/`STATE_TEXT`, `BAR_*`, `CHIP_*`, `CALLOUT*`, `FILTER_ON`/`FILTER_OFF`, `SCROLL_X`, `sign_text`, `sign_bar`, `centred` — all data encodings or the Task 2 toggle's selected state.

Re-aim the ~40 `T.*` assertions in `test_trade_terminal.py` and `test_trade_recommendation.py`. **Re-aim, never delete** — each pins a data colour that survives.

## Task 6 — `trade.py`'s `render()` goes, and `.calc-v2` with it

Delete `render()` (725-1142), `_BREAKDOWN_COLS`, `_SWING_COLS` and the render-only imports (`time`, `bus_client`, `pages.busy`, `select_all_on_focus`, the `.options.theme` block). **Keep every pure helper** — 75 tests pin them, and five siblings import sixteen names.

Then delete `QUASAR_INTERNAL_CSS` from `theme.py` and rewrite `test_theme.py:520` (it **fails**, not goes vacuous). `tests/test_trade.py:104-105` is deleted with `render`; name that in the commit. ⚠ Task 1 must already have moved the three analyze-timeout names.

**Guard:** `trade.py` **deleted**.

## Task 7 — Portfolio

**Files:** `pages/portfolio.py`, `tests/test_portfolio.py`, `tests/test_no_inline_style.py`.

`kit.page()` + `kit.header("Portfolio", view="portfolio:positions", stale=False)` — **write the contradiction above as the reason**; Refresh → `head.actions` + `kit.set_busy`; `status_line(p)` → `kit.status_line()`; **drop the `"Refreshing…"` label write**, which duplicates the spinner.

Three `kit.table(..., numeric=(...))` — name every numeric column: `HOLDINGS_COLS` quantity, market_value, day_pl, total_pl, since_purchase; `SECTOR_COLS` weight, benchmark_delta; `PERF_COLS` the eight grade/return columns. ⚠ `test_shared_copy.py:99-106` asserts `"Shares"` is a `HOLDINGS_COLS` label and `"Contracts"` is not — keep the labels.

Map `UP_COLOR`/`DOWN_COLOR`/`MUTED_COLOR` to `TXT_POS`/`TXT_NEG`/`MUTED`. ⚠ `tests/test_portfolio.py:12-39` pins all six constants **by identity**, so the assertions survive a hex change as long as the names do.

**No spinner bug here** — `build_busy` mounts on `panels` and `_repaint` only sets `.rows`. **The SSE stream does not fight `kit.table`**: the service republishes and the page sets `tbl.rows = …; tbl.update()` in place; `kit.table` returns the same element. Its `ROW_CLASS_FN` reads `_row_class`/`_selected`, which service rows lack → `undefined` → `.filter(Boolean)` drops it. Harmless.

Add `portfolio.py` to `test_no_inline_style.py`. **Guard:** `portfolio.py` **deleted**.

## Task 8 — Claude Trades

**Files:** `pages/driver.py`, `tests/test_driver.py`, `tests/test_driver_monitor.py`, `tests/test_no_inline_style.py`, `services/options_svc/handlers.py` (one docstring), `webgui/page_help.py`.

⚠ **Use `page_col = kit.page()` re-entered, not `with kit.page():`** — `test_driver_monitor.py:424-448` reads `render`'s own source at its own indent.

- `kit.header("Claude Trades", view="options:driver_paper_account", stale=False — write the RTH-only reason)`.
- **One `kit.region` fixes the deleted spinner.** Name the bug and the line (`monitor.clear()` at 698) in the commit body. **Prove it first**: measure that zero scrims survive a render on the pre-change page, as Phase 3 did for its five.
- **The orphan `status` label goes**, replaced by `kit.set_busy` on the four action buttons, released in `_poll` when the version moves.
- **Two `kit.confirm`s**, both already built at page level (665, 681) so neither is inside a cleared container. `danger=True` on both — Resume today re-arms an autonomous trader that halted *itself*. Cancel-then-confirm is already the order. ⚠ The two `ui.card()`s carry **no classes** — default Quasar cards, the unthemed-dialog case; `CONFIRM_CARD` fixes it. ⚠ **Fix the STOP body's stale latch sentence.**
- **Five `kit.table(..., numeric=(...))`**, keeping `.add_slot("body-cell-pnl", _PNL_CELL_SLOT)`.
  Measured by AST, the columns carrying **no `align`** today — i.e. the ones Quasar renders
  RIGHT and `kit.table` will render LEFT — are: closed `strategy, qty, pnl`; positions
  `strategy, quantity, pnl, status`; both scorecards `trades, pnl, win_rate`; postmortem
  `trades, win_rate, pnl, avg`.
  ⚠ **Name only the genuinely numeric ones** — `("qty","pnl")`, `("quantity","pnl")`,
  `("trades","pnl","win_rate")`, `("trades","win_rate","pnl","avg")`. **`strategy` and
  `status` are text columns that are right-aligned today by accident** (nobody writes
  `align` for a text column expecting right), and under the kit they move left, which is
  how every other table in the app renders them. That is a deliberate fix — **name it in
  the commit** so it is not read as a regression.
- `DRIVER_CSS` cut to at most its `max-height` rule; the Quasar colour words (`text-amber-9`, `text-red-9`, `text-green-9`, `text-red-8`, `bg-[#E24B4A]`) and **28** `opacity-*` mutings to theme tokens; the five classless `ui.card()`s to `theme.CARD`.
- `CONTROL_OFF_COLOR` `#888888` is a pure neutral → `MUTED`; `CONTROL_ACTIVE_COLOR` / `CONTROL_HALTED_COLOR` are a state reading on a filled pill → `BADGE_POS` / `BADGE_WARN`. ⚠ `test_driver_monitor.py:70-72` pins all three strings.
- ⚠ `equity_chart` must stay present at page build (the ESM import-map gotcha) and never inside a region that gets cleared.
- **Correct the 5-min → 1-min cadence** on screen, in the module docstring and in `handlers.py:1388`.
- Decide `_pnl_color`: wire it or delete it.
- `"STOP"` → `"Stop"` is a sentence-case call the commit must name, with `page_help.py:1215` changed in step — or keep the caps and say why.

Add `driver.py` and `scorecard.py` to `test_no_inline_style.py`. **Guard:** `driver.py` **deleted**.

## Task 9 — Guard, vacuous tests, help text, gallery

Rewrite the seven absence-asserts in the positive form. **Verify — do not assume —** that `page_help.py` carries every description sentence deleted from a frame. ⚠ `tools/gallery_screens.py:161-163` captures `/trade`, `/trade/evidence`, `/trade/plan` as `image20/21/22` and `test_gallery_routes.py:39` pins those routes: **the marketing gallery shots need recapturing**, the same cost the design records for the three tracked Simulator images.

## Task 10 — Full suite, then the harness

Compare the failing **and skipped** sets against the Phase 2 baseline (webgui 5403 passed / 1 skipped), never the counts. Then `tools/ui_harness.py` for all six screens, and a screenshot of the Rank Board and of Claude Trades.

---

**Where the guard lands:** six entries deleted, one kept — `trade_board.py: {"button": 1}`, the Hide gated toggle. That makes ten permanent entries.
