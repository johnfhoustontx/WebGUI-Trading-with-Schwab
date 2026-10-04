# schwab-proxy — CLAUDE.md

> Cross-app paths and service ports come from the root `repo_paths.py`
> (which reads `config/ports.toml`). Never hard-code `D:\` paths or ports —
> import them. See the root `CLAUDE.md` for the monorepo overview.

## Purpose

Central Schwab API gateway and token manager for the whole monorepo. It owns
Schwab OAuth, refreshes tokens, rate-limits outbound Schwab calls, keeps what it
fetches in memory (see "The local market-data store"), and exposes a
local HTTP API so the other apps share one set of credentials instead of each
authenticating directly. **It must be started first** — options-scanner,
sentiment-dashboard, and the Tier-2 services all fetch market data through it.

## Entry point & port

- Entry: `schwab_proxy.py` (run as the `trading-prod-proxy` systemd unit).
- Serves on `http://127.0.0.1:8100` (`PROXY_URL` / `PROXY_PORT` from
  `repo_paths.py`).
- **Runs until explicitly stopped.** The legacy daily auto-shutdown (the proxy
  self-terminated at 15:30 CT Mon–Fri via a `_shutdown_scheduler` thread) was
  **REMOVED 2026-07-22** per the user — its 24/7 consumers (market_svc's
  futures poll, off-hours pages) need the proxy up around the clock. Stop it
  alone with `systemctl --user stop trading-prod-proxy`, or with the whole stack
  (`systemctl --user stop trading-prod.target`, which is what the Stop All
  Services page runs).
- Key endpoints: `/health`, `/stats/api_calls` (per-day outbound Schwab API-call counts — today / last 7 / last 30 days; counted at the marketdata rate-limit chokepoint + the trader request loop into `data/api_call_counts.db`, best-effort/never-raises; feeds the webgui Settings "API usage" card), `/quote`, `/quotes`, `/chains`, `/pricehistory`,
  `/instruments` (fundamentals; `projection=fundamental` → P/E, growth, ROE,
  margins — used by trade_svc), `/passthrough` (five named market-data endpoints
  only — `PASSTHROUGH_ENDPOINTS`, matched exactly), `/accounts`, `/positions`,
  `/positions/{account_hash}`, `/transactions/{account_hash}`, and the
  trade-stream tracker (`/track`, `/untrack`).
- ⚠ **The account routes FAIL CLOSED, and there is no order route.**
  `/accounts`, `/positions*` and `/transactions*` depend on
  `require_account_secret`: with no `PROXY_SHARED_SECRET` configured they answer
  503 to everyone; with one, 401 without the matching `X-Proxy-Secret`.
  `trader_request` is GET-only and raises on anything else. A new route that
  calls `trader_request` must carry that dependency and a new write to the
  brokerage API must not exist at all — `tests/test_account_surface.py` fails on
  both. `require_secret` (a no-op with no secret) is for market data only.
- **`/positions` aggregates ALL linked accounts** (not just the first): it loops
  every account hash from `/accounts/accountNumbers`, normalizes each, and folds
  same-symbol holdings across accounts into one row via `_merge_positions` (sums
  qty/market-value/P&L, quantity-weights `avg_price`) so a multi-account user sees
  one whole-account book. A per-account fetch failure is logged and skipped; only a
  total failure surfaces. `/positions/{account_hash}` still returns a single account.

## Key files

| File                 | Role                                                            |
|----------------------|-----------------------------------------------------------------|
| `schwab_proxy.py`    | FastAPI/uvicorn app, token mgmt, proxy + trader endpoints, stream worker. |
| `market_store.py`    | What the proxy has already fetched, kept in memory: `ChainStore`, `QuoteStore`, `BarStore`, and the `Gateway` that decides per request between a stored answer and a call to Schwab. No FastAPI and no repo imports, so it is unit-testable on its own. |
| `api_call_counter.py`| Per-day count of calls SENT to Schwab (`api_calls`), plus the per-day breakdown of REQUESTS by endpoint, caller and outcome (`api_calls_detail`). Never raises. |
| `proxy_client.py`    | Client helper imported by the other apps to call the proxy. Sets `X-Caller`, takes `max_age=` on `get_option_chain`, and exposes `X-Store` / `X-Store-Age` as `FakeResponse.store_kind` / `store_age`. |
| `trade_registry.py`  | Registry of tracked OptionsScanner paper trades.                |
| `trade_detector.py`  | Detects fills/events from the option stream.                    |
| `perf_writer.py`     | Writes trade-performance events + IV snapshots.                 |
| `stream_bridge.py`   | `schwab.streaming` LEVELONE_OPTIONS/EQUITIES subscription bridge. |

## The local market-data store (`market_store.py`)

`/quote`, `/quotes`, `/chains` and `/pricehistory` are thin adapters over
`market_store.Gateway`; every decision lives there. Settings are
`config/marketdata.toml`, read through `shared/marketdata_config.py` on every
request (no restart). **It ships `mode = "shadow"`**: every request still goes
to Schwab, and the gateway only records what `on` would have answered. `off`
passes straight through; `on` answers repeats from memory. Anything else reads
as `off`. Nothing is written to disk or Redis; a restart starts empty. Design:
`docs/plans/2026-10-03-market-data-store-design.md`. The rules a caller must
know are in the root `CLAUDE.md` ("The proxy can answer from memory").

- **`maxAge=<seconds>`** on `/quote`, `/quotes` and `/chains` — the oldest
  stored answer the caller accepts. ⚠ It is declared as TEXT on purpose: it is
  a hint, and a value that is not a usable number (text, negative, NaN,
  infinity) means "none given" and never fails the request with a 422. Capped
  at an hour (`MAX_REQUEST_AGE_SEC`). `0` always fetches. With none given the
  configured limit applies. `/pricehistory` takes none.
- **`X-Caller` request header** — who is asking, for the per-caller counts.
  Cut to letters, digits, `_`, `.` and `-`, 40 characters; missing is
  `unknown`. ⚠ The name becomes a key in the per-day counts, so one process
  counts at most 64 distinct names (`MAX_CALLER_NAMES`) and a new one past
  that is `other`.
- **`X-Store` / `X-Store-Age` response headers** on all four endpoints, in
  every mode. `X-Store` is `hit` · `subset` · `coalesced` · `composed`
  (answered locally) or `miss` · `partial` · `pass` (Schwab was called; `pass`
  means the store played no part — mode `off` or `shadow`, an intraday series,
  or a store fault). `X-Store-Age` is seconds since the data left Schwab,
  stamped from when the fetch BEGAN.
- **`/stats/api_calls`** returns two more keys. `store` is today's request
  breakdown: `served_locally`, `by_outcome`, and `rows`
  (`{endpoint, caller, outcome, n}`) — ⚠ `rows` lists at most the 500 largest
  (`MAX_DETAIL_ROWS`) while both totals cover every row. `store_degrades` is
  `{area: count}` of store faults since the process started that fell back to a
  plain fetch; anything but empty is worth a look. `tracker` is
  `{tracked, not_followed, failing}` for the paper-trade tracker. `today` / `last_7_days` /
  `last_30_days` still count calls SENT to Schwab: a local answer skips
  `_rate_limit` and `api_call_counter.record`.
- **A dead token answers 500 with a JSON `detail` body.** `api_request` raises
  when the token cannot be made valid; `_upstream` turns that into an
  `UpstreamError(500, ...)` so the gateway does not read it as a store fault
  (which would count a degrade and make the call a second time).
- In shadow mode the outcomes recorded beside `upstream` are `shadow_*` names
  (`Gateway`'s docstring lists them); a would-be daily-bar hit is recorded as
  `shadow_hit_match` / `shadow_hit_mismatch`. Shadow counts low, with one
  known exception (the collector's own repeats while every session is closed,
  about 700 a day on `chains` for caller `options_svc`); see the root
  `CLAUDE.md`.

## The paper-trade tracker (`/track`, `/untrack`, the 30-second reconcile)

The tracker streams the legs of OPEN paper-ledger trades and fires three
events: target (half the credit captured), stop (twice the credit) and short
strike tested. **Those are credit-spread rules, so it follows `PCS`, `CCS` and
`IC` only** (`trade_registry.TRACKED`). The ledger also holds debit structures:
their `entry_credit` is negative and their strikes live in `legs`, not in the
four strike columns. A row named `IRON_CONDOR` is read as `IC` when it carries
all four strikes.

- ⚠ **Decide before you fetch.** `trade_registry.track_refusal(body)` answers
  from the trade alone (structure, symbol, expiration, usable strikes, a
  positive credit) and `_track` calls it BEFORE the chain request. Until
  2026-10-03 the structure was only discovered after the chain was fetched, so
  every open debit trade cost one Schwab chain call and one ERROR line every 30
  seconds for as long as it stayed open (measured on prod: 4.0 calls a minute
  for two open trades, 48,462 ERROR lines since 2026-09-15).
- ⚠ **A loop that retries must remember what failed.** `_reconcile_once` keeps
  `trade_registry.TrackAttempts`. A trade answered `skipped` (the tracker will
  never follow it) is not tried again. One answered `error` is tried again
  after 30 seconds, then 60, 120 ... up to a limit from
  `config/marketdata.toml` `[tracker]`: `fetch_retry_max_sec` (5 minutes) when
  Schwab did not send the chain (an error status, or `api_request` raising
  because the token cannot be made valid), so tracking resumes soon after an
  outage or a re-authorization, and `retry_max_sec` (30 minutes) otherwise. A
  setting that is not a number above zero is replaced by that limit's own
  built-in value. The memory is dropped when the trade closes or becomes
  tracked.
- ⚠ **"Reported once" keys on a stable name, not on the message.** `_track`
  returns a `key` beside `detail`; the same key as last time is logged at
  DEBUG. The detail of a failed chain fetch carries Schwab's error body, which
  can differ on every attempt, so keying on it would log every retry.
- **The standing signal** is the reconcile summary line
  (`not_followed=N failing=M`, at INFO whenever those counts change) and the
  `tracker` block of `/stats/api_calls`.
- `_track` returns `ok`, `skipped` or `error` and never raises. A skipped trade
  gets no streaming events; the ledger's own hourly manage cycle still prices
  and settles it.
- `_track`'s chain request does not go through the market-data store.

**Streaming SSE fan-outs (2026-07-07).** The shared `_stream_worker` fans level-one ticks to SSE
subscribers via **`/stream/quotes?symbols=…`** (equities — `_normalize_level1_equity` widened with
bid/ask/sizes/last-size/volume + RTH `REGULAR_MARKET_*` fallbacks) and **`/stream/options?symbols=<OSI,…>`**
(options — new `_normalize_level1_option` + a refcounted OSI union that is **provably additive to
paper-trade tracking**: the reconcile subscribes `_registry.legs_union() ∪ flow_osis` on the serialized
stream loop, and the trade-untrack orphan guard spares `_option_refcount`, so a tracked leg can NEVER
lose its subscription; the `_on_option_message` trade-detector block is byte-identical, fan-out appended
after). Consumed by `portfolio_svc` (equity P&L) + `sentiment_svc`'s `order_flow_consumer` (aggressor
order-flow for the five-state classifier). Both refcounts support multiple concurrent subscribers.

## Logging

`logs/schwab_proxy.log` holds the full INFO stream. A dedicated
`logs/errors.log` captures **ERROR/CRITICAL only**, via a
`TimedRotatingFileHandler` that rotates **weekly (Monday)** and keeps
`backupCount=4` weeks before auto-deleting the oldest — effectively a weekly
purge. Both handlers plus the console are wired in the `logging.basicConfig`
block at the top of `schwab_proxy.py`.

## Configuration & secrets

Reads `shared/appsettings.json` (Schwab API keys) and `shared/tokens.json`
(OAuth) via `repo_paths.APPSETTINGS` / `repo_paths.TOKENS` — both gitignored,
with `.example` templates in `shared/`. Maintains `proxy_tokens.json`
(gitignored runtime cache). Writes trade DBs into `options-scanner/data/`.

## Dependency on the proxy

This **is** the proxy — it has no upstream dependency in the repo. It only needs
valid Schwab credentials in `shared/` and a network connection to Schwab.

## Tests

```powershell
cd schwab-proxy && python -m pytest tests
```
