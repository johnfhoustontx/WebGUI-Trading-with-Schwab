# Opportunity Board — rewording the screen from the reader's perspective (design)

**Date:** 2026-09-04
**Scope:** `/options/matrix`. Third page in the pass
([Desk](2026-09-04-desk-user-perspective-copy-design.md) ·
[Flow Alerts](2026-09-04-flow-alerts-user-perspective-copy-design.md)).

## The problem

Thirteen columns, and several name a field rather than a reading:

- **`Call` and `Put` are not call and put anything** — they hold call/put
  *acceleration* arrows (hot / cool / steady / flat). A reader scanning the
  header row has every reason to expect a price or a volume.
- **`Sig` and `Signal` are different quantities two columns apart.** `Sig` is a
  count of live scanner signals; `Signal` is the buy/neutral/sell verdict.
- **`GEX`** labels a column whose values are literally `above` / `below` — which
  side of the dealer gamma flip price sits on.
- **`Hot`**, **`Spot`**, **`Net $M`** and **`Ticker`** each disagree with what
  `/desk`'s board panel calls the same quantity.

## 1. One home for the shared waiting line

This page carries the **third** byte-copy of "Waiting for the options service…",
after `/desk` and `/options/flow`. `desk.py` imports both `pages.options.flow`
and `pages.options.matrix`, so neither can import back — which is why the Flow
pass settled for a guarded copy.

Three copies is past the point where a guard is the right answer. **New leaf
module `webgui/pages/copy.py`**: shared user-facing text, importing nothing from
`pages`, on the model `pages/fmt.py` already set for shared *numeric* vocabulary
("the ONE copy"). All three pages import `WAITING_OPTIONS` from it;
`desk.WAITING_OPTIONS` stays as an alias, because `desk_stream` and several tests
address it there and that name is not wrong. `flow._WAITING` and its guarded-copy
test are deleted — the guard existed only because the copy did.

## 2. Column headers

| was | now | why |
|---|---|---|
| `Ticker` | **Symbol** | the field is `symbol`; `/desk` says SYMBOL |
| `Spot` | **Price** | `/desk` renamed SPOT→PRICE in the first pass |
| `Day %` | `Day %` | already plain |
| `Trend` | `Trend` | already plain |
| `Call` | **Call flow** | it is call ACCELERATION; "Call" alone reads as a price |
| `Put` | **Put flow** | same |
| `P/C` | `P/C` | trader acronym, kept per the standing rule |
| `Net $M` | **Net premium $M** | `/desk` says NET PREMIUM; the unit stays because the cell is a bare number in millions |
| `GEX` | **Vs flip** | the values ARE above/below the flip |
| `Sig` | **Open signals** | it is a COUNT, and the Scanner calls them signals too |
| `Flow` | **Flow alerts** | a count of alerts, not an amount of flow |
| `Signal` | `Signal` | the verdict — unchanged, because `/desk`'s board panel header says SIGNAL for this same value |
| `Hot` | **Score** | `/desk` says SCORE |

`Open signals` / `Signal` was chosen over renaming the verdict to `Verdict`:
`Verdict` is arguably the clearer word, but `/desk` already prints SIGNAL for
this quantity, and introducing a second name for it is the drift the first two
passes closed.

No width constraint: this is a `ui.table`, not the Desk's fixed `minmax()` grid.

## 3. Head and status line

| was | now |
|---|---|
| eyebrow: `Every watchlist stock at a glance — sorted by hotness` | `Every watchlist symbol on one row, ranked by how much is going on. Click any column to re-sort.` |
| `Waiting for the options service…` | the shared `copy.WAITING_OPTIONS` |
| `{n} symbols · session {date} · updated {time}` | unchanged |

The eyebrow gains the sort affordance because this page has **no row
click-through** — sorting is the only thing a reader does here, and nothing on
screen currently says so.

## 4. Out of scope

`_SIGNAL_LABEL` (Buy / Neutral / Sell), the summary band's "Signals" caption, the
trend and acceleration arrow glyphs, and every colour map. The raw payload keys
are untouched, as on the previous two pages.

## 5. Verification

Not browser-verified — a worktree resolves to prod and would bind `:8500`.
Baseline for the affected suites (`test_options_matrix.py`, `test_desk.py`):
**258 passed**.
