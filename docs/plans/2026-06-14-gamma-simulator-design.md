# Gamma & Simulator Pages — Design

**Date:** 2026-06-14
**Status:** Approved
**Builds on:** [`2026-06-14-options-section-expansion-design.md`](2026-06-14-options-section-expansion-design.md)
(replaces the Gamma/Simulator **stubs**).

## Purpose

Port the two heavy Tk tools into the NiceGUI Options section: the **Gamma** tool
(GEX/Charm/DEX/Vanna exposure + intraday heatmap) and the **Simulator**
(What-if price sweep + IV-shock; Replay deferred). Charts use NiceGUI
`ui.plotly`. The compute engines are already pure (chain/snapshot → dicts /
DataFrames), so the pages only marshal results into figures.

## Decisions (from brainstorming)

| Question | Decision |
|----------|----------|
| Charting | NiceGUI `ui.plotly` |
| Gamma scope | Live bar views **+ intraday heatmap** (reads `gex_history_db`) |
| Gamma views | **GEX, Charm, DEX, Vanna** — all four, via a view toggle |
| Simulator scope | **What-if + IV-shock** now; **Replay** deferred |
| Build order | Gamma first, then Simulator; checkpoint between |

## Gamma page (`pages/options/gamma.py`)

Engine: `gamma_tool.GammaEngine` (instance per fetch). `calc_all_from_chain(chain)`
returns `(gex, charm, dex, vanna)` in one pass; each result is
`{"spot", "gex": {strike: {"call","put","net"}}, "strike_count", ...}` (DEX adds
`net_delta_0dte`, `projected_net_delta_close`, `hedge_pressure`).
`snapshot_summary(data, view)` → `{spot, flip, top_pos_strike, top_neg_strike,
net_total, ...}`. Walls: `get_gex_walls` / `get_dex_walls`.

- **Controls:** symbol input + **Fetch**; **view toggle** GEX / Charm / DEX / Vanna.
- **Data flow:** Fetch chain (today..+7) via `proxy.schwab_py_client.get_option_chain`
  → `engine.calc_all_from_chain` → keep all four results in page state; the view
  toggle just re-renders from cached results (instant).
- **Left — bar chart (Plotly):** per-strike **net** exposure as horizontal bars
  within ±N% of spot, colored by sign; **spot line**, **flip line** (where the
  view has one), **wall** markers; hover shows call/put/net.
- **Summary strip:** spot · DTE · strikes · net total · flip · top +/− strikes.
- **DEX extra:** 0-DTE **hedge-pressure** panel (net Δ now / projected at close /
  pressure), colored green/red.
- **Right — intraday heatmap (Plotly):** strike × time from
  `gex_history_db.load_today_with_grid(symbol, view)`. Renders whatever snapshots
  exist; empty → "no snapshots yet (collector not running)" note.
- **TDD (pure):** `bars_from_gex(gex_data, spot, pct)` → (strikes, nets, colors,
  call/put hover); `heatmap_matrix(history_rows)` → (times, strikes, z);
  `summary_text(summary, view)`.

## Simulator page (`pages/options/simulator.py`)

Engine: `options_simulator.data.fetch_snapshot(client, symbol)` → `ChainSnapshot`
(`spot`, `as_of`, `contracts: [ContractRow]`, `price_history`). Scenario engines
take the snapshot: `WhatIfEngine(snapshot).sweep(contract, s_range, t_days)` →
DataFrame[S, theo_price, delta, gamma, theta, vega, rho];
`IVShockEngine(snapshot).sweep(contract, multipliers)` → DataFrame[sigma_mult,
sigma, theo_price, ...]. `Position.single(contract, direction, symbol)` +
`aggregate_position(pos, per_leg_fn)` for signed multi-leg (single-leg MVP).

- **Controls:** symbol + **Fetch snapshot** (cached in page state); **contract
  selector** expiry → strike → call/put → buy/sell → `Position.single`.
- **What-if tab:** `WhatIfEngine.sweep` over `s_range = linspace(spot*0.8,
  spot*1.2, 81)` at `t_days` (slider) → Plotly curve (x=S, y=theo_price) with a
  zero line, **spot** line, and a **ΔS% target** line (slider).
- **IV-shock tab:** `IVShockEngine.sweep(contract, [1.0, mult])` → grouped
  **base-vs-shock** bars across Price/Δ/Γ×100/Θ/Vega (multiplier slider).
- Sliders recompute in-thread (snapshot already fetched; pure BS math is fast).
- **Replay** (Greeks over the 2-day path + scrub cursor) — deferred follow-up.
- **TDD (pure):** `whatif_figure(df, spot, target_s)`; `ivshock_figure(base_row,
  shock_row)`; `expiries_of(snapshot)` / `strikes_of(snapshot, expiry, kind)`.

## Error handling

- Proxy down / fetch failure → `ui.notify` error, page stays usable (shell banner
  already covers proxy-down).
- Empty/short chain → friendly "no data" states (no crashes).
- Heatmap with no history rows → explanatory note, not an error.

## Testing

- Pure figure/transform builders unit-tested with sample dicts/DataFrames.
- `render()` smoke-verified + live screenshots (fetch a real chain/snapshot).
- Heavy fetches run off-thread via `nicegui.run.io_bound`.

## Out of scope

Simulator **Replay** tab; the gex_history collector process itself (we only
*read* its DB); Gamma Term-structure view; chart-style persistence.
