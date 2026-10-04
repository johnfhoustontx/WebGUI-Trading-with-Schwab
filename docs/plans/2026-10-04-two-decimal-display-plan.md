# Two-Decimal Display Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Every price, strike, ratio, percentage and dollar total on an app screen shows exactly two decimals (`450.00`, never `450`).

**Architecture:** Named formatters in `webgui/pages/fmt.py` own the number-to-text step; a `decimals=` option on `kit.table` formats numeric cells in the browser so the row keeps its number and sorts numerically. Pages call those two things instead of writing a format string. Design: [2026-10-04-two-decimal-display-design.md](2026-10-04-two-decimal-display-design.md).

**Tech Stack:** Python 3.11, NiceGUI 3.13 (Quasar `q-table` column `:format`), Highcharts format strings, pytest.

**Run the tests from `webgui/`:** `<venv>\python -m pytest tests -q` (the venv lives in the main checkout; a worktree needs its absolute path).

---

## The decision table (apply it at every site)

| The value is | Formatter | Shape |
|---|---|---|
| An underlying or option price, a level (flip, wall, stop, target, breakeven) | `fmt.price` | `6,712.81` |
| A strike | `fmt.strike` | `450.00` (no thousands separator: it sits in `450.00/445.00` pairs) |
| A ratio or multiple | `fmt.ratio` | `1.50` (the page adds its own `×`) |
| A percentage | `fmt.pct` / `fmt.signed_pct` | `1.20%` / `+1.20%` |
| A dollar total | `fmt.money` | `$1,250.00` / `-$1,250.00` |
| An abbreviated dollar total | `fmt.money_short` | `$1.20M` / `$45.00K` |
| A numeric table cell of any family above | `kit.table(decimals=(...))` | the number stays in the row |
| A score, rank, count, DTE, quantity, volume, open interest, Greek | **leave it** | |
| A CSS width, an SVG coordinate, a DOM `data-` attribute, a dict key | **leave it** — not displayed | |

A page-local helper with its own contract for a missing reading (`""`, `$0`, a
dash) keeps that contract and calls the `fmt` function for the number.

A test that pinned the old string is updated to the new string. It is never
loosened: no `in` where there was `==`, no deleted assertion.

---

### Task 1: The formatters

**Files:** Modify `webgui/pages/fmt.py`; Test `webgui/tests/test_fmt.py`

1. Write failing tests: `price(450) == "450.00"`, `price(6712.814) == "6,712.81"`,
   `strike(5800) == "5800.00"`, `ratio(1.5) == "1.50"`, `pct(65) == "65.00%"`,
   `pct(1.2, signed=True) == "+1.20%"`, `money(1250) == "$1,250.00"`,
   `money(-40) == "-$40.00"`, `money(61.5, signed=True) == "+$61.50"`,
   `money_short(1_200_000) == "$1.20M"`, `money_short(45_000) == "$45.00K"`,
   `money_short(950) == "$950.00"`, and each returns `NO_READING` for `None`,
   NaN, a bool and a string that will not parse. `signed_pct(1.2) == "+1.20%"`.
2. Run, see them fail (`AttributeError: price`).
3. Implement over `num()`, reading one `DECIMALS = 2`. `signed_pct`'s default
   `nd` becomes `DECIMALS`.
4. Run, green. Commit.

### Task 2: `kit.table(decimals=...)`

**Files:** Modify `webgui/pages/ui_kit.py`; Test `webgui/tests/test_ui_kit.py`

1. Failing test: `table_columns(cols, decimals=("spot",))` puts a `:format` on
   `spot` and on no other column; the format string contains `toFixed(2)` and
   answers the em-dash for `null`; a column that already carries `:format` keeps
   its own.
2. Implement `DECIMAL_FORMAT` (built from `fmt.DECIMALS`) and the `decimals`
   argument on `table_columns` and `table`.
3. Run, green. Commit.

### Task 3: The options tables

`matrix.py`, `scanner.py`, `strategy_table.py`, `captured.py`, `paper.py`,
`portfolio.py` (options), `shares.py`, `income.py`, `flow.py`, `rescue.py`,
`finder_view.py`, `swing.py`, `finder_live.py`.

For each: name the price / ratio / percentage / dollar columns in `decimals=`;
where a body-cell slot renders the cell, fix the slot's own `toFixed`; where the
row builder rounds a percentage to one place, round to two; strike-pair text
goes through `fmt.strike`. Update the tests that pin the old strings. Commit.

### Task 4: The options text and charts

`detail.py`, `checks.py`, `chain_grid.py`, `leg_editor.py`, `expected_move.py`,
`gamma.py`, `calculator.py`, `calc_live.py`, `simulator.py`, `sim_view.py`,
`flow_panels.py`, `funnel_view.py`, `book_fit.py`, `ev.py`, `perf_charts.py`,
`persistence.py`, `entry_panel.py`, `rate_trade.py`.

Apply the decision table. Highcharts: strike and price axes take
`{value:.2f}`, tooltips `valueDecimals: 2`, level labels through `fmt.price`.
Commit.

### Task 5: The other pages

`desk.py`, `market.py`, `portfolio.py`, `symbol.py`, `symbol_facts.py`,
`trade.py`, `trade_board.py`, `trade_terminal.py`, `trade_overview.py`,
`trade_evidence.py`, `trade_plan_screen.py`, `sentiment*.py`, `console*.py`,
`eod.py`, `scorecard.py`, `sector_heat.py`, `rotation_view.py`,
`momentum_view.py`, `bullbear.py`, `news_view.py`, `structure.py`.

Apply the decision table. Commit.

### Task 6: The guard, the docs, the full run

1. `webgui/tests/test_two_decimals_guard.py`: fails on a `:g}` format in a
   `webgui/pages/**` module outside a small allow-list of non-display uses (each
   with its reason).
2. `docs/CHANGELOG.md` entry; the manuals and `webgui/page_help.py` where they
   quote a figure in the old shape; `docs/reference/webgui-dev-notes.md` gains
   the rule (prices and ratios go through `fmt`, table cells through
   `decimals=`).
3. Run the whole webgui suite; compare the failing SET with a run at the base
   commit.
4. Render Matrix, Scanner, Gamma and Paper in the local page harness and look.
5. Commit.
