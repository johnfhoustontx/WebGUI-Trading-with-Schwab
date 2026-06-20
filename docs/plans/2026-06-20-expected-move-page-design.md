# Expected Move page — Design

**Date:** 2026-06-20
**Status:** Approved (brainstorm)
**Branch:** `Using_Highcharts`

## Goal

A new **Expected Move** page that, for a given symbol + option strike(s) +
expiration, draws a **candlestick** price chart of recent history with a forward
**expected-move cone** projected to the option's expiration, plus the leg strikes
as horizontal reference lines and an interactive crosshair.

The page is reached from a button on the **Scanner**, **Paper Trades**,
**Captured Signals**, and **Calculator** pages (each hands off the symbol +
strike(s) + expiry it already has). It opens in a **new browser tab**.

This mirrors the legacy "Expected Move" visual (historical OHLC + dashed upper/
lower EM cone) but renders **candles** instead of OHLC bars and adds an axis
crosshair.

## Decisions (from brainstorm)

| Question | Decision |
|----------|----------|
| EM cone basis | **ATM IV → to expiration.** Cone width at horizon `t` = `spot × atm_iv × √(t/365)`, anchored at today's spot, fanning out to the option's expiration. |
| Strike shown | **All legs as horizontal lines** (short = solid, long = dashed; calls vs puts color-coded). The selected strike(s) are reference lines; the cone itself uses ATM IV, not per-strike IV. |
| Navigation | **New browser tab** via a module-level stash handoff (like "Send to Calculator"), plus a left-nav item under **Options**. |
| History window | **6 months** of daily candles by default. |
| Chart timeframe | **Daily only** for now; the figure builder takes a `timeframe` param so intraday can be added later. |
| Placement | **Standalone page** (`/options/expected-move`), not a Simulator sub-tab. |
| Chart library | **Highcharts** (`ui.highchart`) with `extras=["stock"]` — the bundled stock module provides the `candlestick` series type and the axis crosshair labels. |

## Architecture & data flow (Tier-3)

The webgui page is a thin reader: it imports only `nicegui` + `bus_client`. All
market-data work happens in `services/options_svc`. On-demand, latest-result
model — identical to Calculator (`calc_result`) / Simulator (`sim_result`) /
Trade (`trade:analysis`).

```
[Scanner | Paper | Captured | Calculator]
        │ build normalized payload {symbol, expiry, legs}
        ▼
handoff.send_to_expected_move(payload)
        │ stash _pending["expected_move"]; ui.navigate.to(url, new_tab=True)
        ▼ (new browser tab = fresh request)
expected_move.render()
        │ take_pending_expected_move()
        │ bus_client.request("options", {type:"expected_move", args:{symbol,expiry,legs}})
        ▼
options_svc: handle_command → compute.compute_expected_move
        │  • proxy.get_daily_history(symbol, months=6)  → candles
        │  • spot = latest close (live quote if available)
        │  • ATM IV for `expiry` from the option chain (extract_atm_iv logic)
        │  • build forward cone (today → expiry)
        │  • cache_set("cache:options:expected_move", payload, event=…)
        ▼
expected_move.render(): version-poll options:expected_move → repaint chart
```

**Normalized handoff payload**

```python
{
  "symbol": "SPY",
  "expiry": "2026-07-18",              # ISO date string
  "legs": [
    {"strike": 540.0, "option_type": "put",  "side": "short"},
    {"strike": 535.0, "option_type": "put",  "side": "long"},
  ],
}
```

Each source builds this from data it already holds:
- **Calculator** — from `leg_inputs` (strike/option_type/side) + `expiry_sel` + `symbol_in`.
- **Scanner / Captured / Paper** — from the signal/position dict (`symbol`,
  `expiration`, `type`, and the per-type strikes: PCS/CCS short+long, IC four
  legs, single-leg variants). A small per-source builder normalizes these into
  the `legs` list (reusing the type→legs knowledge already encoded in
  `calculator.LEG_SPECS` / the signal field names).

## Expected-move math (`options_svc/compute.py`)

`compute_expected_move(symbol, expiry, legs) -> dict` (defensive — degrades to an
`{"error": ...}` payload, never raises):

1. `candles = proxy.get_daily_history(symbol, months=6)` → list of
   `[ts_ms, open, high, low, close]` (drop incomplete/None rows).
2. `spot` = latest live quote if available, else the last candle close.
3. `atm_iv` = ATM implied vol for `expiry` from the option chain. Reuse the
   extraction already proven in `calculator.extract_atm_iv` (nearest-strike
   `volatility`, normalized to a decimal). Fallback to nearest listed expiry; if
   none, error payload.
4. `dte` = `(expiry - today).days`. Build the cone over the forward calendar days
   `t = 1..dte` (one point per day, plus an anchor point at today = spot so the
   cone starts tight):
   - `width(t) = spot × atm_iv × √(t / 365)`
   - `upper(t) = spot + width(t)`, `lower(t) = spot − width(t)`
   - points as `[ts_ms, value]` so they align on the same datetime x-axis as the
     candles.
5. Cached payload:
   ```python
   {
     "symbol", "expiry", "spot", "atm_iv", "dte",
     "candles":   [[ts_ms,o,h,l,c], ...],
     "em_upper":  [[ts_ms, v], ...],
     "em_lower":  [[ts_ms, v], ...],
     "legs":      [{strike, option_type, side}, ...],
     "generated_at": <iso>,
     "error": None,
   }
   ```

A light contract validator is added only if the calc/sim views use one; otherwise
this caches a plain dict like `calc_result`/`sim_result`.

## Chart (`expected_move.py` — pure builders + `render()`)

`expected_move_figure(payload, timeframe="daily") -> dict` (pure, unit-tested):

- `chart.type` candlestick, `backgroundColor` transparent, dark axes (reuse the
  `_DARK_AXIS` idiom from `simulator.py`), `credits`/`accessibility` disabled.
- **Candlestick series** from `candles` (green up / red down).
- **Upper EM** line series (green, `dashStyle:"Dash"`) from `em_upper`.
- **Lower EM** line series (red, `dashStyle:"Dash"`) from `em_lower`.
- **Leg strike lines** — `leg_lines(legs) -> [plotLine,...]` on the yAxis: short
  = solid, long = `Dash`; put vs call color-coded; label = `"{side} {type} {strike}"`.
- **Crosshair** — `xAxis.crosshair` + `yAxis.crosshair` enabled with `label`
  boxes (stock-module feature) so hovering shows the **Date** on the X axis and
  the **Price** on the Y axis; a shared `tooltip` shows the OHLC for the hovered
  candle.
- `xAxis.type = "datetime"`.

`render()` is thin: reads the stashed payload, enqueues the `expected_move`
command, keeps a persistent `ui.highchart` present at page build (per the
dynamically-added-chart gotcha), and version-polls `options:expected_move` to
repaint in place (`el.options = fig; el.update()`). A symbol/expiry/strike input
row lets the page also be used standalone from the nav (manual entry → same
command).

## Files

**New**
- `webgui/pages/options/expected_move.py` — pure figure/cone builders + `render()`.
- `webgui/tests/test_expected_move.py` — builder + render-smoke tests.

**Edit**
- `webgui/pages/options/handoff.py` — `send_to_expected_move(payload)`,
  per-source payload builders, a per-row "Expected Move" action button
  (`show_chart` icon) for the signal tables.
- `webgui/main.py` — `@ui.page("/options/expected-move")` → `expected_move.render()`;
  nav item under Options; add the route to `test_shell.py`'s expected set.
- `webgui/pages/options/scanner.py`, `captured.py`, `paper.py`,
  `calculator.py` — add the Expected Move trigger button/action.
- `services/options_svc/compute.py` — `compute_expected_move(...)`.
- `services/options_svc/handlers.py` — `expected_move` command → compute → cache
  `cache:options:expected_move` + publish.
- `services/options_svc/scheduler.py` / command dispatch — route the new command
  type (no scheduled tick; on-demand only).
- `services/options_svc/tests/` — `compute_expected_move` + handler tests.

## Testing (TDD)

Pure / unit-tested:
- `em_cone(spot, atm_iv, dte, start_ts, ...)` — endpoints, monotonic widening,
  `√t` shape, zero-DTE and zero-IV edge cases.
- `expected_move_figure(payload)` — series types/colors/dash, datetime axis,
  crosshair config, candlestick data shape.
- `leg_lines(legs)` — solid/dash + put/call color mapping per leg.
- The four handoff payload builders — PCS/CCS/IC/single-leg + calculator form →
  normalized `{symbol, expiry, legs}`.
- `compute_expected_move` — fake proxy (candles) + fake chain (ATM IV) →
  expected payload; defensive error paths (no chain, no history, bad expiry).

Smoke:
- `render()` builds without error from an empty cache (graceful-empty) and from a
  sample cached payload.
- `test_shell.py` includes `/options/expected-move` in the route set.

## Out of scope (later)

- Intraday timeframes (builder already parameterized for it).
- Per-strike IV cone (decided: ATM IV).
- Persisting/archiving generated charts.
