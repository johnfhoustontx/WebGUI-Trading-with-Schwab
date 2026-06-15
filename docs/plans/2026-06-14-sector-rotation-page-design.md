# Sector Rotation sub-page (under Sentiment) — design

**Date:** 2026-06-14
**Status:** approved
**Route:** `/sentiment/rotation`

## Goal

Port the source tk dashboard's "Sector Rotation" button to the webgui as a
**sub-menu item under Sentiment**. Data is fairly static → cache it and display
the cached assessment unless manually refreshed.

## Source behavior (reference)

The tk button (`sentiment_dashboard.show_sector_rotation` / `_render_sector_rotation_popup`)
computes an RRG-vs-SPY assessment via `sector_rotation_assessment.py` and shows:
headline (Risk-ON/OFF/Mixed + spread), ROTATING FROM / INTO columns (with S&P cap
weights), and a full quadrant-map table (Sector/ETF/RS-Ratio/RS-Mom/Quadrant/Dir).

## Engine reuse (no math reimplemented)

`sentiment-dashboard/sector_rotation_assessment.py` is copied + self-contained:
- `build_aligned_frame(symbols)` — fetches daily closes per symbol via the proxy
  (`PROXY_URL` `/pricehistory`, 1yr daily) and tail-aligns into a DataFrame.
- `build_assessment(frame, report_date)` → assessment dict.
- (`assess_from_close_series(sector_closes, bench, date)` — alt entry from
  in-memory closes; not needed here.)
- Constants: `SECTOR_ETFS` (etf→name, 11 GICS), `CYCLICAL_ETFS`/`DEFENSIVE_ETFS`,
  `BENCHMARK="SPY"`, `MIN_BARS=129`, `RISK_THRESHOLD=1.5`, `QUADRANT_DIRECTION`.

Assessment dict shape:
- `date`
- `headline`: {`regime` (Risk-ON/Risk-OFF/Mixed), `text`, `spread`,
  `cyclical_mom_mean`, `defensive_mom_mean`}
- `rotating_from` / `rotating_into`: [{`name`, `etf`, `quadrant`}]
- `sectors`: [{`name`, `etf`, `rs_ratio`, `rs_momentum`, `quadrant`, `direction`}]

The page calls `build_aligned_frame([SPY]+sector ETFs)` → `build_assessment(...)`
off-thread (~12 proxy calls).

## Nav: Sentiment → expandable group

Mirror the Options group in `webgui/main.py`:
- `SENTIMENT_CHILDREN = [("/sentiment","Sentiment","insights"),
  ("/sentiment/rotation","Sector Rotation","donut_large")]`.
- Remove `/sentiment` from `FLAT_NAV` (Trade/Portfolio/Driver stay flat).
- In `_layout`, add a "Sentiment" `ui.expansion` (open when
  `active == "/sentiment" or active.startswith("/sentiment")`), rendering the
  children via `_nav_link`, just like Options.
- Register `@ui.page("/sentiment/rotation")` → `pages.sentiment_rotation.render()`;
  add the route to `test_shell.py`'s expected set.

## Page `webgui/pages/sentiment_rotation.py`

- Reuses the engine (insert `SENTIMENT` on sys.path; `import
  sector_rotation_assessment as rotation_tool`).
- **Cache:** module-level `_ROTATION_CACHE = {"assessment": None, "at": None}`.
  On render: if cached, paint instantly (no fetch); if empty, compute once
  (spinner). Manual **Refresh** recomputes. NO auto-refresh / timers.
- **`_compute()`** (off-thread): `frame = rotation_tool.build_aligned_frame(
  [rotation_tool.BENCHMARK] + list(rotation_tool.SECTOR_ETFS))`;
  `a = rotation_tool.build_assessment(frame, date.today().isoformat())`;
  store `(a, datetime.now())` in `_ROTATION_CACHE`. Returns `a` or None.
- **Render (pure builders + thin wiring):**
  - Header + Refresh button + spinner + "as of {date}" / cache-age.
  - Headline line (regime colored + text + spread detail).
  - ROTATING FROM / INTO two columns (name (quadrant) + cap weight%, side total).
  - Quadrant-map: header row + one `ui.row` per sector (colored by quadrant) —
    Sector/ETF/RS-Ratio/RS-Mom/Quadrant/Direction, sorted by RS-Mom desc.
  - **RRG scatter** via `ui.plotly(rrg_scatter_figure(a))`.
  - Footer caveat. Friendly message when `a` is None (insufficient bars / proxy).
- Off-thread + `try/except → ui.notify`; proxy-down banner handled by `_layout`.

## Pure transforms (TDD)

- `quadrant_color(q)` — Leading green / Improving cyan / Weakening amber /
  Lagging red / else flat.
- `headline_parts(a)` → (regime, color, text, detail_str).
- `side_rows(a, side_key, weights)` → ([{name, quadrant, weight}], total_pct).
- `rotation_rows(a)` → [{name, etf, rs_ratio, rs_mom, quadrant, direction, color}]
  sorted by rs_momentum desc.
- `rrg_scatter_figure(a)` → Plotly fig dict (markers x=rs_ratio,y=rs_momentum,
  text=etf, marker colors by quadrant; reference lines at x=100,y=100;
  `template plotly_dark`, transparent bg).
- Cap weights: build `{etf: sp_weight}` from `sectors_ref.load_sectors_data()`
  sector rows (etf + sp_weight), or map `SECTOR_ETFS` name → `SP500_SECTOR_WEIGHTS`.

## Testing

Unit-test the pure builders with a sample assessment dict (quadrant colors, headline
regime/colors, side totals, row sort, scatter shape). Script-verify
`build_assessment` against the live proxy (sane RRG: ratios/momenta near 100,
quadrants assigned). Browser smoke: nav group + page renders + RRG scatter.

## Out of scope

- Auto-refresh (intentionally manual-only — static data).
- Persisting the assessment to disk (the source `save_snapshot` writes
  `rotation_*.json`; the webgui keeps an in-memory cache only).
