# Calculator + Simulator — full state persistence across navigation (design)

**Date:** 2026-06-24
**Branch:** `Using_Highcharts`
**Status:** approved (brainstorm)

## Problem

The `/options/calculator` and `/options/simulator` pages rebuild **all UI state
fresh on every visit**. `render()` builds a local-closure `state` dict, the symbol
input defaults to `SPY`, the Strategy picker to `PCS`, and the leg editor is
re-seeded from the strategy template. The *results* live in Redis
(`cache:options:calc_result` / `cache:options:sim_result`), so the grid/chart can
repaint — but the **inputs that produced them do not survive navigation**:

- **Calculator**: the P&L grid repaints from the cache, but symbol / strategy /
  legs / IV / rate / range reset to defaults → the grid and the inputs no longer
  describe the same trade.
- **Simulator**: worse — on return `_apply_meta` re-seeds the leg editor to the
  `PCS` template (`not editor.is_dirty()` is always true on a fresh render) **and
  re-enqueues a `sim_run`**, clobbering the last result with a default-template run.

So navigating away and back loses the user's work.

## Goal (confirmed decisions)

1. **Restore everything as the user left it** — symbol, strategy, every leg
   (strike/side/qty/expiry/premium), all input fields (IV/rate/range/price), slider
   positions (Δt / IV-mult / look-back / ΔS), the active Simulator tab, and the
   result/chart.
2. **Auto-refresh on return** — after restoring the inputs, immediately re-run the
   compute/sweep against **current market data** every time the page is reopened
   (the user accepted the extra proxy calls + brief spinner). This means the fix is
   "re-run with the *restored* inputs", not "re-run with the reset template".

## Approaches considered

| Approach | Survives | Trade-off | Verdict |
|---|---|---|---|
| **Module-level state dict** (per page) | navigation + browser reload; resets on webgui restart | The established single-user house pattern (Sentiment `_CACHE`, Trade, `_NAV_OPEN`). Tier-clean — the GUI never writes Redis. | **Chosen** |
| Redis "ui-state" key (`calc_inputs`/`sim_inputs`) | + webgui restart | The GUI writing cache keys breaks tier discipline (GUI only *reads* cache + enqueues commands); cross-restart persistence wasn't requested | Rejected (YAGNI) |
| NiceGUI `app.storage.user`/`tab` | + webgui restart | A new mechanism used nowhere else here; needs a storage secret | Rejected (YAGNI) |

## Design

### Mechanism

A module-level snapshot per page, updated on every input change and restored on
render — single-user, exactly like the other persisting pages:

```python
# pages/options/calculator.py
_LAST_CALC: dict | None = None     # last full input snapshot (single-user)
# pages/options/simulator.py
_LAST_SIM: dict | None = None
```

### Captured state

- **Calculator** `_LAST_CALC`: `symbol`, `strategy`, `legs[]` (full leg dicts),
  `iv`, `rate`, `ivadj`, `contracts`, `price`, `num_strikes`, `expiry`. (The old
  `range_*` controls are replaced by `num_strikes` — see the addendum below.)
- **Simulator** `_LAST_SIM`: `symbol`, `strategy`, `legs[]`, `dt`, `mult`,
  `lookback`, `ds`, `active_tab`.

The **result** is not snapshotted into module state — it already persists in Redis,
and the auto-refresh produces a fresh one on return.

### Capture

A `_capture()` helper reads the current widget values + `editor.get_legs()` into the
module dict. It is wired to every `on_value_change` (symbol, strategy, each field,
each slider), the leg-editor `on_change`, and the Simulator tab change. It is a cheap
dict write (no command, no Redis).

### Restore + auto-refresh on `render()`

1. **Resolve the seed source** by precedence — **handoff copy-pending → `_LAST` →
   `SPY`/`PCS` defaults**. An explicit "Copy to Calculator/Simulator" is a fresh
   intent and still wins over the persisted snapshot.
2. **Apply the snapshot under a `restoring` guard** so the change handlers that get
   wired don't fire stray commands mid-restore: set the symbol / strategy / field /
   slider / tab widgets, `editor.set_legs(restored_legs)`, and paint the **cached**
   result immediately (no empty flash).
3. **Auto-refresh**: trigger the existing fetch path (`sim_fetch` / `calc_load`) for
   the restored symbol. The restored legs ride the **existing `pending_legs` hook** —
   which already takes precedence over the template re-seed and survives the
   chain/meta load (it is how Copy-to-Calculator/Simulator already injects legs) — so
   the cascade re-runs with the user's legs, and the restored sliders feed the run.
   The fresh result version-polls in and repaints in place.

### Details / edge cases

- **Leg coercion**: restored legs coerce to the freshly-fetched chain's
  strikes/expiries via the editor's existing `coerce_strike` / `coerce_choice` (a
  strike may have rolled off the ladder since last visit).
- **Simulator scrub cursor**: resets on auto-refresh — the replay trace is newly
  fetched, so a stale integer cursor index is meaningless.
- **First-ever visit** (no `_LAST`): unchanged `SPY`/`PCS` default behavior.
- **Lifetime**: survives navigation + browser reload (module state is server-side in
  the webgui process); resets on a webgui restart — same as every other persisting
  page.

### Testing

- Pure unit tests: the **seed-precedence resolver** (handoff > `_LAST` > defaults)
  and **leg coercion** against a chain that dropped a strike.
- End-to-end: Redis-driven check (enqueue fetch/run, confirm the restored legs drive
  the result) + a browser navigate-away/back screenshot.

### Scope

Only `webgui/pages/options/calculator.py` + `webgui/pages/options/simulator.py`
(+ `webgui/tests/`). **No** service, contract, or engine changes — purely GUI-tier.

## Addendum (2026-06-24): Calculator range → "Number of strikes"

Folded in before implementation. The Calculator's **Range min / Range max / Range %**
controls are replaced by a single **Number of strikes** input (default **24**,
configurable), and the P&L grid's price rows become **24 actual chain strikes either
side of spot**.

**Confirmed decisions**
- **Rows = actual chain strikes** — each grid row is a real option strike from the
  loaded chain: the `n` strikes ≤ spot plus the `n` strikes > spot (spacing follows
  the real ladder). When no chain is loaded yet, fall back to the engine's existing
  even-step heuristic over ±`n` rows so the grid still renders.
- **Strictly ±`n` around spot** — the count is the zoom. The grid is **not** widened
  to keep far-OTM legs visible (today's `symmetric_price_range` leg-widening is
  dropped for this page); a leg beyond ±`n` strikes falls off until the user raises
  the count.

**Mechanics**
- Pure `strikes_window(strikes, spot, n)` (page-side) → the ±`n` strike list around
  spot, computed from the cached chain's front-expiry ladder (union of call+put
  strikes).
- Engine `options-scanner/options_calculator.py:calc_spread_pnl` gains a
  `price_rows=None` parameter: when provided it is used verbatim as the grid rows
  (sorted/deduped/rounded), bypassing the heuristic step-gen; absent → existing
  `price_range`/`rows_per_side` behavior (the fallback). Additive + back-compat.
- Service `services/options_svc/compute.py:calc_compute` takes `num_strikes`
  (default 24) + optional `price_rows`, replacing `range_min`/`range_max`/
  `range_pct`; it forwards `price_rows` to the engine, falling back to
  `rows_per_side=num_strikes` over a wide-open `price_range` when `price_rows` is
  absent. `handlers.calc_compute` forwards the new args.

**Effect on persistence**: simplifies it — there is no longer a range overwrite in
`_apply_chain` to gate, so the `restore_pending` range-gate is unnecessary;
`_CALC_KEYS` carries `num_strikes` instead of the three `range_*` fields.

## Non-goals (YAGNI)

- Cross-webgui-restart persistence (Redis/disk/browser storage).
- A staleness threshold on the auto-refresh (it re-fetches on every return, as
  chosen; can be added later if rapid page-bouncing proves too chatty).
- Persisting transient client-only cursor positions beyond what "as I left it"
  meaningfully needs (the scrub cursor resets with the new trace).
