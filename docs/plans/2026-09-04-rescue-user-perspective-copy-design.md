# Rescue — rewording the screen from the reader's perspective (design)

**Date:** 2026-09-04
**Scope:** `/options/rescue`. Seventh page in the pass
([Desk](2026-09-04-desk-user-perspective-copy-design.md) ·
[Flow Alerts](2026-09-04-flow-alerts-user-perspective-copy-design.md) ·
[Opportunity Board](2026-09-04-opportunity-board-user-perspective-copy-design.md) ·
[Paper Ledger](2026-09-04-paper-ledger-user-perspective-copy-design.md) ·
[Captured Signals](2026-09-04-captured-signals-user-perspective-copy-design.md) ·
[Market Scanner](2026-09-04-market-scanner-user-perspective-copy-design.md)).

## Why this page's copy carries more weight than the six before it

Every previous page **reports**. This one **executes** — Apply mutates the paper
book. So a message that leaves the reader unsure what happened is not a
legibility problem here, it is a correctness-of-understanding one.

## 1. ⚠ The stale-price guard does not say that nothing happened

The most important finding on this page. When prices have drifted since the
candidate was priced, the apply **aborts without mutating** — stated twice in
the engine:

- `options-scanner/paper_adjust.py:475` — *"stale (re-review) and nothing is
  mutated"*
- `services/options_svc/handlers.py:1682` — *"its built-in stale-price guard
  aborts (`stale`) without mutating when prices have drifted"*

The reader is told **"Prices moved — re-review"**. That says prices moved. It
does not say the adjustment was refused, and on a page whose Apply button had
just been pressed, "re-review" is as easily read as *"it went through, go look
at it"* as *"nothing happened"*.

| where | was | now |
|---|---|---|
| toast | `Prices moved — re-review` | `Prices moved — nothing was applied. Check the new numbers and try again.` |
| `summary_line` prefix | `Prices moved — re-review · ` | `Nothing applied — prices moved · ` |

The summary prefix stays short because it sits in front of a headline that
continues; the toast is where the full sentence belongs.

## 2. Two labels are wrong, and one is inconsistent with the page's own cards

- **`Strike Date` holds `expiration`.** It is not a strike date — there is no
  such thing on a spread with two strikes. It becomes **Expiry**, the word the
  five previous pages settled on. Third page in a row with a header that names
  the wrong quantity.
- **`Δ short` and `Short delta` are the same number on one screen.** The at-risk
  table says `Δ short`; the candidate cards' own metric list says `Short delta`
  (`_CANDIDATE_METRICS`). The table takes the cards' word.
- **`Comm`** is a casual shortening → **Commission**.

## 3. Column headers (`at_risk_columns`)

| was | now | why |
|---|---|---|
| `Symbol` | `Symbol` | already plain |
| `Strat` | **Strategy** | matches the four pages before it |
| `Strikes` | `Strikes` | already plain |
| `Strike Date` | **Expiry** | it holds `expiration` — see above |
| `Δ short` | **Short delta** | the page's own cards already call it that |
| `P&L` | **Open P&L** | every row here is an at-risk *open* position, so this is the Captured Signals case, not the Paper Ledger one |
| `Heat` | `Heat` | the page's own 0–100 concept, explained in the help and used throughout the module |
| `State` | **Risk state** | "State" names nothing. ⚠ Deliberately **not** "Status" — the Paper Ledger's `Status` column means OPEN/CLOSED, and reusing the word for TESTED/CRITICAL would put one name on two different things |

## 4. The Apply dialog and the advisory-only cards

| was | now |
|---|---|
| `This dispatches a (simulated) paper adjustment.` | `This adjusts your paper position. No real money, and no live order is placed.` |
| `manual — place yourself` | `Manual — you place this one yourself` |

The dialog line was implementation-speak for the single most reassuring fact on
the page. The card label marks candidates the app will not execute for you, and
lower-case "manual — place yourself" reads as a note rather than as the
instruction it is.

## 5. Out of scope

`Gross` / `Net` stay: they sit in a money row beside `Commission`, which makes
the pairing self-evident. The candidate metric labels (`Max loss after`,
`Breakeven`, `Width`, `Expiry`, `DTE after`) are already correct — and
`Max loss after`'s suppression on a full close is a piece of care worth leaving
untouched. `_ADHOC_STRUCT_ERR`, the two subtab labels and their tooltips, every
colour map, and the raw payload keys are unchanged.

## 6. Verification

Not browser-verified — a worktree resolves to prod and would bind `:8500`.
Baseline for `test_rescue.py`: **44 passed**.
