[TOC]

# About this document

This is the **integration reference** for the WebGUI Trading with Schwab 3-tier
architecture: the contracts, the Redis bus API, each service's commands and
published views, and the Schwab proxy's HTTP surface. It is aimed at developers
extending the stack or wiring a new client to it.

For the math behind the cached payloads, see the *Technical Reference*; for the
end-user view, see the *User Guide*.

---

# 3-Tier Overview

```
TIER 1  GUI (webgui, :8500)  ──enqueue command──▶  cmd:{domain}  (Redis Stream)
        ▲                                                  │
        │  read cache / subscribe events                   ▼
TIER 3  Redis (Memurai, :6379)  ◀──cache_set + publish──  TIER 2  services
        ▲                                                  │
        │                                                  ▼  market data
        └──────────────  schwab-proxy (:8100)  ◀───────────┘
```

**Rules of the model:**

- The GUI imports only `nicegui` + `shared.bus` + `shared.contracts`. It never
  imports an engine, never calls Schwab, and never computes domain results.
- Services never call each other. They communicate only by reading/writing Redis.
- All market data flows through the proxy; no service holds Schwab credentials.
- Every cache write **increments a version counter**; the GUI polls versions
  cheaply and only re-reads the payload when the version changes.

---

# Bus / Redis API

**File:** `shared/bus/client.py`. The `Bus` class wraps redis-py (and `fakeredis`
under pytest). Connection from `repo_paths.MEMURAI_URL` (default
`redis://127.0.0.1:6379/0`).

## Key naming conventions

| Pattern | Meaning |
|---------|---------|
| `cache:{domain}:{view}` | Versioned cache key holding a `CacheEnvelope`. |
| `cache:{domain}:{view}:ver` | Integer version counter, `INCR`'d by `cache_set`. |
| `events:{domain}:{view}` | Pub/sub channel; messages are `{"version": int}`. |
| `cmd:{domain}` | Redis Stream of commands (GUI → service RPC). |

## Cache methods

**`cache_set(key, payload, event=None, skip_unchanged=False) -> int`**
Atomically increments `{key}:ver`, stores a `CacheEnvelope` (version + ISO ts +
payload) at `key`, and returns the new version.
- `event` — when given, publishes `{"version": v}` on that channel in the same
  pipeline.
- `skip_unchanged=True` — if the payload is byte-identical to what's stored, the
  whole write *and* publish are skipped (no version bump, so GUI pollers don't
  repaint). Used by tick republishers (header, GEX status).

**`cache_get(key) -> CacheEnvelope | None`** — deserializes the full envelope.

**`cache_version(key) -> int | None`** — reads only the `:ver` counter (cheap; no
payload deserialize). This is what GUI poll timers use.

**`cache_versions(keys) -> dict`** — pipelined `cache_version` for many keys in one
round-trip (use when a page polls several views).

## Pub/sub

**`publish(channel, message) -> None`** — JSON-publish a dict.

**`subscribe(channel)`** — context manager yielding a subscription;
`sub.get_message(timeout)` returns a decoded dict or `None`.

```python
with bus.subscribe("events:sentiment:composite") as sub:
    msg = sub.get_message(timeout=5.0)   # {"version": 42} or None
```

## Command streams

**`enqueue_command(stream, command) -> str`** — validates `command` as a `Command`
(`{type, args}`) and `XADD`s it to the stream; returns the message id.

**`consume_commands(stream, group, consumer, block_ms=50, count=10) -> list[(msg_id, Command)]`**
— reads up to `count` pending messages for a consumer group, blocking up to
`block_ms`. The consumer group is auto-created on first call.

**`ack(stream, group, msg_id) -> None`** — acknowledges a message (the service
scaffold auto-acks after the handler returns).

---

# Contracts

**Folder:** `shared/contracts/`. Pydantic models validated **before** `cache_set`,
so gross shape drift fails loudly. The strictly-typed domain contracts are below;
several options/sentiment views are validated defensively in compute rather than by
a contract (listed in *Cache Key Index*).

## Envelope types (`envelope.py`)

| Class | Fields |
|-------|--------|
| `CacheEnvelope` | `version: int`, `ts: str` (ISO), `payload: dict` |
| `Command` | `type: str`, `args: dict = {}` |

## Domain contracts

| Class | File | Cache key | Key fields |
|-------|------|-----------|-----------|
| `ScanResult` | `options.py` | `cache:options:scan` | `signals_0dte[]`, `signals_swing[]`, `vix_term_structure{}`, `timestamp`, `errors[]`, `warnings[]` |
| `TradeAnalysis` | `trade.py` | `cache:trade:analysis` | `symbol`, `description`, `price`, `volume`, `bias`, `ema_alignment{}`, `momentum{}`, `volume_profile{}`, `sector{}`, `position_verdict{}`, `investor_verdict{}`, `fundamentals{}`, `fundamentals_available`, `markov{}` (optional), `timestamp`, `errors[]` |
| `PortfolioModel` | `portfolio.py` | `cache:portfolio:positions` | `holdings_rows[]`, `sector_rows[]`, `performance_rows[]`, `suggestions{}`, `proxy_up`, `streaming`, `errors[]`, `timestamp` |
| `ApprovalState` | `driver.py` | `cache:driver:approvals` | `date`, `grade`, `grade_reasons[]`, `conditions{}`, `pnl_today`, `pnl_week`, `proposed_trades[]`, `status`, `decision`, `results[]`, `reasons[]`, `error`, `timestamp` |
| `PerfReport` | `driver.py` | `cache:driver:performance` | `summary{}`, `trades[]`, `timestamp` |
| `CompositeSnapshot` | `sentiment.py` | (validation only) | `total: float`, `bias: str`, `components{}` |
| `RescueAdvisory` | `options.py` | `cache:options:rescue:<position_id>` | `position_id`, `symbol`, `strategy`, `state`, `heat`, `mark`, `context[]`, `candidates[]`, `error` |
| `RescueCandidate` | `options.py` | (embedded in `RescueAdvisory.candidates`) | `action`, `label`, `apply_kind` (`execute`\|`advisory`), `gross_cash`, `commission`, `net_cash`, `new_max_loss`, `breakeven`, `short_delta`, `width`, `expiry`, `dte_after`, `est_fill_legs[]`, `rationale[]`, `context[]`, `warnings[]`, `score` |

---

# Per-Service Reference

Each service is an async FastAPI app built by `services/_scaffold.py:make_app`,
which provides the lifespan, the command-consumer loop, and a `GET /health`
endpoint returning `{"domain": ..., "up": true}`.

## Sentiment service — :8210

**Entry:** `services/sentiment_svc/app.py`. **Scheduler:** full refresh at startup,
composite-only every 120 s, trend recompute gated to 15 min, rotation at startup.

**Commands (`cmd:sentiment`):**

| Type | Args | Effect |
|------|------|--------|
| `refresh` | — | Full refresh: load snapshots + live data, recompute trend if due; publish composite, history, sectors. |
| `refresh_rotation` | — | Rotation assessment + S&P weights + risk threshold. |

**Published views:**

| Cache key | Event | Payload |
|-----------|-------|---------|
| `cache:sentiment:composite` | `events:sentiment:composite` | `{live, composite_at, proxy_up, derived}` |
| `cache:sentiment:history` | (none) | `{snaps[], spy[]}` |
| `cache:sentiment:sectors` | `events:sentiment:sectors` | `{sector, industries, sector_at, summary}` |
| `cache:sentiment:rotation` | `events:sentiment:rotation` | `{assessment, weights, risk_threshold, error}` |

## Options service — :8211

**Entry:** `services/options_svc/app.py`. **Scheduler:** auto-scan (15-min slots,
08:00–15:15 CT), GEX collection (2-min slots, 08:30–15:20 CT), paper auto-manage
(5 min in market hours), header tick (each 30 s, skip-unchanged).

**Commands (`cmd:options`):**

| Type | Args | Published view |
|------|------|----------------|
| `rescan` | — | `cache:options:scan` |
| `swing_scan` | `{symbol, dte_min, dte_max, put_d_min, put_d_max, call_d_min, call_d_max, min_cr_fraction}` | `cache:options:swing` |
| `refresh_paper` | — | `cache:options:paper_account` |
| `paper_entry` | — | `cache:options:paper_account` |
| `paper_manage` | — | `cache:options:paper_account` |
| `paper_reset` | `{starting_balance}` | `cache:options:paper_account` |
| `paper_create` | `{signal, qty}` | `cache:options:paper_trades` |
| `paper_reload` | — | `cache:options:paper_trades` |
| `paper_close` | `{trade_id, debit}` | `cache:options:paper_trades` |
| `paper_delete` | `{trade_id}` | `cache:options:paper_trades` |
| `paper_delete_closed` | — | `cache:options:paper_trades` |
| `paper_analyze` | `{trade_id}` | `cache:options:paper_analyze` |
| `captured_reload` | — | `cache:options:captured` |
| `captured_reprice` | — | `cache:options:captured` + `cache:options:captured_flags` |
| `captured_close` | `{signal_id, exit_val, reason}` | `cache:options:captured` |
| `gamma_refresh` | `{symbol}` | `cache:options:gamma` |
| `gamma_explain` | `{symbol}` | `cache:options:gamma_explain` |
| `gamma_analyze` | — | `cache:options:gamma_analyze` |
| `sim_fetch` | `{symbol}` | `cache:options:sim_meta` |
| `sim_run` | `{symbol, legs[], dt, mult}` (legs: `{kind, strike, expiry, side, qty}`; legacy `{expiry, kind, strike, direction}` single-leg args still accepted) | `cache:options:sim_result` |
| `sim_replay` | `{symbol, legs[], lookback}` (same multi-leg shape; legacy single-leg args still accepted) | `cache:options:sim_replay` |
| `calc_load` | `{symbol}` | `cache:options:calc_chain` |
| `calc_compute` | `{strategy, spot, iv, rate, ivadj, qty, expiry, legs[], range_*}` (each leg carries its own `expiry`/`qty`; `strategy="CUSTOM"` or any non-PCS/CCS/IC/single code → generic numeric summary) | `cache:options:calc_result` |
| `expected_move` | `{symbol, expiry, legs[], lookback}` | `cache:options:expected_move` |
| `rescue` | `{position_id}` | `cache:options:rescue:<position_id>` |
| `rescue_apply` | `{position_id, candidate}` | `cache:options:rescue:<position_id>` |

**Scheduled (not command-driven):** `rescan` (auto-scan window), `refresh_header`
(per tick, skip-unchanged), `collect_gex_snapshots` + `publish_gex_status` +
`publish_gamma_symbols` (GEX window), `run_manage_and_refresh` (paper-manage window).
The paper-manage cycle also overlays `rescue_state` / `heat` onto
`cache:options:paper_account` and publishes `cache:options:rescue_summary` (tested +
critical counts) for the nav badge.

## Portfolio service — :8212

**Entry:** `services/portfolio_svc/app.py`. **Scheduler:** initial rebuild + SSE
quote-stream worker; throttled publish ≤ every 2 s while ticks pending; full rebuild
every 10 min or on a queued refresh.

**Commands (`cmd:portfolio`):**

| Type | Args | Effect |
|------|------|--------|
| `refresh` | — | Sets `state.rebuild_requested`; the scheduler performs the rebuild and restarts the stream. |

**Published view:** `cache:portfolio:positions` / `events:portfolio:positions` →
the `PortfolioModel` contract.

## Trade service — :8213

**Entry:** `services/trade_svc/app.py`. **Scheduler:** none (on-demand only).

**Commands (`cmd:trade`):**

| Type | Args | Effect |
|------|------|--------|
| `analyze` | `{symbol}` | MTF technical + fundamental analysis, plus the **Markov 2.0** forecast (5-band composite-score chain → band-probability forecast + bounded drift tilt) → `cache:trade:analysis`. |

**Published views:**

| Cache key | Event | Payload |
|-----------|-------|---------|
| `cache:trade:analysis` | `events:trade:analysis` | `TradeAnalysis` — verdicts + momentum + sector + fundamentals + the optional `markov` forecast block. |
| `cache:trade:markov_prior` | — | Pooled Markov transition prior `{matrix[5][5], date, n_symbols}`; rebuilt lazily once/day and read by `analyze` (internal memoization — no event). |

## Driver service — :8214

**Entry:** `services/driver_svc/app.py`. **Scheduler:** morning pipeline once/day at
09:28 ET; perf refresh ≈ every 5 min. The scheduler **never executes orders** — only
an explicit `approve` does.

**Commands (`cmd:driver`):**

| Type | Args | Effect |
|------|------|--------|
| `run` | — | Run the morning pipeline → cache a *pending* `ApprovalState`. |
| `approve` | — | If still pending, execute the proposed trades (simulated, `PAPER_TRADE=True`), re-cache as `approved` with results. |
| `skip` | — | Mark the cached approval `skipped`. |
| `perf` | — | Recompute the `PerfReport` → `cache:driver:performance`. |

---

# Schwab Proxy

**Entry:** `schwab-proxy/schwab_proxy.py`, port **8100**. Central token manager +
HTTP gateway. GET market-data and Trader calls are rate-limited (~200 ms spacing)
and retried up to 3× with backoff (0.25 / 0.5 / 1.0 s); order POSTs are **single
attempt** (never duplicate a submitted order).

## Health & auth

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/health` | GET | `{status, has_token, token_expired, refresh_token_expired, token_file, timestamp}` |
| `/auth` | GET | HTML OAuth login page |
| `/auth/callback` | GET | Exchanges the OAuth `code`/`url` for tokens |

## Market data

| Endpoint | Params | Returns |
|----------|--------|---------|
| `/quote` | `symbol` | Single quote |
| `/quotes` | `symbols` (comma-sep) | Quotes array |
| `/chains` | `symbol, contractType, range, fromDate?, toDate?, strikeCount?` | Options chain |
| `/pricehistory` | `symbol, periodType, period, frequencyType, frequency, needExtendedHoursData` | Price bars |
| `/instruments` | `symbol, projection` (e.g. `fundamental`) | `{instruments:[{fundamental, symbol, description, ...}]}` |
| `/passthrough` | `endpoint, params` | Generic marketdata fallback |

All return `{status_code, data, error}`.

## Trader API

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/accounts` | GET | `[{hashValue, accountNumber, ...}]` |
| `/positions/{account_hash}` | GET | Normalized positions (net qty; options carry underlying) |
| `/transactions/{account_hash}` | GET (`start_date, end_date`) | Normalized TRADE transactions |
| `/orders/{account_hash}` | POST (Schwab order body) | `{status: "submitted", status_code, data}` |

## Trade-stream tracker

Daemon threads register paper trades and stream option ticks (degrade gracefully if
streaming fails).

| Endpoint | Method | Body |
|----------|--------|------|
| `/track` | POST | `{trade_id, symbol, strategy, expiration, quantity, entry_credit, short_strike, long_strike, call_short, call_long, target_mid, stop_mid}` |
| `/untrack` | POST | `{trade_id}` |

---

# Cache Key Index

Every Redis key by domain. Views without a strict contract are validated
defensively in compute.

**Sentiment:**

```
cache:sentiment:composite      events:sentiment:composite
cache:sentiment:history        (no event)
cache:sentiment:sectors        events:sentiment:sectors
cache:sentiment:rotation       events:sentiment:rotation
cmd:sentiment
```

**Options:**

```
cache:options:scan             events:options:scan          (ScanResult contract)
cache:options:header           events:options:header
cache:options:swing            events:options:swing
cache:options:paper_account    events:options:paper_account
cache:options:paper_trades     events:options:paper_trades
cache:options:paper_analyze    events:options:paper_analyze
cache:options:captured         events:options:captured
cache:options:captured_flags   events:options:captured_flags
cache:options:gamma            events:options:gamma
cache:options:gamma_explain    events:options:gamma_explain
cache:options:gamma_analyze    events:options:gamma_analyze
cache:options:gamma_symbols    events:options:gamma_symbols
cache:options:sim_meta         events:options:sim_meta
cache:options:sim_result       events:options:sim_result
cache:options:sim_replay       events:options:sim_replay
cache:options:calc_chain       events:options:calc_chain
cache:options:calc_result      events:options:calc_result
cache:options:gex_status       events:options:gex_status
cache:options:expected_move    events:options:expected_move
cache:options:rescue:<position_id>   events:options:rescue:<position_id>   (RescueAdvisory contract)
cache:options:rescue_summary   events:options:rescue_summary
cmd:options
```

**Trade / Portfolio / Driver:**

```
cache:trade:analysis           events:trade:analysis          (TradeAnalysis)
cache:portfolio:positions      events:portfolio:positions     (PortfolioModel)
cache:driver:approvals         events:driver:approvals        (ApprovalState)
cache:driver:performance       events:driver:performance      (PerfReport)
cmd:trade   cmd:portfolio   cmd:driver
```

---

# Service Scaffold

**File:** `services/_scaffold.py`. Each service is one call:

```python
app = make_app(
    "trade",
    scheduler=scheduler.loop,              # optional async def loop(bus)
    command_handler=handlers.handle_command,  # optional callable(bus, command)
)
```

The scaffold provides:

1. **Lifespan** — creates the `Bus` (Redis, or `fakeredis` under pytest), spawns the
   scheduler task and the command-consumer loop, and cancels both on shutdown.
2. **Command consumer** — `consume_commands("cmd:{domain}", group="{domain}-svc",
   consumer="c1")`, blocking 50 ms per poll, up to 10 messages per batch; dispatches
   to the handler via an executor; acks after the handler returns; swallows and logs
   handler exceptions.
3. **Health** — `GET /health` → `{"domain": ..., "up": true}`.

> **Process isolation matters.** Several app folders expose same-named top-level
> modules (`config`, `scoring`, `notifier`, `src`). Running two service test suites
> in one process re-triggers those collisions — run service suites **one folder at a
> time**.

---

# End-to-End Flow Example

**User clicks "Analyze SPY" on the Trade page:**

1. GUI: `bus.enqueue_command("cmd:trade", {"type": "analyze", "args": {"symbol": "SPY"}})`.
2. The command lands in Redis Stream `cmd:trade`; the consumer group `trade-svc`
   is auto-created on first poll.
3. The trade service consumer reads it and dispatches to
   `handlers.handle_command(bus, command)`.
4. The handler runs `compute.analyze("SPY")` → a result dict (price, bias, momentum,
   verdicts, fundamentals, errors).
5. The dict is projected onto the `TradeAnalysis` contract (defaults filled, types
   validated).
6. `bus.cache_set("cache:trade:analysis", payload, event="events:trade:analysis")`
   increments `cache:trade:analysis:ver`, stores the envelope, publishes the version.
7. The message is acked.
8. The GUI's 1 s version-poll sees the new version, reads `cache:trade:analysis`, and
   repaints the verdict cards.

A developer can drive this entire path headlessly — bypassing the browser — with:

```python
from shared.bus import Bus
bus = Bus()
bus.enqueue_command("cmd:trade", {"type": "analyze", "args": {"symbol": "SPY"}})
# ...wait briefly...
env = bus.cache_get("cache:trade:analysis")
print(env.payload)
```

---

# Ports & Paths

`repo_paths.py` (reading `config/ports.toml`) is the single source of truth — never
hard-code ports or `D:\` paths.

| Component | Port | `repo_paths` |
|-----------|------|--------------|
| schwab-proxy | 8100 | `PROXY_PORT` / `PROXY_URL` |
| Memurai (Redis) | 6379 | `MEMURAI_PORT` / `MEMURAI_URL` |
| sentiment_svc | 8210 | `SERVICE_PORTS["sentiment"]` / `SERVICE_URLS["sentiment"]` |
| options_svc | 8211 | `SERVICE_PORTS["options"]` |
| portfolio_svc | 8212 | `SERVICE_PORTS["portfolio"]` |
| trade_svc | 8213 | `SERVICE_PORTS["trade"]` |
| driver_svc | 8214 | `SERVICE_PORTS["driver"]` |
| webgui (NiceGUI) | 8500 | `NICEGUI_PORT` / `NICEGUI_URL` |

> The `dashboard_frontend = 5173` entry in `config/ports.toml` belongs to the retired
> React frontend and is **not** used by this app. The web GUI is on **8500**.

**Key paths:**

| Path | Holds | Status |
|------|-------|--------|
| `shared/appsettings.json` | Schwab API keys | gitignored (template `*.example.json`) |
| `shared/tokens.json` | Schwab OAuth tokens | gitignored |
| `schwab-proxy/proxy_tokens.json` | Proxy runtime tokens | gitignored |
| `shared/sentiment_bridge.json` | Sentiment bridge (legacy shim) | gitignored |
| `repo_paths.REPO_ROOT` | Repo root | — |
