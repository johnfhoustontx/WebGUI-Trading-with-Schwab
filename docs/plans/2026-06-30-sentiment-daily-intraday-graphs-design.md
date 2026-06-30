# Sentiment page — replace 30-Day History with two colorized intraday graphs

**Date:** 2026-06-30
**Branch:** `Using_Highcharts`
**Status:** design (approved via Q&A)

## Summary

Replace the `/sentiment` page's collapsed **30-Day History** section (a composite
line chart + rolling-average / velocity / divergence text lines) with **two
colorized intraday time-series graphs** inside a collapsible expander:

1. **Daily Market Sentiment** — the live composite (0–10) recorded every ~2 min.
2. **Daily Market Trend** — the live directional trend score (0–100) recorded
   every ~2 min.

Both are **rolling continuous series over the last 5 trading days**, with
overnight/session gaps collapsed, and each line is **colorized by value**
(green / yellow / red).

## Requirements (locked via Q&A)

- Two graphs **stacked vertically** (Sentiment top, Trend bottom).
- Inside a **collapsible expander**, collapsed by default (replaces the
  "30-Day History" expander).
- Recorded **every ~2 min** (the existing composite-refresh cadence), displayed
  at that resolution.
- **Rolling continuous, last 5 trading days**; session gaps collapsed.
- Line **colorized by value** (greens / yellow / red).
- **Drop** the rolling-average / velocity / divergence text lines.
- Series is **recorded going forward** (starts empty; fills in over time). No
  backfill — the trend has no historical series and, for consistency, sentiment
  is recorded the same way.

## Approach — persistence (chosen: SQLite)

The series must survive service restarts to retain 5 trading days, so it needs
durable storage.

- **SQLite (chosen)** — follows the existing `gex_history_db` time-series
  precedent; clean ordered queries + pruning. ~975 rows for 5 days is trivial.
- JSON file — simpler but clumsier to append/prune safely. Rejected.
- Redis-only — loses history on flush/restart. Rejected.

## Architecture

### Tier 2 — `services/sentiment_svc`

**New `intraday_history_db.py`** (SQLite at a new `repo_paths.SENTIMENT_INTRADAY_DB`,
e.g. `sentiment-dashboard/data/sentiment_intraday.db`; parent dir auto-created):

```
table sentiment_intraday(ts INTEGER PRIMARY KEY, sentiment REAL, trend REAL)
```

- `insert_point(conn, ts, sentiment, trend)` — one 2-min sample.
- `load_recent(conn, n_days=5)` — rows within the last 5 *trading* days, ascending.
- `prune(conn, n_days=5)` — drop rows older than the window.

**`handlers.refresh`** (runs every 120 s already): after the composite + trend
compute, **gated to RTH trading sessions** (off-hours the live composite is stale
/ zero and would clutter the series):

1. Record one point: `sentiment = float(live["composite"]["total_score"])` (0–10),
   `trend = float(_TREND["trend"]["score"])` (0–100), `ts = now (unix)`.
2. Prune to 5 trading days.
3. Publish a new cache view **`cache:sentiment:intraday_history`** →
   `{"points": [{"ts", "sentiment", "trend"}, …]}` + event
   `events:sentiment:intraday_history`.

Recording is wrapped defensively (a record/publish failure must not abort the
core composite refresh).

**Contract:** an additive light validator in `shared/contracts/sentiment.py`
(`IntradayHistory`) for the new view, consistent with the other domain views.
Non-strict — a malformed point is dropped, not fatal.

### Tier 1 — `webgui/pages/sentiment.py`

1. `_read_cache` reads `sentiment:intraday_history` → `state["intraday"]`.
2. Replace the collapsed "30-Day History" expander (lines ~622–628) and its
   roll/vel/flag/div labels with a new **"Daily Sentiment & Trend"** expander
   (collapsed by default) holding **two stacked charts**.
3. Two pure builders:
   - `build_sentiment_intraday_figure(points)`
   - `build_trend_intraday_figure(points)`

   Both: Highcharts `type="stockChart"`, **ordinal datetime x-axis**
   (`xAxis.ordinal:True` — collapses session gaps automatically; all points are
   RTH-only so overnight gaps close), date in the **tooltip header**
   (`tooltip.xDateFormat`) to avoid the datetime-crosshair epoch-ms gotcha,
   explicit `chart.height`, dark transparent theme matching the page.
4. **Value-zone coloring** via Highcharts `series.zones` (`zoneAxis:'y'`),
   matching the existing gauge/traffic semantics:
   - **Sentiment (0–10):** ≤4.5 red, 4.5–6.5 yellow, ≥6.5 green (the existing
     `traffic_color` bands).
   - **Trend (0–100):** ≤30 red, 30–70 yellow, ≥70 green (the existing
     `score_to_state` boundaries / range band).
5. Charts are **built once at render** and updated in place
   (`el.options = fig; el.update()`).
6. **Collapsed-expander reflow gotcha:** a chart built inside a collapsed
   expander measures a 0×0 container and renders collapsed. Mitigate with (a) an
   explicit `chart.height`, and (b) a **reflow on expand** — on the expansion's
   `value`-change (open), `ui.timer(0.05, lambda: ui.run_javascript(
   f"getElement({el.id})?.chart?.reflow()"), once=True)` for each chart (the same
   fix used for the Simulator's hidden tab panels).
7. Wiring: the new view is published in the same `refresh()` as the composite, so
   the existing composite version-poll repaint triggers a re-read + re-apply; the
   `_apply()` path sets both figures' options from `state["intraday"]`.

### Removed from the page

- `build_history_figure` usage (the old composite line) in the section.
- The rolling-average (`roll_lbl`), velocity (`vel_lbl`/`flag_lbl`), and
  divergence (`div_lbl`) text lines.
- `composite_series` / `sentiment_30d_avg` are **retained** — still used by the
  "30-Day Avg" sentiment gauge.

## Color thresholds (reference)

| Series | red | yellow | green |
|--------|-----|--------|-------|
| Sentiment (0–10) | ≤ 4.5 | 4.5–6.5 | ≥ 6.5 |
| Trend (0–100) | ≤ 30 | 30–70 | ≥ 70 |

## Testing

- **Service:** `intraday_history_db` round-trip + 5-day prune (pure). A
  Redis-driven `handlers.refresh` test proving a point is recorded during RTH and
  the `cache:sentiment:intraday_history` view is published with the expected shape.
- **Page:** pure-function tests for `build_sentiment_intraday_figure` /
  `build_trend_intraday_figure` (zones present + correct, data mapping, empty-state
  yields a valid empty figure). `test_no_inline_style.py` already guards the page.

## Out of scope

- Backfilling historical sentiment/trend (recorded going forward only).
- Any change to the gauges, components popup, sector table, or rotation page.
- Highcharts option dicts remain inline (chart config, not CSS — per the
  Tailwind-first out-of-scope rule).
