# Sentiment page — persistence + industry expansion — design

**Date:** 2026-06-14
**Status:** approved
**Builds on:** [`2026-06-14-sentiment-sector-perf-design.md`](2026-06-14-sentiment-sector-perf-design.md)

## Goals

1. **Persist the page across navigation.** NiceGUI rebuilds `/sentiment` on every
   visit, so today it re-fetches ~24 proxy calls each time. Cache the loaded data
   so returning to the tab paints instantly with no re-fetch.
2. **Expandable industries.** Clicking a sector row expands it into its industry
   sub-rows (Day/Week/Month % only; P/C and RRG blank — matching the source).
   Add Expand All / Collapse All.

## A. Persistence (module-level cache)

The app is single-user (per root CLAUDE.md), so a module-level cache is safe.

```python
_CACHE = {"snaps": None, "spy": None, "sector": None,
          "expanded": set(), "industry": {}}  # industry[sector] = {quotes, trends}
```

- `render()` seeds the local `state` from `_CACHE` (snaps/spy/sector/expanded/
  industry). After building widgets, if cached data exists it calls `_apply()` +
  `_apply_sectors()` + `_render_sector_table()` **synchronously** → instant repaint
  on return, previously-expanded sectors still open.
- `ui.timer(0.1, …, once=True)` fetches **only when the cache is empty** (first
  visit of the session). Otherwise no fetch.
- `load()` / `load_sectors()` / `_load_industries` write results into both `state`
  and `_CACHE`.
- Composite auto-timer: **120 s → 300 s** (still composite-only). Manual Refresh
  re-fetches composite + sectors; sector Refresh re-fetches sectors.

Lost on server restart (acceptable — it's an in-process cache, not a DB).

## B. Industry expansion

The sector table becomes a single rebuildable list via `_render_sector_table()`:
- Each **sector** row gets a ▷ (collapsed) / ▽ (expanded) toggle. Clicking flips
  the sector's membership in `state["expanded"]`, ensures its industry data is
  loaded, and re-renders.
- When a sector is expanded, its **industry** rows render indented beneath it:
  label (industry name) · ETF · description · Day/Week/Month % (colored via
  `pct_color`). P/C and RRG cells blank (source: industry option volume too thin).
- **Lazy load:** first expand fetches the sector's industry ETFs off-thread —
  `get_quotes` (Day %) + per-ETF 3-mo `get_daily_history` (Week/Month % via
  `week_month_from_closes`) — caches under `_CACHE["industry"][sector]`. Re-expand
  (incl. after navigation) is instant from cache.
- **Expand All / Collapse All** buttons by Refresh. Expand All opens all 11
  (fetching uncached, sector spinner shown); Collapse All clears the set.

Industry rows come from `sectors_ref.load_sectors_data()` (kind=="industry",
already deduped by (sector,industry) and global ETF).

## New pure transforms (TDD)

- `sector_industry_etfs(sector_data, sector_name) -> [etf, …]` — that sector's
  industry ETF symbols (kind=="industry", sector match, valid etf).
- `industry_rows(sector_data, sector_name, ind_quotes, ind_trends) -> [row, …]` —
  indented rows: {label, etf, desc, day, week, month, pcr=None, rrg=None}.

## New loader (off-thread)

- `_load_industries(etfs) -> {quotes, trends}` — `get_quotes(etfs)` + per-ETF
  history → `week_month_from_closes`. Mirrors `_load_sector_perf`'s defensive
  per-call try/except.

## Render / state

`_render_sector_table()` rebuilds `sector_box` from `sector_table_rows(...)` +
`state["expanded"]` + `state`-cached industry data, interleaving industry rows
under expanded sectors. The toggle handler (async) flips `expanded`, awaits
`_ensure_industry(sector)` (no-op if cached), re-renders. Heavy fetches off-thread
with the sector spinner + `try/except → ui.notify`. State in the local `state`
dict; cross-navigation persistence via the module-level `_CACHE` mirror.

## Testing

Unit-test `sector_industry_etfs` + `industry_rows`. Persistence/expansion wiring
is verified by (a) the test suite staying green + import smoke, and (b) driving
`_load_industries` against the live proxy in a single-threaded script to confirm
real industry rows (the preview renderer has been unreliable under the slow
weekend proxy).

## Out of scope

- Per-industry P/C / RRG (blank by design).
- Live-intraday composite (still last-completed-session).
