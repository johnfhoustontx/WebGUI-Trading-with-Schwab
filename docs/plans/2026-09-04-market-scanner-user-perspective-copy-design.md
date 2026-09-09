# Market Scanner — rewording the screen from the reader's perspective (design)

**Date:** 2026-09-04
**Scope:** `/options/scanner`. Sixth page in the pass
([Desk](2026-09-04-desk-user-perspective-copy-design.md) ·
[Flow Alerts](2026-09-04-flow-alerts-user-perspective-copy-design.md) ·
[Opportunity Board](2026-09-04-opportunity-board-user-perspective-copy-design.md) ·
[Paper Ledger](2026-09-04-paper-ledger-user-perspective-copy-design.md) ·
[Captured Signals](2026-09-04-captured-signals-user-perspective-copy-design.md)).

## This page starts in better shape than the previous five

Its prose is already written from the reader's side and should be left alone:

- `day_note` — *"Waiting for today's scan — the day's signals are from a previous
  session and are not shown."*
- `truncated_note` — *"N earlier signals dropped at the day cap — the day's
  coverage is incomplete."*
- The three subtab tooltips in `page_help.SUBTAB_HELP`, which explain 0-DTE,
  Swing and Directional in one sentence each.
- `status_line`'s deliberate word **live** — the tab headers carry the DAY's
  counts (hundreds by 3pm) while that line sums the last SCAN (dozens), and the
  word is what stops the gap reading as a bug.

So the work here is narrow: column labels, a fourth spelling of the waiting
line, and one toast.

## 1. ⚠ `Credit` STAYS, and that is the opposite of the last two pages

The [Paper Ledger](2026-09-04-paper-ledger-user-perspective-copy-design.md) and
[Captured Signals](2026-09-04-captured-signals-user-perspective-copy-design.md)
both had a `Credit` column that was **wrong**, because their books mix credits
and debits and a debit is stored as a negative credit.

`signal_columns()` is not that. Its own docstring says *"a credit-spread signal
table (0-DTE / Swing)"*, and the Directional tab — the one that holds debits —
does not use these columns at all: `directional_columns()` is a separate list
built on `strategy_table.strategy_columns()`, and it *deliberately* carries no
credit or R:R economics, because a directional trade is scored by a model that
is not commensurable with the premium one.

So `Credit` is accurate here and stays. This gets a comment in the source and a
test, because three pages into a pattern the next reader will "fix" it.

## 2. Column headers (`signal_columns`)

| was | now | why |
|---|---|---|
| `Symbol` | `Symbol` | already plain |
| `Type` | **Strategy** | it holds `PCS` / `CCS` / `IC` — the structure. Matches the Paper Ledger and Captured Signals, which name the same thing |
| `Exp` | **Expiry** | matches the four pages before it |
| `DTE` | `DTE` | trader acronym, and unlike Captured Signals this one is **live** — the scan reruns every 15 minutes |
| `Strikes` | `Strikes` | already plain |
| `Credit` | `Credit` | correct here — see above |
| `Max Loss` | **Max loss** | sentence case, matching the Paper Ledger and Captured Signals |
| `R/R %`, `PoP %`, `IV Rank` | unchanged | trader vocabulary, per the standing rule |
| `Score`, `Grade` | unchanged | match `/desk` and the Opportunity Board |
| `Dropped` | **Dropped at** | the cell holds `stale_since`, a timestamp — *when* the signal stopped appearing in a scan, not whether it did |

## 3. The waiting line — a fourth spelling

`status_line` returns `"Waiting for options service…"` — note the missing "the",
which makes it a fourth variant of a sentence that had three. It becomes
`pages.copy.WAITING_OPTIONS`, the shared constant introduced by the Opportunity
Board pass.

⚠ Nothing here imports `pages.copy` yet, and nothing imports `scanner` except
`webgui/alerts.py` (for `_sig_key`) and `main`, so there is no cycle risk.

## 4. The scan toast

| was | now |
|---|---|
| `Scan requested` | `Scanning — results appear when the scan finishes.` |

A full scan takes tens of seconds, so "requested" left the reader with no idea
whether to wait or re-press. Same shape as the Paper Ledger and Captured Signals
toasts: it does not claim the scan is done.

## 5. Out of scope

The three tab base labels (`0-DTE` / `Swing` / `Directional`) are trader
vocabulary with a one-sentence tooltip each. `directional_columns()` inherits
from `strategy_table.strategy_columns()`, which the Strategy Finder also renders
— renaming there changes two pages and belongs in its own pass. Every colour
map, `tab_label`'s count format, and the raw payload keys are unchanged.

## 6. Verification

Not browser-verified — a worktree resolves to prod and would bind `:8500`.
Baseline for `test_options_scanner.py`: **77 passed**.
