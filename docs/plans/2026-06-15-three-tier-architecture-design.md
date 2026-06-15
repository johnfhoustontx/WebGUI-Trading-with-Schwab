# Three-Tier Architecture — Design

**Date:** 2026-06-15
**Status:** Approved (brainstorming → design). Implementation plan to follow.
**Scope:** Whole monorepo. Re-tier the entire Trading-With-Schwab WebGUI stack into
three physically separate tiers communicating over a Redis (Memurai) backbone.

## Decisions (locked during brainstorming)

| Question | Decision |
|----------|----------|
| Scope | Whole monorepo (GUI + all app engines + shared data/comm layer) |
| Tier boundary | **Physical** — independent processes/services, not just packages |
| Transport | **Message broker** — Redis pub/sub + cache + command queue |
| Processing tier shape | **Per-domain services** (options, sentiment, trade, portfolio, driver) |
| Redis runtime | **Memurai** — native Windows service, redis-py compatible |
| Migration | **Incremental / strangler-fig** — one domain end-to-end at a time |

## Architecture overview

```
TIER 1 — PRESENTATION (GUI)            webgui/ NiceGUI (:8500)
  render() only; no engine imports, no Schwab calls, no sys.path glue.
  Reads Redis cache on page build; subscribes to pub/sub for live repaints;
  enqueues commands for user-triggered actions.
        ▲ subscribe / cache read              │ commands (RPC)
        │                                      ▼
TIER 3 — STORAGE & COMMUNICATION       Memurai (:6379) + on-disk DBs
  • cache:{domain}:{view}  — latest state (replaces _CACHE / _LAST_RESULTS)
  • events:{domain}:{view} — pub/sub live push (replaces bridge file + version polling)
  • cmd:{domain}           — Redis Streams command queue (GUI → service RPC)
  • shared/contracts/      — typed payload schemas; the API between tiers
  • shared/bus/            — redis-py wrapper (+ fakeredis test backend)
  • persistent: paper-trade SQLite, gex_history_db (unchanged, owner-only access)
  • sentiment_bridge.json kept as dual-write compat shim until regime_filter migrates
        ▲ publish results                      │ read commands
        │                                      ▼
TIER 2 — PROCESSING                    services/{domain}_svc FastAPI
  options_svc · sentiment_svc · trade_svc · portfolio_svc · driver_svc
  Each: imports ONLY its own engines; owns its scheduler/auto-scan;
  consumes its command stream; validates + caches + publishes results.
  Separate processes ⇒ scoring/notifier sys.path collisions cannot occur.
        │ HTTP (Schwab data/auth)
        ▼
  schwab-proxy (:8100)  — unchanged external-data gateway
```

## Component mapping (today → tier)

| Today | Tier | Becomes |
|-------|------|---------|
| `webgui/pages/**/render()` + pure transforms | 1 | unchanged shape; data source swapped to Redis |
| page-level engine calls, `options_scoring()` guard, autoscan loops | 2 | move into domain service; guard **deleted** (process isolation) |
| `_LAST_RESULTS`, `_CACHE`, version counters | 3 | `cache:{domain}:{view}` keys |
| `sentiment_bridge.json` (`bridge.write_bridge`) | 3 | pub/sub channel + cache key; file kept as temp dual-write shim |
| `schwab-proxy` (:8100) | edge of 3 | unchanged — services call it for Schwab data/auth |
| paper-trade DBs, `gex_history_db` | 3 | unchanged on disk; accessed only by owning service |

## Tier 3 — Storage & Communication

- **Cache:** `cache:{domain}:{view}` → `{"version": N, "ts": ISO, "payload": {...}}`.
  Views incl. `options:scan`, `options:swing`, `sentiment:composite`, `sentiment:sectors`,
  `sentiment:history`, `portfolio:positions`. GUI reads on page build → instant paint,
  survives navigation and restart (improves on in-process caches).
- **Pub/sub:** services `PUBLISH` to `events:{domain}:{view}` with the new version on every
  refresh. GUI holds one subscriber per process; on message it re-reads the cache key and
  repaints. Replaces the bridge file and the version-polling `ui.timer`s.
- **Command queue:** **Redis Streams** `cmd:{domain}` with a consumer group per service
  (durable, replayable, ack'd). Carries Run-scan / Refresh / paper-entry / order-approval.
  Result is cached + announced via pub/sub, so user clicks and the scheduler converge on one
  code path.
- **Contracts (`shared/contracts/`):** typed payload schemas (Pydantic/TypedDict) —
  `ScanResult`, `CompositeSnapshot`, `SectorPerf`, `Command`, … Both tiers import them;
  validated on write and read. This is what makes a later full physical split safe.
- **Persistence:** SQLite DBs + `gex_history_db` stay on disk, touched only by their owner.
  `sentiment_bridge.json` dual-written until `options-scanner/regime_filter` reads Redis.
- **Bus wrapper (`shared/bus/`):** thin redis-py wrapper — `publish/subscribe`,
  `cache_get/set`, `enqueue/consume_command` — contract-aware, auto-selects **fakeredis**
  under pytest (no live broker needed for tests).

## Tier 2 — Processing (per-domain services)

```
services/
  sentiment_svc/   app.py  scheduler.py  handlers.py   (imports sentiment-dashboard/*)
  options_svc/     app.py  scheduler.py  handlers.py   (imports options-scanner/*)
  trade_svc/       ...                                  (imports trade-analyzer/src)
  portfolio_svc/   ...                                  (imports portfolio-analyzer/src)
  driver_svc/      ...                                  (imports claude-driver/*)
```

Each thin FastAPI service does three things:
1. **Scheduler** — owns the auto-cadence that lived in the page modules. `options_svc`: the
   15-min 08:00–15:15 CT auto-scan (the `autoscan_due`/`_market_now`/holiday logic moves here
   verbatim). `sentiment_svc`: the 120 s composite refresh + bridge publish. Each run computes
   via engines → validates against a contract → `cache_set` → `PUBLISH` new version.
2. **Command consumer** — reads `cmd:{domain}`; on a command runs the engine on demand and
   publishes identically. User clicks and the scheduler share one path.
3. **Health** — `/health` for the launcher and the GUI status strip.

Notes:
- **Collision class dies here.** Separate processes ⇒ `import scoring`/`notifier` resolve
  unambiguously. `options_scoring()` and the `notifier` guard are **deleted, not ported** —
  called out so we don't reflexively carry the guard over.
- **schwab-proxy stays the data edge** (:8100); services resolve their Schwab client through it.
- **Engines reused, not rewritten** — `run_full_scan`, `compute_live`, `build_bridge_payload`,
  `backfill_history`, trade/portfolio/driver engines all imported as-is.

## Tier 1 — Presentation (webgui)

Pages keep their render shape, lose all non-UI concerns. Worked example —
`webgui/pages/options/scanner.py`:
- **Deleted:** `sys.path.insert(OPTIONS_SCANNER)`, `from scanner_engine import run_full_scan`,
  `engines`/`options_scoring()`, `_run_scan_sync`, `_autoscan_loop`, `start_autoscan`,
  `autoscan_due`, `_market_now`, holiday/window constants, `_LAST_RESULTS`.
- **Kept:** `signal_columns()`, `signal_rows()`, `_scan_meta_strip()`, `_select`,
  `_populate`, `render()` — pure transforms + widget wiring, still unit-testable.
- **New `webgui/bus_client.py`:** on page build `cache_get("cache:options:scan")` →
  `_populate` (instant paint); per-page subscriber to `events:options:scan` re-reads +
  repaints; "Run scan" → `enqueue_command("cmd:options", {type:"rescan"})`.
- **`main.py`:** `@app.on_startup` no longer starts `start_autoscan` /
  `start_background_refresh` (now in services). Proxy-down banner generalizes to a **services
  health** strip (each `/health` + Memurai reachability).
- **Net:** webgui imports only `nicegui`, `shared/bus`, `shared/contracts`. No engine imports,
  no hyphen-folder `sys.path` glue, no Schwab client. GUI cannot crash on an engine exception.

## Ports

Add to `config/ports.toml` + `repo_paths.py`: `memurai = 6379`, and one port per service
(e.g. `sentiment_svc = 8210`, `options_svc = 8211`, `portfolio_svc = 8212`,
`trade_svc = 8213`, `driver_svc = 8214` — exact numbers finalized in the plan).
`start_all` launcher order: **Memurai → proxy → domain services → webgui**.

## Migration sequence (strangler-fig — each step shippable, webgui stays green)

- **Step 0 — Foundation.** Install Memurai; add ports; build `shared/bus/` (+ fakeredis) and
  `shared/contracts/` (`CompositeSnapshot`, `SectorPerf`, `ScanResult`, `Command`). Unit-test
  bus + contracts in isolation. Nothing wired yet.
- **Step 1 — Sentiment (reference migration).** It already publishes a bridge. Stand up
  `services/sentiment_svc` (move 120 s refresh + `compute_live` + `backfill_history` + sector
  load + `build_and_write_bridge`). Dual-write Redis **and** `sentiment_bridge.json`. Rework
  the page to read `cache:sentiment:*` + subscribe. Verify legacy parity (82% green / Score
  7.8) against live proxy. **This is the pattern reference.**
- **Step 2 — Options.** scan + swing + autoscan → `options_svc`; delete `options_scoring()`.
  Highest payoff (removes worst collision risk).
- **Step 3 — Portfolio.** live streaming → `portfolio_svc` publishing `cache:portfolio:positions`
  the page subscribes to (replaces GUI stream-polling). Builds the currently-stub page natively.
- **Step 4 — Trade** and **Step 5 — Driver.** `trade_svc`, `driver_svc`; order-approval queue
  becomes a natural Redis Streams flow. Both pages are stubs today ⇒ born 3-tier.
- **Step 6 — Retire shims.** Migrate `options-scanner/regime_filter` to read
  `cache:sentiment:composite`; drop the bridge-file dual-write; remove the last `sys.path`
  engine-glue from webgui.

Per step: write contract → build service (TDD scheduler/handlers vs fakeredis) → cut page over
→ verify in browser against live proxy → commit.

## Testing & verification

- **Unit (bulk, no infra):** `shared/bus` auto-selects **fakeredis** under pytest. Contracts get
  round-trip + reject-malformed tests. Pure transforms keep existing tests verbatim (they never
  knew the data source) — most of the current 127 webgui tests survive unchanged. Service
  handlers/schedulers TDD'd with a fake engine + fakeredis ("scheduled run publishes a
  contract-valid blob + bumps version"; "command triggers one engine call + one publish").
- **Integration (per service folder):** fakeredis bus, drive a command through the stream, assert
  cache + event. One opt-in `@pytest.mark.live_redis` test hits real Memurai locally.
- **E2E / browser (Claude Preview):** per step, start Memurai → proxy → migrated service →
  webgui; screenshot; confirm legacy parity; verify push by triggering a run and watching the
  page repaint with no manual refresh.
- **Regression guardrail:** options-scanner's ~2 known date-relative failing tests left untouched.
