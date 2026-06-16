# Options GUI polish batch — design (2026-06-16)

A batch of UI/UX fixes and two small service additions across the Options
section. **Streaming-driven paper repricing is explicitly OUT of this batch** —
it is a larger cross-tier feature to be designed separately.

## Context

All Options pages are 3-tier: `webgui/pages/options/*.py` read from Redis via
`bus_client` and enqueue commands; `services/options_svc/{compute,handlers,scheduler}.py`
own the engines and publish cache views. So most items here are pure-GUI
(page-only) changes; only the Gamma status bar needs a new published view.

Decisions taken with the user:
- New-signal marking = **session-level diff** (page-side; resets on reload).
- Calculator matrix = **spot-centered, symmetric, strike always visible**.
- Gamma status bar = **alongside** the existing "Next refresh" countdown (keep it).
- Streaming repricing = **separate** later effort.

## Items

### 1. Persistent nav dropdowns — `webgui/main.py`
The left-nav `ui.expansion` groups ("Options", "Sentiment") are rebuilt per page
with `value` hardcoded from the active route, so the open/closed state resets on
navigation. Add a module-level `_NAV_OPEN: dict[str, bool]` (single-user, mirrors
the `_CACHE` pattern). Each expansion: `value = _NAV_OPEN.get(name, <active default>)`
and `on_value_change` records the toggle. State persists across navigation; a
never-toggled group still auto-opens when its route is active.

### 2. Scanner — signals colored by quality — `pages/options/scanner.py`
Add a `body-cell-composite_score` `ui.table` slot rendering the score as a colored
chip by the existing zones: red <40, amber 40–55, blue 55–75, green 75–100 (same
scheme as `svg.py` speedometer / `detail.py` colors). Pure transform
`score_zone_color(score)` is unit-tested.

### 3. Scanner — plain-English term text — `pages/options/scanner.py`
`_scan_meta_strip` currently shows `Term: CONTANGO as of <iso>`. Replace with a
pure `term_text(term_dict, ts)` →
e.g. `"VIX term: Contango (near-term calm) · as of 1:32 PM"`. Map
CONTANGO/BACKWARDATION/MIXED/UNKNOWN to plain phrases; format ISO ts to short
local time. Unit-tested.

### 4. Scanner — new-signal highlight — `pages/options/scanner.py`
Page-side session diff. Keep previous scan's keys
(`symbol|type|short_strike|long_strike|expiration`) in page state. On repaint,
rows whose key is absent from the prior set get a `_new` flag → "NEW" badge +
subtle row tint (via a body-cell slot or row class). First load marks nothing.
Pure `mark_new(rows, prev_keys)` unit-tested.

### 5. Paper Trades — detail panel not updating — `pages/options/paper.py`
`_populate` repaints the table on each version change but never re-renders the
open detail panel, so the selected trade's detail goes stale. Fix: capture the
selected row id before clearing `raw_by_id`, and after repaint re-call
`detail_panel.update(synth_from_trade(raw_by_id[id]))` if the trade still exists,
else `detail_panel.clear()`.

### 6. Captured — drift `x.xx` — `pages/options/captured.py`
`score_drift` is passed raw (int). Round to 2 decimals in `captured_rows`
(`round(v, 2)` / format helper). Unit-tested.

### 7. Captured — color by recommendation — `pages/options/captured.py`
Add a `body-cell-recommendation` slot coloring the cell by value:
HOLD=amber, TAKE_PROFIT=green, CUT=red. Pure `rec_color(rec)` unit-tested.

### 8. Calculator — symmetric matrix — `services/options_svc/compute.py`
The P&L grid range is computed server-side (`calc_compute` →
`options_calculator.generate_price_range(spot)` + step rounding/clamp), which can
render asymmetrically and may exclude strikes. Make the range exactly symmetric
about spot (spot in the middle row, equal rows each side) and widen it if needed
so short/long strikes fall within view. Implemented in `compute.calc_compute`
(do NOT edit the copied engine). Unit-tested via a pure helper
`symmetric_price_range(spot, strikes, pct)`.

### 9. Symbol inputs auto-select on focus — options pages
Add a reusable focus handler that selects-all on focus/tab-in to every Symbol
`ui.input` (calculator, gamma, simulator, swing; scanner if present). NiceGUI:
`inp.on('focus', js_handler=...)` selecting the nested `<input>`. Verified in
the browser preview.

### 10. Gamma status bar — service + page
- **Service:** new pure `compute.gex_status_view()` returns
  `{status_label, status_color, last_scan, next_scan, age_seconds}` using
  `gex_status.classify_collector_status` + `gex_history_db.last_snapshot_age` and
  a next-5-min-boundary calc within 08:30–15:20 CT. `handlers.publish_gex_status(bus)`
  caches it under `cache:options:gex_status` + event. Called each 30s scheduler
  tick (cheap DB read), so the bar stays fresh even off-hours.
- **Page (`pages/options/gamma.py`):** add a status bar
  `Collector: ✓ 2m ago · Last scan 1:25 PM · Next scan 1:30 PM`, version-polled
  from `options:gex_status`. Keep the existing "Next refresh" countdown alongside.

## Testing

TDD the pure functions (`score_zone_color`, `term_text`, `mark_new`,
`rec_color`, `symmetric_price_range`, `gex_status_view`) in the respective
`webgui/tests/` and `services/options_svc/tests/` suites. Smoke-verify the
rendered pages + symbol auto-select + status bar via the NiceGUI preview.

## Out of scope (separate design)
- Streaming-driven paper-position repricing (proxy `LEVELONE_OPTIONS` stream →
  live position marks → exit rules). Tracked for its own design doc.
