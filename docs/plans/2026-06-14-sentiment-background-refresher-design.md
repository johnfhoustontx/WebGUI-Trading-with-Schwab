# Sentiment background refresher — design

**Date:** 2026-06-14
**Status:** approved
**Builds on:** [`2026-06-14-live-sentiment-bridge-design.md`](2026-06-14-live-sentiment-bridge-design.md)

## Goal

Refresh the sentiment **every 120 s server-side, independent of any open/active
tab** (today the page only auto-refreshes while it is the active route — its
`ui.timer` dies when you navigate away). And: opening/returning to the page must
**not** trigger a new fetch — show the cached value (≤120 s old) instantly.

## Decisions (confirmed)

- **120 s** cadence.
- **Composite-only** per cycle (~20-30 proxy calls); the heavier Sector & Industry
  table (~24 calls incl. 11 `/chains`) loads **once at startup** + on the page's
  manual **Refresh**. Avoids hammering `/chains` every 2 min.
- No fetch on page activation — cached display is fine (data not that dynamic).

## Architecture

### Server-side background task (client-independent) — in `webgui/pages/sentiment.py`
- **`refresh_cache(with_sectors=False)`** (async, NO page UI): off-thread via
  `nicegui.run.io_bound`, runs the composite path (`_load_snapshots` backfill +
  `_load_live` during RTH) and, when `with_sectors`, `_load_sector_perf`; writes
  results into the module `_CACHE` (`snaps/spy/live/[sector]/composite_at/proxy_up`);
  then **publishes the bridge** (`build_bridge_payload` + `bridge.write_bridge`).
  Touches no widgets/`ui.notify`, so it is safe with zero clients. A
  `_REFRESHING` flag prevents a slow cycle from stacking.
- **`start_background_refresh()`** — idempotent (guard so it starts once). Runs an
  initial `refresh_cache(with_sectors=True)` (populates composite + sector table),
  then a 120 s loop of `refresh_cache(with_sectors=False)`. Implemented as a
  client-independent asyncio task (NiceGUI `background_tasks`/`asyncio.create_task`)
  so it runs regardless of connected clients.

### `webgui/main.py`
`@app.on_startup` → `sentiment.start_background_refresh()`. Importing the sentiment
module at startup binds its `scoring/` package first (the "good" order per the
root CLAUDE.md collision note).

### Page (`render()`) changes
- Seed `state` from `_CACHE`, paint. **Remove** the on-activation fetch
  (`ui.timer(0.1, lambda: load(with_sectors=True), once=True)`) and the per-page
  `ui.timer(300.0, load)`.
- Add a cheap page-local **`ui.timer(120, _repaint_from_cache)`**: re-seed `state`
  from `_CACHE` and re-apply `_apply`/`_apply_sectors`/`_render_status` — **no
  fetch** — so an open page tracks the background-refreshed cache.
- Keep the manual **Refresh** button (`load(with_sectors=True)` — explicit fetch)
  and the 15 s `_render_status` timer.
- Empty-cache first render (server just started): show "Loading…"; the startup
  refresh + the 120 s repaint fill it within seconds.

## Net effect

The bridge and the in-memory page snapshot refresh every 120 s regardless of any
open/active tab (and with no browser open at all, as long as the webgui server
runs) — complementing the GEX collector's 5-min publish. Page activation never
fetches; it shows ≤120 s-old cache instantly.

## Concurrency / safety

Single-user; asyncio single-threaded with heavy work in `io_bound` threads.
`_REFRESHING` guards overlap. `refresh_cache` is fully defensive (try/except;
never raises out of the loop). The background task and the page both read/write
`_CACHE` (whole-value reassignments — no torn mutation).

## Testing

- Unit: `refresh_cache` populates `_CACHE` given a fake client, with no page/UI
  context (assert composite keys land in `_CACHE`); `start_background_refresh` is
  idempotent.
- Confirm `render()` no longer registers the on-activation fetch timer (the
  0.1 s-once is gone) — a smoke assertion or code inspection.
- Import smoke (`import main`); brief log/script check that the startup task ticks.

## Out of scope

- Pushing live updates to an open page faster than 120 s (the repaint timer is the
  cap; fine — data isn't that dynamic).
- Changing the GEX collector's 5-min publish (unchanged; complementary).
