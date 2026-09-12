# Calculator + Simulator — shared trade-entry panel (design)

**Date:** 2026-09-12 · **Status:** approved · **Plan:** [the plan](2026-09-12-calc-sim-entry-panel-plan.md)

## Problem

Entering a trade on `/options/calculator` and `/options/simulator` is slow, and
the operator named all four causes:

1. **Strike dropdowns.** Every leg's strike is a `ui.select` over the whole
   ladder — often 100+ entries to scroll.
2. **Too many button steps.** Type symbol → LOAD CHAIN → pick expiry → FETCH
   PREMIUMS → CALCULATE (and IV UPDATE). Nothing happens until you ask.
3. **No chain to pick from.** Legs are built blind; bid/ask/delta/OI are not on
   screen.
4. **Keyboard / layout.** Two-line leg cards, a crowded three-column card on the
   Simulator, and the two pages entering the same thing differently.

Primary device is **desktop** (wide screen, mouse + keyboard).

## Decision

**One shared entry panel mounted by both pages** (approach A of three; B "fix in
place" kept two divergent pages, C "merge the pages" rippled into navigation,
handoffs and the manuals). Each page keeps its own results area below.

## Layout (grid and legs side by side)

```
┌─ ENTRY ─────────────────────────────────────────────────────────────────────┐
│ [SPY ⏎]  Spot 571.24   Strategy [Put Credit Spread ▾]          ⟳ quotes     │
│ Expiry  ‹ Sep 12·0d │ Sep 15·3d │ Sep 19·7d │ Oct 17·35d … ›            ⚙   │
├──────────────── CHAIN (Sep 19) ──────────────┬──────── LEGS ────────────────┤
│   CALLS            │ STRIKE │      PUTS      │ # B/S QTY EXP STRIKE C/P PRICE│
│ Bid  Ask  Δ    OI  │  566   │ Bid  Ask  Δ OI │ 1 SELL ‹1› Sep19 ‹565› PUT 2.07│
│  (ITM shaded, spot line)                     │ 2 BUY  ‹1› Sep19 ‹560› PUT 1.12│
│ click Bid → SELL leg · click Ask → BUY leg   │ + Add leg  + Add shares (Calc)│
└──────────────────────────────────────────────┴───────────────────────────────┘
  Calculator: metric cards + P&L matrix     Simulator: Replay / What-if / IV shock
```

- **Symbol bar** — ticker (Enter loads), spot readout, strategy picker, a small
  ⟳ that re-pulls quotes. The Calculator's rarely-changed inputs (IV %, rate,
  IV Δ, contracts, strike window) move to a **collapsed "Pricing assumptions"**
  row. The Simulator's sliders stay in its results area.
- **Expiry strip** — pills of date + days to expiry; selecting one points the
  grid at that date. ⚙ opens the column picker.
- **Chain grid** (new) — calls | strike | puts, centred on spot, ITM rows shaded,
  a spot line. Default columns **Bid, Ask, Delta, OI**; the picker adds Mark,
  IV, Gamma, Theta, Vega, Volume and the choice persists in `app_settings`.
  **Click Bid → SELL leg; click Ask → BUY leg.**
- **Leg table** — a new `layout="table"` in `leg_editor.py`, one row per leg,
  replacing `layout="card"` on both pages: B/S and C/P one-click toggles, Qty
  with ‹ ›, Strike type-to-filter with ‹ › stepping to the adjacent REAL chain
  strike, Price auto-filled and editable (a "manual" marker + reset when typed),
  Delta, ✕. **Rescue keeps `layout="row"` untouched.**

## Behaviour

1. **Enter on the ticker** loads the chain, keeps the selected expiry if the new
   chain lists it (else the nearest), and lays the template legs around ATM.
   (No template carries a "usual DTE" in the code, so none is invented here.)
2. **Prices fill from the chain** — mark, else bid/ask mid (today's
   `extract_premium` rule).
3. **Auto-recalculate**, debounced ~300 ms, on any edit. LOAD CHAIN, FETCH
   PREMIUMS, CALCULATE and **IV UPDATE are removed** (IV is implied from the mark
   on load).
4. **Strike/expiry change re-fills that leg's price**, unless the price was
   typed; a typed price survives until reset.
5. **A grid click prices at the MARK whichever side was clicked** — the side
   decides only long/short. Pricing a sell at the bid would understate every
   trade by the full spread and disagree with the mark-implied IV. A click marks
   the legs edited → the strategy reads "Custom", as manual edits already do.
6. **Keyboard:** Tab order ticker → strategy → leg rows; ↑/↓ in Strike and Qty
   step; Enter commits. No keyboard navigation inside the grid (YAGNI — desktop,
   mouse).

## Data

- **Calculator:** `calc_load` keeps its one `/chains` call. `thin_calc_chain`'s
  whitelist grows from 5 fields to **bid, ask, mark, volatility, delta, gamma,
  theta, vega, openInterest, totalVolume** — est. 0.68 → ~1.3 MB, still far below
  the 8.77 MB unthinned payload that motivated the thinning.
- **Simulator:** `sim_fetch` already fetches the chain inside
  `options_simulator.data.fetch_snapshot` but keeps only strike/kind/bid/ask/iv.
  It will also publish the thinned chain to a **new `cache:options:sim_chain`**,
  from the **same** call (no extra Schwab request). Its own key, so loading a
  symbol on one page never changes the other page's chain.
- **Handoffs** (Scanner / Strategy Finder / Calculator ↔ Simulator) keep working;
  incoming legs are held until the chain has loaded, since setting legs first
  wipes their strikes.
- **Stock legs:** "Add shares" on the Calculator only; a share row's strike and
  expiry are disabled. Simulator and Rescue still exclude stock.

## Errors

- A symbol that fails to load → reason in the status line; existing legs stay.
- A leg whose strike is not in the new chain → warning marker, price untouched
  (never zeroed).
- Off-hours zero or `-999` values → "—" in the grid; a click still adds the leg
  at the mark.

## Testing

Pure, test-first: `chain_grid_rows`, `step_strike`, `leg_from_pick`, the
price-override rule, the debounce, column-preference parsing, the widened
`thin_calc_chain`, `sim_fetch` publishing `sim_chain` from one chain call
(call-counted), handoff-waits-for-chain. Both pages stay in
`test_no_inline_style.py`. Compare the failing SET before/after for webgui,
options_svc and options-scanner.

**Runtime check:** there is no separate dev stack, so both pages are rendered
locally with the page harness (fake bus + real handlers + synthetic chain) and
driven in the browser — Enter to load, grid click, strike step, recalc fires —
before any promote.

## Docs touched

`webgui/page_help.py`, User Guide, Reference Guide, `docs/webgui-routes.md`,
CHANGELOG, and the `CLAUDE.md` lines describing the Calculator's steps and the
leg editor's `card` layout.

## Delivery

Branch commits in stages (service → shared components → Calculator →
Simulator). No promote without the operator's go-ahead; promote restarts the
whole stack, so on a trading day it waits for 15:25–16:15 CT.
