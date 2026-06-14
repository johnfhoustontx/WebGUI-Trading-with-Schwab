# Sentiment page — layout & restyle pass — design

**Date:** 2026-06-14
**Status:** approved
**Builds on:** [`2026-06-14-sentiment-persistence-industries-design.md`](2026-06-14-sentiment-persistence-industries-design.md)

Presentation-only re-layout of `/sentiment` (plus two small data additions for the
industry rows). No change to the composite/sector data pipeline.

## Changes

### 1. Component table → right of the gauge (top two-column region)
Top region becomes one `ui.row` (`items-start no-wrap`):
- **Left column** (~fixed ~300px): speedometer gauge, `bias_lbl` ("6.14 · Mild
  Bullish"), `sub_lbl` (size/conf), and **the Market Trend regime** (badge +
  description + SPY/SMA detail) blended directly beneath (item 6).
- **Right column** (`flex:1`): the component table.

### 6. Market Trend regime → top
Moved into the left column beneath the gauge; the old standalone "Market Trend
Regime" section (and its separator) lower down is removed.

### 2. Tiles — ~25% smaller + traffic-light background
Reduce tile min-width (~96→72px) + padding. Background = composite traffic-light
band via new pure `traffic_color(total)`: `>=6.5` green, `<=4.5` red, else amber
(mirrors source `_update_metric_card_colors`). All five tiles share the band
color; dark text (`#111`) for contrast.

### 3. History grid less intrusive
In `build_history_figure` layout: `xaxis`/`yaxis` `gridcolor:
"rgba(255,255,255,0.06)"`, `zeroline: false`, soft `linecolor`, thinner ticks
(`nticks` ~6). Pure-function change; existing shape test still passes.

### 4. Sector table full-window width + gridlines + row highlight
Keep the row-based render (needed for per-cell color + expansion). Make the
**Description** cell `flex:1` so each row spans the window. Add a scoped CSS block
via `ui.add_css` (a `.sent-sectors` wrapper class — `ui.html` strips `<style>`):
faint per-cell right-border (vertical gridlines), faint row bottom-border, and a
hover row highlight `rgba(255,255,255,0.04)`. Industry rows get a subtle tint.

### 5. Bottom status bar
Thin muted full-width strip as the last element:
`Updated HH:MM:SS · Next ~HH:MM · Sectors HH:MM · Proxy: connected`.
- `load()` stamps `state["composite_at"]` = `datetime.now()`; next = +300 s.
- `load_sectors()` stamps `state["sector_at"]`.
- Proxy from `proxy.health()["up"]` (best-effort; cached from the health the shell
  already polls, or a quick call). Shows "Loading…" before first load.
- A `status_lbl` updated by a small `_render_status()` called after each load and
  on a light `ui.timer` so "Next" stays current. Times persisted in `_CACHE` so
  the bar is correct immediately on a return visit.

### 7. Industries show P/C + RRG (NEW)
`_load_industries(etfs, spy_closes)` also:
- fetches `/chains` per industry ETF → `pcr_from_chain` → `pcr` dict, and
- computes `scoring_rotation.compute_rrg_quadrants(industry_closes, spy_closes,
  rs_window=50, mom_window=20)` → `quadrants` dict.
Returns `{quotes, trends, pcr, quadrants}`. `_ensure_industry` passes
`state["spy"]`. `industry_rows(sector_data, sector, quotes, trends, pcr,
quadrants)` now fills `pcr`/`rrg` (blank when a thin-volume industry returns no
chain / insufficient history — graceful). Heavier per-expand (~2 calls/industry +
1 quotes batch), on demand, cached.

### 8. Component table: drop Contrib column, Score to 2 decimals (NEW)
`component_table_rows` keeps the float score (`"score": s`, no `int()`); the
render drops the **Contrib** header + cell and formats Score `f"{score:.2f}"`
(e.g. Sector Perf `7.60`, VIX `4.00`). `contrib` stays in the row dict (cheap;
still documents the confidence-weighted reconciliation) but is not displayed.

## Pure transforms (TDD)
- New: `traffic_color(total)`.
- Changed: `industry_rows` (add `pcr`/`quadrants` params; populate the cells),
  `component_table_rows` (float score), `build_history_figure` (subtle grid).
Update the affected unit tests (`test_industry_rows_*`, `test_component_table_rows_contrib`).

## Verify
Unit tests for the transforms; restart preview + a11y/screenshot for layout; script
fallback (drive `_load_industries` w/ SPY) to confirm industry P/C + RRG if the
preview proxy is slow.

## Out of scope
No composite/sector data-pipeline or scoring changes.
