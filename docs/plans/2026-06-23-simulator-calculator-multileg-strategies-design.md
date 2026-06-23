# Simulator + Calculator multi-leg strategies — design

**Date:** 2026-06-23
**Branch:** `Using_Highcharts`
**Status:** approved (design); plan to follow.

## Goal

Upgrade the **Options Simulator** (`/options/simulator`) and **Calculator**
(`/options/calculator`) so both can build, price, and analyze **multi-leg
strategies** — verticals (credit *and* debit), condors (iron + all-same),
butterflies (long 1-2-1 + iron), and **calendars/diagonals** (per-leg expiry) —
with **editable legs** and a **copy-legs button both ways** (Simulator ↔
Calculator).

## Why now / current state

- **Simulator.** The engine (`options-scanner/options_simulator/engine.py`)
  already prices *arbitrary* multi-leg positions — `Position(legs=[...])` +
  `aggregate_position()` sign-flip-and-sum every Greek per leg, and a
  `Position.vertical()` constructor exists. But the **webgui page only ever
  builds `Position.single`** (one leg, buy/sell). The legacy Tk window exposed
  Call/Put **spreads**; the webgui port dropped that.
- **Calculator.** Already has a strategy/leg-template model (`LEG_SPECS`: PCS,
  CCS, **IC**, singles) and a generic-over-legs P&L grid (`calc_spread_pnl`).
  But it is **missing butterfly + calendar**, all legs share **one expiry**, and
  the summary tiles (`calc_summary`/`_estimate_pop`) are **hard-coded per
  strategy** (no generic path).
- The snapshot (`fetch_snapshot`) already pulls **all expiries within 90 days**,
  so calendars are feasible without new fetch plumbing.

### Three technical wrinkles that shape the design

1. **Calendars need per-leg time.** `WhatIfEngine.sweep()` (Simulator) and
   `calc_spread_pnl()` (Calculator) both apply a **single T / single expiry to
   every leg** — correct for same-expiry structures, **wrong for calendars**
   (front and back legs have different DTE). IV-shock and Replay already compute
   `T` per leg from `contract.expiry`, so they are calendar-safe. Only What-if and
   the calc grid need a per-leg-time change.
2. **Ratio legs.** A long butterfly is **1-2-1** — the body leg trades at **2×**.
   The engine's `Leg` only carries `sign` (±1), no quantity, so ratio strategies
   can't be expressed today. `calc_spread_pnl`/`calc_summary` already read a
   per-leg `qty`, so only the Simulator engine needs the change.
3. **Summary metrics for new structures.** Butterfly/calendar/condor/debit
   verticals have **no closed-form** `calc_summary` today (calendars have no
   simple max-loss/breakeven at all — the risk graph is the value-at-front-expiry
   curve). A **generic numeric** summary is required; the existing analytic path
   for PCS/CCS/IC/singles is kept for exactness + zero regressions.

## Approach (chosen: A — shared model + shared editor)

One **pure** shared leg model and **one parameterized leg-editor widget** that
both pages mount. Rejected: (B) duplicate per page — templates/copy-format drift,
double the tests, and the explicit ask is the *same* thing in both; (C)
templates-only without editable legs — the user chose editable legs.

## Components

### 1. Shared strategy/leg model — `webgui/pages/options/strategies.py` (pure, Tier-1)

- **Normalized leg dict** — the lingua franca for both pages *and* the copy
  payload:
  `{option_type: "call"|"put", side: "long"|"short", strike: float|None,
    expiry: "YYYY-MM-DD"|None, qty: int, premium: float|None}`.
- **`STRATEGY_TEMPLATES`** — name → ordered leg specs carrying *roles*
  (`strike_role` ∈ {atm, wing_lo, wing_hi, body, inner, outer, …},
  `expiry_role` ∈ {near, far}, `qty`). Families:
  `LONG_CALL/PUT`, `NAKED_CALL/PUT`, `VERT_CALL_DEBIT/CREDIT`,
  `VERT_PUT_DEBIT/CREDIT` (PCS/CCS kept as aliases), `IRON_CONDOR`,
  `CONDOR_CALL/PUT`, `BUTTERFLY_CALL/PUT` (1-2-1), `IRON_BUTTERFLY`,
  `CALENDAR_CALL/PUT`, `DIAGONAL_CALL/PUT`.
- **`build_default_legs(template, spot, strikes, expiries)`** → normalized legs
  with ATM-centered strikes (and near/far expiries for calendars/diagonals), each
  strike snapped to the nearest available. Pure, unit-tested.
- **`summary_code(template, legs)`** → the analytic strategy code (`PCS/CCS/IC/…`)
  when legs still match a canonical template, else `"CUSTOM"`. Routes the
  Calculator summary between exact-analytic and generic-numeric.

### 2. Shared editable leg-editor — `webgui/pages/options/leg_editor.py` (Tier-1)

`build_leg_editor(container, *, strikes_for, expiries_for, show_premium,
on_change)` renders one row per leg — **kind · side · strike(select) ·
expiry(select) · qty(number) · [premium] · remove** — plus **＋ Add leg** and a
**Strategy** dropdown that repopulates rows from a template. Returns a handle
exposing `get_legs()` / `set_legs(legs)`. Each page injects its own data sources:
`strikes_for(expiry, otype)` / `expiries_for()` (Simulator ← snapshot meta;
Calculator ← cached chain) and `show_premium` (Calc yes / Sim no).

### 3. Engine + Simulator compute (Tier-2)

- **Engine (`options_simulator/engine.py`):** add `ratio: int = 1` to `Leg`;
  `aggregate_position` multiplies each leg's Greeks by `sign * ratio` (enables
  1-2-1 butterflies). Add a generic `Position.from_legs(...)` builder.
- **`compute.sim_run` / `compute.sim_replay`:** accept `legs: list` (each
  `{kind, strike, expiry, side, qty}`) instead of single `kind/strike/direction`.
  Resolve each leg to a `ContractRow` in the snapshot (calendars pull
  different-expiry contracts — already present in the 90-day snapshot) and build
  the multi-leg `Position`. **What-if fix:** the `Δt` slider becomes *elapsed days
  from now*; each leg is priced at `T_leg = max(current_dte_leg − Δt, floor)/365`
  via a per-leg lambda in `aggregate_position`. Single/same-expiry behavior is
  unchanged; calendars now decay each leg by its own clock. Replay + IV-shock take
  the multi-leg `Position` directly (already per-leg T). Any leg with `iv ≤ 0`
  keeps the existing "IV unavailable" message.
- **`sim_fetch`** is unchanged (already returns the full per-expiry strikes map).

### 4. Calculator compute (Tier-2)

- **`calc_spread_pnl`:** add optional **per-leg expiry** → per-leg, per-column
  time-to-expiry (`T_leg(col) = max((leg_expiry_settlement − col_datetime)/yr,
  0)`). For calendars the eval columns run **Now → nearest (front) expiry**; the
  back leg is BS-priced at its remaining `T` in each column (standard calendar
  risk graph). Same-expiry behavior is byte-identical to today (single expiry ⇒
  same T for every leg).
- **Summary:** keep the exact analytic path for `PCS/CCS/IC/singles` (no
  regressions); add **`calc_summary_generic`** (numeric) for
  butterfly/condor/debit-vertical/calendar/diagonal/`CUSTOM` — max-profit /
  max-loss / breakevens read off the dense **value-at-front-expiry** curve
  (sign-change crossings = breakevens), PoP via the risk-neutral lognormal CDF
  over the profit region. Routed by `summary_code`.
- **`calc_compute`** grows per-leg `expiry`/`qty` on each leg, falling back to the
  page-level expiry when a leg omits it (back-compat).

### 5. Cross-page copy — `webgui/pages/options/handoff.py`

- Add a `simulator` stash + `take_pending_simulator()` and a normalized leg
  payload `{symbol, legs:[...]}`.
- **Calculator → Simulator** button: stash legs+symbol → navigate to
  `/options/simulator`; on arrival the Sim auto-enqueues `sim_fetch`, and once
  meta lands applies the legs to its leg-editor (premiums dropped — Sim reprices
  from IV).
- **Simulator → Calculator** button: stash legs+symbol → navigate to
  `/options/calculator`; on arrival the Calc auto-enqueues `calc_load`, then
  applies legs and auto-**Fetch Premiums** from its own chain.
- Reuses the existing single-user `_pending` stash pattern.

### 6. Page wiring

- **Simulator page:** the single-contract selector row (expiry/kind/strike/dir
  toggles) is **replaced** by the Strategy dropdown + editable leg-editor. The
  three tabs (Replay / What-if / IV-shock) operate on the built multi-leg
  position. The `Δt` label/semantics change to elapsed days. A **Copy to
  Calculator** button is added.
- **Calculator page:** the fixed `LEG_SPECS` rows are replaced by the shared
  editable leg-editor (with the premium column); the Strategy dropdown gains the
  new families. A per-leg **Expiry** select appears (single-expiry strategies
  default every leg to the page expiry; calendars/diagonals set near/far). A
  **Copy to Simulator** button is added.

## Decisions (confirmed)

- **Build model:** templates **+ editable legs** (pick a template, then add /
  remove / edit legs).
- **Strategy menu:** all four families (verticals incl. debit, condors incl.
  iron + all-same, butterflies incl. iron, calendars + diagonals) plus the
  existing singles + PCS/CCS/IC.
- **Simulator selector:** **replaced** by strategy + leg editor.
- **What-if `Δt`:** changes from absolute-DTE to **elapsed days from now**
  (per-leg decay) — a deliberate behavior change, justified by calendars and the
  "Δt" label.
- **Calculator calendar IV (v1):** **one IV** for both expiries; per-leg IV is a
  possible later follow-up.

## Testing

Pure units: `strategies.py` (templates, default legs, `summary_code`),
`leg_editor` get/set, `calc_summary_generic` (butterfly/calendar max-loss +
breakevens vs hand-computed), `calc_spread_pnl` per-leg-expiry (calendar
front-expiry column), `sim_run` multi-leg + calendar Δt, engine ratio
aggregation. The existing PCS/CCS/IC analytic suites must stay green (analytic
path untouched). Live-verify a calendar + an iron butterfly end-to-end via the
Redis command path (`Bus().enqueue_command` → `cache:options:*`).

## Risks / call-outs

- **What-if Δt semantic change** — flagged; the only behavior change to an
  existing view.
- **Calendar IV** — each Simulator leg uses its own contract IV (from the chain);
  the Calculator grid uses the single IV field for v1.
- **Shared widget parameterization** — the leg-editor must serve two different
  data sources cleanly (snapshot meta vs cached chain); the `strikes_for` /
  `expiries_for` callback seam keeps the pages decoupled from the widget.
