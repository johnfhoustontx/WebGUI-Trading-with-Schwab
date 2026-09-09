# The rest of the app — completing the reader's-voice pass (design)

**Date:** 2026-09-04
**Scope:** every page not covered by the seven per-page designs already written
(`/desk`, `/options/flow`, `/options/matrix`, `/options/paper`,
`/options/captured`, `/options/scanner`, `/options/rescue`).

## What an audit of the remaining ~20 pages actually found

Most of them are already fine, and that is the headline. The six Trend &
Sentiment screens were rebuilt on 2026-08-17 with copy written from the reader's
side; `pages/portfolio.py` uses plain English throughout ("Market Value",
"Since Purchase", "Suggestion"); `/status`, `/terminate` and `/settings` are
machine-facing screens whose vocabulary IS the subject matter.

Grepping the tree for the four defect classes this pass targets turned up a
short, specific list rather than twenty pages of work:

| defect | sites |
|---|---|
| a waiting line naming an internal service | 6 sentiment screens + `/market` |
| a toast reporting the mechanism ("… requested") | 6 sentiment + 3 options |
| a casual shortening in a visible label | `/options/portfolio`, `/driver`, `/portfolio`, `/eod`, `strategy_table` |
| a label naming the wrong quantity | `/options/portfolio`'s `CurVal` |

## 1. Two more shared sentences

`pages/copy.py` already holds `WAITING_OPTIONS` for the three screens that show
it. Two more conditions are shown by more than one screen and worded differently
at each, so they join it:

- **`WAITING_SENTIMENT`** — `/sentiment` says *"Waiting for sentiment service…"*,
  `/sentiment/rotation`, `/sentiment/rrg` and `/sentiment/sectors` say *"Waiting
  for the sentiment service…"* (with "the"). Two spellings, four screens.
- **`WAITING_MARKET`** — `/market` only, but it belongs beside its siblings so
  the next screen to need it finds it.

Both take the `WAITING_OPTIONS` shape: state what is true, name no service.

⚠ The rule that module's docstring already carries applies unchanged: this is the
line for a feed that has published **nothing**, never for one that is fine and
has nothing to say.

## 2. The "requested" toasts

Nine sites report that a command was enqueued. That is honest and useless — it
tells the reader nothing about what to expect or how long.

| where | was | now |
|---|---|---|
| the 5 sentiment refresh buttons | `Refresh requested` | `Refreshing — the page updates when the new read lands.` |
| `/sentiment/momentum` | `Recompute requested` | `Recomputing — this one takes a moment.` |
| `handoff.send_to_paper` | `Paper trade requested.` | `Sent to the paper ledger — it appears when the engine confirms.` |
| `/options/portfolio` reset | `Paper account reset requested.` | `Resetting the paper account — the book clears when the engine confirms.` |
| `/options/swing` | `Swing scan requested` | `Scanning — results appear when the scan finishes.` |

The last three keep the "when the engine confirms" formula the Paper Ledger,
Captured Signals and Market Scanner passes established, because they are the same
kind of `cmd:*` command.

## 3. Column labels

### `/options/portfolio` (Paper Account)

| was | now |
|---|---|
| `Strat` · `Exp` · `Qty` | **Strategy** · **Expiry** · **Contracts** |
| `Credit` | **Entry** — the engine's paper book holds directional debits, the same defect fixed on the Paper Ledger and Captured Signals |
| `CurVal` | **Mark** — a casual shortening AND the wrong idea; it is the live price, which is what `/desk` and Captured Signals call Mark |
| `P&L$` | **Open P&L** — the `$` says nothing the number doesn't, and these rows are open |
| `ID`, `Symbol`, `Strikes`, `Status` | unchanged |
| orders table: `Qty` | **Contracts** |

### `/driver` (Claude Trades)

`Strat`→**Strategy**, `Qty`→**Contracts** (both tables).

### `/portfolio`, `/eod`, `strategy_table`

- `/portfolio`: `Qty`→**Contracts**.
- `/eod`: the captured section's `Rec`→**Action** and the closed-trades
  `Credit`→**Entry**, matching the two pages that report the same rows.
- `strategy_table.strategy_columns`: `Exp`→**Expiry**. ⚠ Shared — it renders the
  Strategy Finder *and* the Market Scanner's Directional tab, so this reaches two
  screens by design.

## 4. ⚠ What is deliberately NOT renamed

- **`leg_editor`'s `Qty`.** It sits in a `w-16` (64px) track in a dense multi-leg
  widget mounted by the Calculator, the Simulator and Rescue; "Contracts" does not
  fit and widening the track reflows a shared component on three pages. Same
  width-concession class as `/desk`'s `STRAT`/`QTY`, and it gets the same
  treatment: a test recording that it is a deferral with a reason.
- **The six Trend & Sentiment screens' own vocabulary**, `/status`, `/terminate`,
  `/settings`, and every Highcharts option dict — chart config, not copy.
- **Raw payload keys**, as on all seven previous pages.

## 5. Verification

Not browser-verified — a worktree resolves to prod and would bind `:8500`. The
full `webgui` suite is the gate; it stood at **2971 passed** before this sweep.
