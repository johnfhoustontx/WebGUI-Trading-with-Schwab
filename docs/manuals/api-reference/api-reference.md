[TOC]

# About this document

This is the **integration reference** for the WebGUI Trading with Schwab 3-tier
architecture: the contracts, the Redis bus API, each service's commands and
published views, and the Schwab proxy's HTTP surface. It is aimed at developers
extending the stack or wiring a new client to it.

For the math behind the cached payloads, see the *Technical Reference*; for the
end-user view, see the *User Guide* and the *Reference Guide*.

## Finding the service behind a screen

**This document is organised by tier and service, not by menu**, because a service
is not a menu item: `options_svc` alone backs nine screens, and several screens read
more than one domain. Use this map to get from a screen to the service and cache
keys that feed it. Menu order matches the rail.

| Menu page | Service | Primary cache key(s) |
|---|---|---|
| **Dealer Positioning** | `options_svc` :8211 | `cache:options:gamma`, `:gamma_hist_*`, `:gamma_symbols`, `:net_premium`, `:gamma_analyze*`, `:gamma_briefings` |
| **Opportunity Board** | `options_svc` | `cache:options:matrix` |
| **Flow Alerts** | `options_svc` | `cache:options:flow_alerts` |
| **Market Dashboard** | `market_svc` :8215 | `cache:market:dashboard`, `:summary` |
| **Sentiment** | `sentiment_svc` :8210 | `cache:sentiment:composite`, `:regime`, `:regime_history`, `:intraday_history` |
| **Sector & Industry** | `sentiment_svc` | `cache:sentiment:sectors` |
| **Sector Rotation** · **RRG** | `sentiment_svc` | `cache:sentiment:rotation` |
| **Momentum** | `sentiment_svc` | `cache:sentiment:momentum` |
| **Calculator** | `options_svc` | `cache:options:calc_chain`, `:calc_result`, `:calc_iv` |
| **Simulator** | `options_svc` | `cache:options:sim_meta`, `:sim_result`, `:sim_replay` |
| **Market Scanner** | `options_svc` | `cache:options:scan_day` (rendered), `:scan` (live counts) |
| **Strategy Finder** | `options_svc` | `cache:options:swing` |
| **Expected Move** | `options_svc` | `cache:options:em_chain`, `:expected_move` |
| **Captured Signals** | `options_svc` | `cache:options:captured`, `:captured_flags`, `:captured_closed` |
| **Paper Ledger** | `options_svc` | `cache:options:paper_trades`, `:paper_analyze` |
| **Paper Account** | `options_svc` | `cache:options:paper_account`, `:paper_analytics` |
| **Rescue** | `options_svc` | `cache:options:rescue:<position_id>`, `:rescue_summary` |
| **Trade Analyzer** | `trade_svc` :8213 | `cache:trade:analysis`, `:deepdive`, `:deepdive_query` |
| **Claude Trades** | `driver_svc` :8214 decides, `options_svc` executes | `cache:driver:autonomous`, `:control`, `cache:options:driver_paper_account`, `:driver_paper_perf` |
| **Portfolio** | `portfolio_svc` :8212 | `cache:portfolio:positions` |
| **EOD Report** | none — pure Tier-1 reader | aggregates the `options:*` and `driver:*` keys |
| **System Status** | none — probes `/health` directly | reads every domain's `:ver` / `:ts` side keys |

---

# 3-Tier Overview

```
TIER 1  GUI (webgui, :8500)  ──enqueue command──▶  cmd:{domain}  (Redis Stream)
        ▲                                                  │
        │  read cache / subscribe events                   ▼
TIER 3  Redis (:6379)  ◀──cache_set + publish──  TIER 2  services
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
| `cache:{domain}:{view}:ts` | ISO freshness stamp, `SET` by `cache_set` in the same pipeline. |
| `events:{domain}:{view}` | Pub/sub channel; messages are `{"version": int}`. |
| `cmd:{domain}` | Redis Stream of commands (GUI → service RPC). |
| `dead:{domain}` | Dead-letter list for commands that could not be parsed or handled. |

**Why two side keys.** `:ver` answers *"has this changed?"* and `:ts` answers
*"when did the publisher last confirm this is current?"* — which are different
questions, and the difference matters for `skip_unchanged` writes. A payload that
is republished byte-identically does **not** bump `:ver` (so pollers do not
repaint) but **does** refresh `:ts` (so freshness monitoring still sees a live
publisher). The `/status` page's freshness table reads `:ts`.

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

**`cache_metas(keys) -> dict`** — pipelined read of the `:ver` **and** `:ts` side
keys for many keys at once, with no payload deserialize. This is what the app-wide
freshness watcher uses; `ts` is `None` for a key written before the `:ts` side key
existed.

## Dead-lettering

**`enqueue_command`** validates before writing, but a malformed or unhandleable
message that reaches a consumer is moved aside rather than retried forever:

**`dead_letter(stream, raw_fields, reason) -> None`** — records the raw message and
why it failed under `dead_letter_key(stream)` (`dead:{domain}`).

**`drain_pending(stream, group, consumer) -> int`** — reclaims messages left
pending by a consumer that died mid-handler, returning how many were recovered.
The service scaffold calls this at startup.

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
| `TradeAnalysis` | `trade.py` | `cache:trade:analysis` | `symbol`, `description`, `price`, `volume`, `bias`, `ema_alignment{}`, `momentum{}`, `volume_profile{}`, `sector{}`, `position_verdict{}`, `investor_verdict{}`, `fundamentals{}`, `fundamentals_available`, `markov{}` (optional), `swing_model{}` (optional), `timestamp`, `errors[]` |
| `PortfolioModel` | `portfolio.py` | `cache:portfolio:positions` | `holdings_rows[]`, `sector_rows[]`, `performance_rows[]`, `suggestions{}`, `proxy_up`, `streaming`, `errors[]`, `timestamp` |
| `DriverControl` | `driver.py` | `cache:driver:control` | `enabled`, `halted`, `reason`, `halted_date` (ISO date the latch was set, so it re-arms next day), `timestamp` |
| `AutonomousState` | `driver.py` | `cache:driver:autonomous` | `date`, `enabled`, `halted`, `halt_reason`, `day_pnl`, `target`, `positions[]`, `decisions[]` (newest-first checkpoint log), `perf{}`, `last_cycle_ts`, `error`, `timestamp` |
| `MarketDashboard` | `market.py` | `cache:market:dashboard` | `categories[]` (ordered frames of display-ready tiles), `proxy_up`, `errors[]` |
| `MarketSummary` | `market.py` | `cache:market:summary` | `narrative` (a short Claude-written verdict; empty when there is no key or the call failed) |
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
| `gamma_refresh` | `{symbol}` | `cache:options:gamma` + `cache:options:gamma_hist_{view}` |
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
| `analyze` | `{symbol}` | MTF technical + fundamental analysis, plus the **validated swing model** verdict (Position), the **Markov 2.0** forecast (5-band composite-score chain → band-probability forecast + bounded drift tilt), and the Investor verdict → `cache:trade:analysis`. |

**Published views:**

| Cache key | Event | Payload |
|-----------|-------|---------|
| `cache:trade:analysis` | `events:trade:analysis` | `TradeAnalysis` — verdicts + momentum + sector + fundamentals + the optional `markov` forecast block + the optional `swing_model` block. |
| `cache:trade:markov_prior` | — | Pooled Markov transition prior `{matrix[5][5], date, n_symbols}`; rebuilt lazily once/day and read by `analyze` (internal memoization — no event). |
| `cache:trade:universe_factors` | — | Daily swing-model factor snapshot `{factors{factor: [values]}, date}` across a curated universe; rebuilt lazily once/day and read by `analyze` as the **secondary** cross-sectional fallback basis (the artifact's `norm` is primary). No event. |

**`swing_model` block** (additive, optional — present when the artifact loaded and the
symbol scored; absent → the page shows the legacy verdict). Produced by
`services/trade_svc/swing_model.py:score_symbol`:

| Field | Meaning |
|-------|---------|
| `verdict` | `BUY` (top calibration band) / `SELL` (bottom) / `HOLD`. |
| `score` | The signed-IC-weighted composite (`Σ signed_weight · clip(z, ±3)`). |
| `percentile` | Band-quantile percentile (top band of 5 → ~90th). |
| `expected_fwd` | The band's mean forward excess return over the horizon. |
| `hit_rate` | The band's beat-SPY hit-rate (`P(forward > 0)`). |
| `horizon_days` | The label horizon (20). |
| `contributions[]` | Per factor `{factor, z, weight, contribution, ic}`, sorted by \|contribution\|. |
| `model_version` | The artifact's `version` (fit date). |
| `oos_ic` | The artifact's walk-forward out-of-sample IC. |
| `source` | `"validated"`. |

**Offline artifact (not a service view).** `trade-analyzer/data/swing_model.json`
(`repo_paths.SWING_MODEL`, gitignored) is fit **offline** by
`trade-analyzer/fit_swing_model.py` (run manually/periodically — never imported by a
service) using the pure `src/analysis/factors.py` + `src/analysis/backtest.py`. It
stores, per regime key (`"all"`), the signed `weights`, per-factor `factor_ic`
(`mean_ic`/`icir`/`n_days`), the cross-sectional `norm` (`{factor: {mean, std}}` — the
live scorer's primary z-score basis), the `calibration` bands
(`{band, score_lo, score_hi, mean_fwd, hit_rate, n}`), and `oos_ic`/`oos_ic_by_fold`/
`n_folds`. A markdown research report is written alongside (`SWING_MODEL_REPORT`).
Re-running the fit (e.g. after a regime shift) is the supported maintenance path.

## Driver service — :8214

**Entry:** `services/driver_svc/app.py`. **Scheduler:** polls the run gate every
30 s; fires a checkpoint at 09:28 ET and then every 30 minutes inside the entry
window **09:45–15:30 ET**.

> **The order-approval queue was removed in July 2026.** `ApprovalState`,
> `PerfReport`, `cache:driver:approvals`, `cache:driver:performance` and the
> `approve` / `skip` commands no longer exist. The service is now an autonomous
> decision layer whose output is a **command enqueued on `cmd:options`**, and the
> page is a monitor with a kill switch.

**The cycle.** `build_packet` → `decider.decide` (Claude, forced tool call) →
`guardrails.apply_guardrails` → `driver_paper_create` on `cmd:options`.

`apply_guardrails` is **pure code** and is the reason the design is defensible: it
clamps position size and halts on the banked daily target, the daily loss cap, or a
VIX threshold. **The model never sizes its own risk.** Risk is evaluated in
*per-contract* dollars (`CONTRACT_MULTIPLIER = 100`) — an earlier version compared
the scanner's per-share `max_loss` against a per-contract cap, a 100× mismatch that
silently rejected every index trade.

**Key settings** (`services/driver_svc/settings.py`):

| Constant | Value | Meaning |
|---|---|---|
| `DAILY_TARGET` | `500.0` | Base bank-the-day threshold ($ net day P&L). |
| `TARGET_CAP` | `1000.0` | Maximum ratcheted daily target (2× base) when behind the MTD pace. |
| `TARGET_FLOOR` | `250.0` | Minimum daily target when ahead of the MTD pace. |
| `DAILY_LOSS_HALT` | `1500.0` | Daily loss that halts new entries. |

**Commands (`cmd:driver`):**

| Type | Args | Effect |
|------|------|--------|
| `cycle` | — | Run one decision checkpoint now (packet → decide → guardrails → enqueue). |
| `enable` | — | Set `cache:driver:control.enabled = true`. |
| `disable` | — | Clear the enable flag; the scheduler stops opening new positions. |
| `stop` | — | Latch `halted` for the rest of the day (`halted_date` re-arms it next session). Management and exits continue. |

> **The arm state lives in Redis, not in the process.** Disabling the scheduler
> alone would not stop a restored snapshot that carried `cache:driver:control`
> enabled, which is why `run_autonomous_cycle` also checks the environment's
> `autonomous_trading` flag directly.

---

## Market service — :8215

**Entry:** `services/market_svc/app.py`. Publishes the macro-ticker board that backs
`/market` and the summary the bottom ticker leads with.

**Scheduler cadence** (`services/market_svc/scheduler.py`):

| Constant | Value | When |
|---|---|---|
| `RTH_INTERVAL_SEC` | `3` | Regular trading hours. |
| `OFFHOURS_INTERVAL_SEC` | `15` | Outside RTH — futures trade nearly around the clock, so the board stays live. |
| `WEEKEND_INTERVAL_SEC` | `60` | Saturday and Sunday before 17:00 CT, when futures are closed. |
| `SUMMARY_RTH_SEC` | `40 * 60` | Claude verdict refresh during RTH. |
| `SUMMARY_OFFHOURS_SEC` | `60 * 60` | Claude verdict refresh off-hours. |

Each tick polls the proxy's raw `/quotes`, normalizes `change` across INDEX / EQUITY
/ FUTURE instrument types, computes the `$ADVN-$DECN` breadth spread and the
`BIG10` basket, reads the cap-weighted put/call from `cache:sentiment:composite` and
the dollar-weighted premium skew from `cache:options:matrix`, and publishes
`cache:market:dashboard`.

The Claude summary runs as a **background task** rather than inline, so a slow
completion cannot stall the poll loop.

**Commands (`cmd:market`):**

| Type | Args | Effect |
|------|------|--------|
| `enable_summary` | — | Turn the Claude narrative on (`cache:market:summary_enabled`). |
| `disable_summary` | — | Turn it off. This stops the API calls, not just the display. |

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
cache:sentiment:composite          events:sentiment:composite
cache:sentiment:history            (no event)
cache:sentiment:intraday_history   events:sentiment:intraday_history
cache:sentiment:sectors            events:sentiment:sectors
cache:sentiment:rotation           events:sentiment:rotation
cache:sentiment:regime             events:sentiment:regime
cache:sentiment:regime_history     events:sentiment:regime_history
cache:sentiment:momentum           events:sentiment:momentum
cache:sentiment:order_flow         events:sentiment:order_flow
cmd:sentiment
```

**Options:**

```
cache:options:scan             events:options:scan          (ScanResult contract)
cache:options:scan_day         events:options:scan_day      (the DAY UNION the Scanner renders)
cache:options:matrix           events:options:matrix        (Opportunity Board)
cache:options:flow_alerts      events:options:flow_alerts   (Flow Alerts, today only)
cache:options:flow_alert_cooldowns  (uncapped seen-map behind the per-symbol counts)
cache:options:flow_skew        events:options:flow_skew
cache:options:net_premium      events:options:net_premium   (Net Prem subtab, 28 symbols)
cache:options:header           events:options:header
cache:options:swing            events:options:swing
cache:options:paper_account    events:options:paper_account
cache:options:paper_trades     events:options:paper_trades
cache:options:paper_analyze    events:options:paper_analyze
cache:options:captured         events:options:captured
cache:options:captured_flags   events:options:captured_flags
cache:options:gamma            events:options:gamma
cache:options:gamma_hist_gex | _charm | _dex | _vanna   (per-view intraday history)
cache:options:gamma_explain    events:options:gamma_explain
cache:options:gamma_analyze    events:options:gamma_analyze
cache:options:gamma_symbols    events:options:gamma_symbols
cache:options:gamma_history    events:options:gamma_history
cache:options:gamma_briefings  events:options:gamma_briefings
cache:options:gamma_analyze_premarket | _midday | _close    (per-slot auto briefings)
cache:options:gamma_regime_state
cache:options:market_snapshot  events:options:market_snapshot
cache:options:em_chain         events:options:em_chain      (Expected Move ladders)
cache:options:calc_iv          events:options:calc_iv
cache:options:driver_paper_account    events:options:driver_paper_account
cache:options:driver_paper_perf       events:options:driver_paper_perf
cache:options:driver_paper_analytics  events:options:driver_paper_analytics
cache:options:paper_analytics  events:options:paper_analytics
cache:options:captured_closed  events:options:captured_closed
cache:options:action_alert     events:options:action_alert
cache:options:eod_summary      events:options:eod_summary
cache:options:autoclose_enabled           (Settings toggle)
cache:options:manual_paper_lifecycle      (Settings toggle)
cache:options:notified_scan | :notified_captured    (alert de-duplication)
cache:options:eth_eligible
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

**Trade / Portfolio / Driver / Market:**

```
cache:trade:analysis           events:trade:analysis          (TradeAnalysis)
cache:trade:deepdive           events:trade:deepdive
cache:trade:deepdive_query     events:trade:deepdive_query
cache:trade:markov_prior       cache:trade:universe_factors
cache:portfolio:positions      events:portfolio:positions     (PortfolioModel)
cache:driver:control           events:driver:control          (DriverControl)
cache:driver:autonomous        events:driver:autonomous       (AutonomousState)
cache:market:dashboard         events:market:dashboard        (MarketDashboard)
cache:market:summary           events:market:summary          (MarketSummary)
cache:market:summary_enabled                                  (ticker/Claude toggle)
cmd:trade   cmd:portfolio   cmd:driver   cmd:market
```

> **`cache:driver:approvals` and `cache:driver:performance` no longer exist** — they
> belonged to the order-approval queue removed in July 2026. The driver's realized
> performance now lives in its isolated paper book under
> `cache:options:driver_paper_account` and `:driver_paper_perf`, published by
> `options_svc` rather than `driver_svc`.

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
| Redis | 6379 | `MEMURAI_PORT` / `MEMURAI_URL` |
| sentiment_svc | 8210 | `SERVICE_PORTS["sentiment"]` / `SERVICE_URLS["sentiment"]` |
| options_svc | 8211 | `SERVICE_PORTS["options"]` |
| portfolio_svc | 8212 | `SERVICE_PORTS["portfolio"]` |
| trade_svc | 8213 | `SERVICE_PORTS["trade"]` |
| driver_svc | 8214 | `SERVICE_PORTS["driver"]` |
| market_svc | 8215 | `SERVICE_PORTS["market"]` |
| webgui (NiceGUI) | 8500 | `NICEGUI_PORT` / `NICEGUI_URL` |

> The `dashboard_frontend = 5173` entry in `config/ports.toml` belongs to the retired
> React frontend and is **not** used by this app. The web GUI is on **8500**.

## Environments — the ports above are the *prod* profile

Two checkouts of this repo run simultaneously on one machine. `repo_paths.py`
resolves the identity and every port consumer follows it with no edit of its own:

| | prod | dev |
|---|---|---|
| `[services]` ports | 8210–8215 | **9210–9215** (`port_offset`) |
| webgui | 8500 | **9500** |
| Redis | Redis db **0** | Redis db **1** |
| schwab-proxy | **owns** it on 8100 | **borrows** prod's — starts none |

Identity comes from `config/env.local.toml` (**gitignored**, so `git pull` can never
carry it between checkouts); a missing marker resolves to **prod**, which is why the
table above is the default. Exports: `ENV_NAME`, `ENV_FLAGS`, `IS_DEV`,
`OWNS_PROXY`, `REDIS_DB`, `PEER_ROOT`.

> **`[services]` ports are offset automatically; a top-level port is not.** That is
> correct for a process this repo does not start, and a bug for one it does.

> **Under pytest the process presents as PROD** regardless of the marker — ports,
> Redis DB, `owns_proxy` and `ENV_NAME` — with all four behaviour suppressions
> forced on. Consequence for anyone writing tests: a dev-only branch is only ever
> reached by monkeypatch. Patch a flag with
> `monkeypatch.setitem(repo_paths.ENV_FLAGS, …)`, but patch a by-value export like
> `IS_DEV` with `monkeypatch.setattr` **on the module that consumed it**.

**Key paths:**

| Path | Holds | Status |
|------|-------|--------|
| `shared/appsettings.json` | Schwab API keys | gitignored (template `*.example.json`) |
| `shared/tokens.json` | Schwab OAuth tokens | gitignored |
| `schwab-proxy/proxy_tokens.json` | Proxy runtime tokens | gitignored |
| `shared/sentiment_bridge.json` | Sentiment bridge (legacy shim) | gitignored |
| `repo_paths.REPO_ROOT` | Repo root | — |
