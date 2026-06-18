# GEX collection expansion + Gamma symbol dropdown — Design

**Date:** 2026-06-18
**Status:** Approved

## Goal

Two related changes to the Gamma subsystem:

1. **Expand intraday GEX history collection** to cover the full scan watchlist
   (`options-scanner/data/Top 20.xlsx` Column A), not just the four index
   benchmarks — so the Gamma strike×time heatmap has live intraday data for any
   watchlist symbol.
2. **Make the Gamma page's Symbol field a dropdown** (default `$SPX`) populated
   from the collected universe, so the user picks from the tracked symbols
   instead of free-typing. The **Explain** button then covers every new symbol
   for free (it already operates on whatever symbol is selected).

User decisions (brainstorming):
- **Cadence:** every **2 minutes** for **all** symbols (was 5 min for 4 symbols).
- **Dropdown scope:** the full collected universe **minus `$VIX`** ($VIX is still
  collected for the sentiment bridge, just not offered as a Gamma selection).

## Context

- `options-scanner/watchlist.py` already owns the scan universe:
  `get_scan_symbols()` = `BASE_SYMBOLS` (`$SPX`, `SPY`, `QQQ`) ∪ Column A of
  `Top 20.xlsx`, deduped + order-preserving, mtime-cached, with a safe fallback
  to the base indices. Currently 21 symbols.
- `options-scanner/gex_collector.py` polls a hardcoded
  `SYMBOLS = ["$SPX", "$VIX", "SPY", "QQQ"]` every `POLL_INTERVAL_MIN = 5`. The
  options service (`services/options_svc`) is the Tier-2 owner of collection and
  reuses `gex_collector.poll_once` **verbatim** via `compute.collect_gex_snapshots`.
- `webgui/pages/options/gamma.py` is a pure **Tier-3 reader** — it imports only
  `nicegui` + `shared.bus`/`bus_client`, never an app engine. So the dropdown's
  symbol list must arrive over the Redis bus, not from a direct `watchlist` import.

## Design

### A. Collector symbol universe — `options-scanner/gex_collector.py`

- New `collection_symbols()`: `dedupe(SYMBOLS + watchlist.get_scan_symbols())`,
  order-preserving. Defensive: any `watchlist` import/read failure → return
  `list(SYMBOLS)`. `SYMBOLS` stays `["$SPX","$VIX","SPY","QQQ"]` as the always-on
  index base — it is what supplies **`$VIX`**, which the watchlist lacks.
- `poll_once(client, engine, conn, lock=None, symbols=None)`: when `symbols is
  None`, iterate `collection_symbols()`. Verbatim reuse by the service still works
  (it calls `poll_once` with no `symbols` arg). **Term-structure collection
  (`poll_term_once`) stays SPX-only.**

### B. Cadence → every 2 minutes (kept aligned across the stack)

- `gex_collector.POLL_INTERVAL_MIN = 5 → 2`. Downstream derivations follow:
  `LOCK_TTL_SEC = POLL_INTERVAL_MIN*60*2` → 240s; `next_boundary` already keys off
  it; `gamma_tool.py`'s forward-projection cursor (imports `POLL_INTERVAL_MIN`)
  follows automatically.
- `services/options_svc/scheduler.py`: `_GEX_INTERVAL_MIN = 5 → 2`.
- `options-scanner/gex_status.py`: `STALE_AFTER_SEC = 600 → 240` (preserves the
  "2× poll interval" semantics; comment updated).
- `webgui/pages/options/gamma.py` `panel_flex` default `full_cols ≈ 205`
  (08:30–15:20 CT at 2-min slots) so the heatmap-growth heuristic stays
  calibrated. Cosmetic only.

### C. Dropdown symbol source (Tier-3 clean)

- `compute.gamma_symbol_options()` → `[s for s in gc.collection_symbols() if s !=
  "$VIX"]` ($SPX first, no $VIX).
- `handlers.publish_gamma_symbols(bus)` → `cache:options:gamma_symbols =
  {"symbols": [...]}` + publish `events:options:gamma_symbols`. Called **once at
  scheduler startup** (mirrors the existing one-shot gamma startup refresh; the
  watchlist rarely changes mid-session and a service restart re-publishes).
- `compute.collect_gex_snapshots()` returns `len(gc.collection_symbols())`.

### D. Gamma page dropdown — `webgui/pages/options/gamma.py`

- Replace `ui.input("Symbol", value="$SPX")` with
  `ui.select(options=symbols, value="$SPX", with_input=True)` — typeahead over
  ~21 entries.
- A small pure helper `symbol_options(cached)` builds the option list from the
  cached `{"symbols":[...]}` view, guaranteeing `$SPX` is present and first;
  static fallback `["$SPX","SPY","QQQ"]` when the cache is cold. Read via
  `bus_client.read("options:gamma_symbols")` at render.
- `_current_symbol()` and the Refresh/Explain/Analyze wiring are **unchanged**, so
  **Explain automatically covers any selected symbol**. Drop the input-only
  `select_all_on_focus` for the select.

### Non-changes

- **Analyze** stays the bundled SPX/SPY/QQQ prompt (only Explain was in scope).
- `$VIX` is still collected (sentiment bridge unaffected); it is only excluded
  from the dropdown.

## Testing (TDD)

- New (options-scanner): `collection_symbols` union / dedupe / order / fallback.
- New (options_svc compute): `gamma_symbol_options` excludes `$VIX`, `$SPX` first.
- New (options_svc handlers): `publish_gamma_symbols` caches + publishes.
- New (webgui): `symbol_options` helper (default present + first, cold fallback).
- Updated: `test_collect_gex_snapshots_*` mock `collection_symbols` and assert the
  returned count; `gex_status` / `_gex_next_scan` boundary tests recalibrated to
  2-min slots.
- Docs: root `CLAUDE.md` (collector cadence/symbols, Gamma dropdown) +
  `options-scanner/CLAUDE.md` collector notes.

## Risks

- **Load:** 2-min × ~22 chains is a large increase in proxy load, and each poll
  must finish inside its 2-min slot. Per-symbol failures stay isolated; slot-key
  dedupe means an overrun simply skips the next slot (no overlapping polls, since
  the scheduler runs the blocking poll sequentially in the executor and `gex_due`
  is slot-gated). If polls regularly overrun 2 min, revisit cadence. Honoring
  2 min as requested.
