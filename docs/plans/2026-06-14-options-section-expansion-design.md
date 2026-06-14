# Options Section Expansion — Design

**Date:** 2026-06-14
**Status:** Approved
**Supersedes:** the single-page scope of Task 3.1 in
[`2026-06-14-nicegui-webgui-plan.md`](2026-06-14-nicegui-webgui-plan.md).

## Purpose

The original Tk Options dashboard (`D:\Trading With Schwab\options-scanner\dashboard.py`)
is far richer than the first NiceGUI Options page. It has seven internal tabs, a
persistent right-side **Trade detail** panel shared by all signal tables, a
compact top quotes/IV/sentiment strip, and spawns separate **Gamma** and
**Simulator** windows. This design expands the webgui Options section to match.

## Decisions (from brainstorming)

| Question | Decision |
|----------|----------|
| Nav structure | **Expandable "Options" group** in the left-nav; each sub-section is its own route |
| Gamma / Simulator | **Stub routes now**; port `gamma_tool.py` / `options_simulator/` later |
| Header strip | **Compact**: SPX/SPY/QQQ + VIX/regime + sentiment dot (not a full scan) |
| Trade detail | **Persistent shared right-pane** (not a click→dialog), graphics-rich |
| Calculator | Faithful **colored P&L heatmap grid + colored summary tiles** (no chart lib) |

## Navigation

Left-nav becomes group-aware. **Options** is an expandable parent; the other
apps stay flat (Sentiment, Trade, Portfolio, Driver).

| Child | Route | Engine | Proxy? |
|-------|-------|--------|--------|
| Scanner (0-4 / 5-15 DTE tabs) | `/` | `scanner_engine.run_full_scan` | yes (off-thread) |
| Paper Trades | `/options/paper` | `paper_trader.get_all_trades` (+ close/delete/analyze) | DB read; client for analyze/close |
| Captured Signals | `/options/captured` | `signal_db.get_open_signals_with_latest_mark` (+ reprice/auto-close) | DB read; client for "refresh marks" |
| Paper Portfolio | `/options/portfolio` | `paper_engine.account_snapshot` + `paper_account_db.fetch_open_positions/fetch_orders` | DB read; client for cycles |
| Calculator | `/options/calculator` | `options_calculator.calc_summary/calc_spread_pnl/generate_eval_dates/generate_price_range` | no (pure math) |
| Swing Scanner | `/options/swing` | `scanner_engine.screen_spreads/build_iron_condors` + `scoring.score_all_signals` | yes (off-thread) |
| Gamma | `/options/gamma` | **stub** (`gamma_tool.py` later) | — |
| Simulator | `/options/simulator` | **stub** (`options_simulator/` later) | — |

"Paper Portfolio" is named to avoid colliding with the top-level Portfolio
(portfolio-analyzer) page.

## Code structure

Refactor `webgui/pages/options.py` into a `webgui/pages/options/` subpackage:

```
webgui/pages/options/
├── __init__.py
├── header.py      # compact quotes/VIX/regime/sentiment strip (shared)
├── detail.py      # persistent Trade detail panel + SVG graphics (shared)
├── svg.py         # pure SVG builders: speedometer, gradient bar, range marker
├── scanner.py     # two-pane: 0-4/5-15 DTE tables (left) + detail panel (right)
├── paper.py
├── captured.py
├── portfolio.py
├── calculator.py
├── swing.py
├── gamma.py       # stub
└── simulator.py   # stub
```

The shell `_layout` (in `webgui/main.py`) gains group rendering via
`ui.expansion` and active-route highlighting for nested paths.

## Compact header strip (`header.py`)

Shown atop the Options pages. Cheap data, refreshed on a `ui.timer` (~30s):

- **Quotes:** one `get_quotes(["$SPX","SPY","QQQ","$VIX"])` via the proxy client.
- **VIX regime:** `scanner_engine.vix_regime(vix_price)` → `{regime,label,color}`.
- **Sentiment dot:** `regime_filter.evaluate_regime()` (reads
  `shared/sentiment_bridge.json`, no client) → dot color/label from
  `allow_ccs`/`allow_pcs`/`active`/`composite_score`.

## Trade detail panel (`detail.py`) — persistent, shared

One panel pinned to the right of the signal tables, shared by Scanner,
Captured Signals, Paper Trades, Swing Scanner. Selecting a row calls
`update(signal)`; each tab synthesizes a signal-like dict first. Placeholder
("Select a signal…") until a selection exists. Sections:

- **Header:** `SYMBOL · TYPE · TRADE_TYPE`, **speedometer gauge** (composite
  score/grade), 2×2 tiles (Credit / PoP / Breakeven / DTE), color-coded.
- **Card 1 Trade Info:** expiration/DTE, underlying, strikes/legs (IC = two
  legs), max loss (red), max contracts, E[P&L], max profit, R:R, lifecycle.
- **Card 2 Greeks:** Δ, Θ (green/red), Vega, IV.
- **Card 3 Composite Score:** 11 factor **gradient bars** (rr, pop, theta, iv,
  iv_hv, vega, em, liq, trend, gex, dex), red→amber→green; IC variant shows
  pcs_leg/ccs_leg/delta_bonus.
- **Card 4 IV Analysis:** ATM IV, 52w **range marker**, vol-rank + percentile bars.
- **Card 5 Expected Move:** 1d/1w/30d moves, ±1σ/±2σ markers, est. EOD drift
  (green/red by direction).

### Graphics as SVG (`svg.py`, pure functions → TDD)

- `speedometer_svg(score, grade)` — semicircular gauge, colored zones
  (0–40 red, 40–55 amber, 55–75 blue, 75–100 green), needle at score.
- `gradient_bar_svg(value, width=150, height=12)` — 0–50 red→amber,
  50–100 amber→green; value clamped [0,100].
- `range_marker_svg(low, high, current, ...)` — horizontal range with a marker
  interpolated at `current`.

## Calculator (`calculator.py`)

Inputs form (strategy, symbol, price, expiry, contracts, IV, IV change, rate,
per-leg strikes/premiums, price range). Optional "fetch price/IV" buttons use
the proxy client; the math itself does not. Two visual outputs:

1. **Summary tiles** — `calc_summary()` → Entry Credit/Debit, Max Risk (red),
   Max Return (green), Return on Risk, Breakeven(s), PoP; color by sign/type.
2. **P&L heatmap grid** — `calc_spread_pnl()` rows (price × eval-date pairs of
   $ and %). Custom colored HTML/CSS grid with a fixed header and scroll body:
   - eval-date columns from `generate_eval_dates(today, expiry, max_columns=7)`.
   - price rows from `generate_price_range(spot, pct)` (or user min/max).
   - **5-step green / 5-step red gradient** keyed to the grid's global
     max-profit / max-loss (banding: |frac| in 0–.2,.2–.4,.4–.6,.6–.8,.8–1).
   - **current-price row** highlighted; numeric cells `+1,234` / `+12.3%`.

   Pure functions to TDD: `pnl_cell_class(value, g_max, g_min)` (banding),
   `eval_date_labels(...)`, `grid_rows(pnl_data)`.

## Data tabs (DB-backed; manual-first actions)

The Tk app auto-polls; the web app exposes the same operations as **explicit
buttons** run off-thread (no auto-timers this pass — YAGNI/safety).

- **Captured Signals:** `signal_db.get_open_signals_with_latest_mark()` (read).
  Actions: "Refresh marks" → `signal_repricer.reprice_swing` +
  `signal_recommender.build_mark`/`plan_auto_closes` →
  `signal_db.close_signal_manually` (off-thread, client). "Close selected"
  (manual exit value, no client).
- **Paper Trades:** `paper_trader.get_all_trades()` (read). Actions: close
  (debit dialog), delete, delete-all-closed; per-row analyze
  (`paper_trader.analyze_trade`, client).
- **Paper Portfolio:** `paper_engine.account_snapshot()` cards +
  `paper_account_db.fetch_open_positions()` + `fetch_orders()`. Actions: reset
  account; run entry/manage cycle (`paper_engine.run_entry_cycle/run_manage_cycle`,
  client).

## Testing

- SVG builders, calculator banding/grid mapping, signal→row/synth transforms:
  **pure functions, unit-tested TDD**.
- Each page exposes a `render()`; rendering smoke-verified + live screenshot.
- DB reads verified against the real (possibly empty) options-scanner DBs.

## Build order (batches; checkpoint between)

- **A** — Nav restructure (expandable group) + move scanner into the subpackage,
  two-pane layout + shared **detail panel + SVG** + **header strip** +
  Gamma/Simulator stubs.
- **B** — Calculator (tiles + heatmap grid) + Captured Signals (gets detail panel).
- **C** — Paper Trades + Paper Portfolio.
- **D** — Swing Scanner.
- then resume the original plan's **Sentiment / Trade / Portfolio / Driver** pages.

## Out of scope (this expansion)

Full Gamma heatmaps and the Simulator (replay/what-if/IV-shock) — stubbed now,
ported in a later phase. No auto-polling cycles. No new analytics — pages render
existing engine output only.
