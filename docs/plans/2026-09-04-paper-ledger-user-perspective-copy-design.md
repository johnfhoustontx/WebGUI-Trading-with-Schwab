# Paper Ledger — rewording the screen from the reader's perspective (design)

**Date:** 2026-09-04
**Scope:** `/options/paper`. Fourth page in the pass
([Desk](2026-09-04-desk-user-perspective-copy-design.md) ·
[Flow Alerts](2026-09-04-flow-alerts-user-perspective-copy-design.md) ·
[Opportunity Board](2026-09-04-opportunity-board-user-perspective-copy-design.md)).

## What is different about this page

The first three were read-only. This one **acts** — Close, Delete, Analyze,
Delete all closed — so its copy has a second job: saying what a button will do
and what happened after it was pressed. The toasts currently report the
*mechanism* ("Close requested."), which is honest about the command being
enqueued and silent about what the reader should now expect to see.

## 1. One label is wrong, not merely terse

`Credit` holds `entry_credit_total`, and **a debit trade stores its debit as a
negative credit**. So half the book renders under a header that contradicts the
sign in the cell. It becomes **Entry**, which is also `/desk`'s word for the same
quantity — the sign then carries credit-vs-debit, which is what it already does.

That frees the name `Entry`, which was taken by `entry_time`. That column becomes
**Opened**, which is a better word for a timestamp regardless.

⚠ This is a **swap**, and worth calling out: a reader who knows the old layout
sees `Entry` move from a time to a price. The alternative — leaving `Credit`
wrong — is worse, and `Opened` is unambiguous in a way `Entry` never was.

## 2. Column headers

| was | now | why |
|---|---|---|
| `Symbol` | `Symbol` | already plain |
| `Strat` | **Strategy** | casual shortening; no width limit on a `ui.table` |
| `Strikes` | `Strikes` | already plain |
| `Exp` | **Expiry** | `/desk` says EXPIRY |
| `Qty` | **Contracts** | casual shortening — and this IS a contract count |
| `Credit` | **Entry** | see above; `/desk` says ENTRY |
| `Risk` | **Max loss** | it is `max_loss_total`; "Risk" names no quantity |
| `P&L` | `P&L` | correct as-is — realized when closed, unrealized when open, so `/desk`'s "OPEN P&L" would be wrong for half these rows |
| `Status` | `Status` | matches `/desk` |
| `Entry` (time) | **Opened** | frees `Entry` for the price |

### ⚠ The deliberate divergence from `/desk`

`Strategy` and `Contracts` are the two labels `/desk` had to keep as `STRAT` and
`QTY` — its Positions grid has measured per-string `minmax()` floors, and those
two are label-bound, so spelling them out there costs ~58px against 43px of
slack and would clip the panel at the 1920px window it is read at.

So the app now shows one concept under two spellings, and that is a decision
rather than an oversight: the standing rule is to spell out casual shortenings,
and the Desk's abbreviations are a width **concession** already pinned by
`test_the_two_width_blocked_labels_stay_short`. That test gains a note pointing
here, so the next reader finds the reason rather than the inconsistency.

## 3. The action copy

| was | now |
|---|---|
| button `Close` | **Close trade** — the Analyze dialog also has a `Close` button, which dismisses it |
| `Close requested.` | `Closing {symbol} — the ledger updates when the engine confirms.` |
| `Delete requested.` | `Deleting {symbol} — the row clears when the engine confirms.` |
| `Delete-all-closed requested.` | `Deleting every closed trade — the ledger updates when the engine confirms.` |
| `Click a trade row first.` | unchanged — already plain and already actionable |
| `Reloading paper trades…` | unchanged |

"Requested" was accurate: these are commands on `cmd:options`, and the ledger
changes when `options_svc` processes them. The new wording keeps that honesty —
it says the engine has to confirm — while telling the reader what to watch for.
Nothing here claims the trade is closed.

## 4. Out of scope

The Analyze popup's metric names (`Unrealized P&L`, `% of max profit`,
`Current price`, `DTE remaining`, `Profit target`, `Breakeven`) are already
reader-facing and correct; `DTE` stays as a trader acronym per the standing rule.
The transient status strings (`Reloading…`, `Closing…`) and every colour map are
unchanged. The raw payload keys are untouched, as on the previous three pages.

## 5. Verification

Not browser-verified — a worktree resolves to prod and would bind `:8500`.
Baseline for the affected suites (`test_options_paper.py`, `test_desk.py`):
**287 passed**.
