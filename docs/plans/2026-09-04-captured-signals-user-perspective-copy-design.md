# Captured Signals — rewording the screen from the reader's perspective (design)

**Date:** 2026-09-04
**Scope:** `/options/captured`. Fifth page in the pass
([Desk](2026-09-04-desk-user-perspective-copy-design.md) ·
[Flow Alerts](2026-09-04-flow-alerts-user-perspective-copy-design.md) ·
[Opportunity Board](2026-09-04-opportunity-board-user-perspective-copy-design.md) ·
[Paper Ledger](2026-09-04-paper-ledger-user-perspective-copy-design.md)).

## Three labels are wrong, not merely terse

This page turned up more genuine mislabelling than any of the previous four.

1. **`DTE` holds `dte_at_entry`.** It is the days-to-expiry the signal had *when
   it was captured*, and it never moves. A reader watching a tracked signal has
   every reason to read that column as "days left", and it drifts further from
   the truth with every session that passes — the one column on a
   *tracking-over-time* page that does not track over time.
2. **`Grade` holds `entry_grade`** — the same freeze, one column over.
3. **`Credit` holds `entry_credit`, and this book contains debits.** `mode` is
   the PREMIUM-vs-DIRECTIONAL tag, and a directional signal is a debit. Exactly
   the defect the [Paper Ledger pass](2026-09-04-paper-ledger-user-perspective-copy-design.md)
   found in the same-named column, and it is fixed the same way.

## 1. Column headers

| was | now | why |
|---|---|---|
| `Rec` | **Action** | the cell literally holds `TAKE_PROFIT` / `HOLD` / `CUT` — what to do, not a description |
| `Symbol` | `Symbol` | already plain |
| `Strat` | **Strategy** | casual shortening; matches the Paper Ledger |
| `Mode` | **Style** | it is the PREMIUM-vs-DIRECTIONAL tag. "Mode" names nothing; "Style" says selling premium versus taking a direction. ⚠ Deliberately **not** "Trade type" — this app already uses `trade_type` for 0-DTE / Swing / Directional, and reusing the phrase would collide |
| `Opened` | `Opened` | already plain, and matches the Paper Ledger |
| `Exp` | **Expiry** | matches the Desk and the Paper Ledger |
| `DTE` | **DTE at entry** | it is frozen at capture — see above |
| `Credit` | **Entry** | the book contains debits; the sign carries which |
| `Cur Price` | **Mark** | casual shortening, and `/desk`'s word for the live price of an open position |
| `Risk` | **Max loss** | it is the max loss; matches the Paper Ledger |
| `P&L` | **Open P&L** | see the note below |
| `Grade` | **Entry grade** | frozen at capture — see above |

### ⚠ `Open P&L` here, plain `P&L` on the Paper Ledger

These look inconsistent and are not. A closed signal **leaves this table** — the
module says so, and it is why the Status column was dropped — so every visible
row is open and `Open P&L` is exactly right, matching `/desk`. The Paper Ledger
keeps closed rows, and its `trade_pnl` returns *realized* for those, so `Open
P&L` would be wrong there for half the book. Same words would have been the
inconsistency; different words track a real difference.

## 2. Buttons and toasts

| was | now |
|---|---|
| `Refresh marks (live)` | **Reprice now** — "marks" is jargon, and the page's own status line already says "Repricing…" |
| `Close requested.` | `Closing {symbol} — the ledger updates when the engine confirms.` (the Paper Ledger's wording, for the same reason: these are `cmd:options` commands) |
| `Reload`, `Close selected`, `Select a signal first.`, `Reloading captured signals…`, `Repricing open signals…` | unchanged — already plain |

## 3. Out of scope

The four footer figures (`Opened today` / `Closed today` / `P&L today (booked)` /
`P&L today (open)`) are already the clearest copy on the page, including the
em-dash-not-$0.00 rule for an unpriced book. Every colour map, the badge
vocabulary (`TAKE_PROFIT` / `HOLD` / `CUT` render as-is), and the raw payload
keys are unchanged.

## 4. Verification

Not browser-verified — a worktree resolves to prod and would bind `:8500`.
Baseline for `test_options_captured.py`: **62 passed**.
