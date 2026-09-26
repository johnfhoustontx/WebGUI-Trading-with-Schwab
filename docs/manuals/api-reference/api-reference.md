[TOC]

# About this document

This is the **integration reference** for the NeuralStrike 3-tier
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
| **Symbol** | `options_svc` (+ `sentiment_svc` for context) | `cache:options:matrix`, `:scan_funnel`, `:scan_day`, `:gex_status`, `:flow_alerts`, the four paper books, `cache:sentiment:regime`, `:bullbear`, `cache:news:feed`, `:sec`; `cache:options:dossier:<SYMBOL>` via the `dossier` command |
| **Dealer Positioning** | `options_svc` :8211 | `cache:options:gamma`, `:gamma_hist_*`, `:gamma_symbols`, `:net_premium`, `:gamma_analyze*`, `:gamma_briefings` |
| **Opportunity Board** | `options_svc` | `cache:options:matrix` |
| **Flow Alerts** | `options_svc` | `cache:options:flow_alerts` |
| **Market News** | `news_svc` :8216 | `cache:news:feed`, `:sec`, `:calendar`, `:status` (the public copy reads `:feed_public`, `:sec_public`, `:calendar_public`); also the Desk's headlines strip (`:feed`) and the Symbol page's news band (`:feed` + `:sec`) |
| **Market Dashboard** | `market_svc` :8215 | `cache:market:dashboard`, `:summary` |
| **Sentiment** | `sentiment_svc` :8210 | `cache:sentiment:composite`, `:regime`, `:regime_history`, `:intraday_history` |
| **Sector & Industry** | `sentiment_svc` | `cache:sentiment:sectors` |
| **Sector Rotation** · **RRG** | `sentiment_svc` | `cache:sentiment:rotation` |
| **Momentum** | `sentiment_svc` | `cache:sentiment:momentum` |
| **Calculator** | `options_svc` | `cache:options:calc_chain`, `:calc_result`, `:calc_iv`, `:calc_rating` |
| **Simulator** | `options_svc` | `cache:options:sim_meta`, `:sim_chain`, `:sim_result`, `:sim_replay` |
| **Market Scanner** | `options_svc` | `cache:options:scan_day` (rendered), `:scan` (live counts) |
| **Strategy Finder** | `options_svc` | `cache:options:swing` |
| **Expected Move** | `options_svc` | `cache:options:em_chain`, `:expected_move` |
| **Captured Signals** | `options_svc` | `cache:options:captured`, `:captured_flags`, `:captured_closed` |
| **Paper Ledger** | `options_svc` | `cache:options:paper_trades`, `:paper_analyze`, `:paper_create` (the Paper button's answer, read by Market Scanner and Strategy Finder) |
| **Paper Account** | `options_svc` | `cache:options:paper_account`, `:paper_analytics` |
| **Rescue** | `options_svc` | `cache:options:rescue:<position_id>`, `:rescue_summary` |
| **Trade Analyzer** | `trade_svc` :8213 | `cache:trade:analysis`, `:deepdive`, `:deepdive_query` |
| **Portfolio** | `portfolio_svc` :8212 | `cache:portfolio:positions` |
| **EOD Report** | none — pure Tier-1 reader | aggregates the `options:*` keys |
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

- The GUI imports `nicegui` and `shared.bus` (never `redis` directly), plus a short
  allow-list: `shared.market_calendar`, `shared.symbols`, `shared.calibration`
  (only `bucket_key`), `repo_paths`, `requests` (only for the `/health` fan-out),
  `fastapi.responses` (report routes) and a lazy `edge_tts` (spoken alerts). It
  does **not** import `shared.contracts` — contracts are validated service-side on
  write. It never imports an engine, never calls Schwab, and never computes domain
  results.
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
| `MarketDashboard` | `market.py` | `cache:market:dashboard` | `categories[]` (ordered frames of display-ready tiles), `proxy_up`, `errors[]` |
| `MarketSummary` | `market.py` | `cache:market:summary` | `headline` (the latest published market report's verdict title), `highlights` (its section headlines in report order, at most 5; the headline alone when the report has no sections), `slot` (`premarket` / `open` / `first_hour` / `midday` / `close`), `slot_label` (the report's own name for the slot, e.g. "Market close"), `report_date` (`YYYY-MM-DD`), `as_of` (the report's own time stamp, e.g. "16:20 CT"), `report_url` (`https://<SITE_HOST>/report.html`). Empty until a report has been published. |
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
08:00–15:15 CT), GEX collection (1-min slots, 08:00–15:20 CT; from 06:30 for
ETH-eligible symbols), Paper Portfolio entry + manage (hourly at the top of the
hour, 09:00–14:00 CT, no 15:00 run — also refreshes the Paper Ledger), captured-signal auto-manage (5-min
slots, 08:00–15:15 CT, when auto-close is on), header tick (each 30 s,
skip-unchanged).

**Commands (`cmd:options`):**

| Type | Args | Published view |
|------|------|----------------|
| `rescan` | — | `cache:options:scan` |
| `swing_scan` | `{symbol, dte_min, dte_max, put_d_min, put_d_max, call_d_min, call_d_max, min_cr_fraction, expiry_choice?}` — `dte_max: null` is **no upper limit** (every listed expiry). A MISSING key takes the handler default, each on its own, and the defaults are the Strategy Finder's untouched scan: `dte_min` 0, `dte_max` null (no limit, so a large chain answers with the choices), the Balanced bands ±0.10–0.20, `min_cr_fraction` 0.10, every group. The page always sends every key but `families`. Every listed expiry in the range is built, a report is flagged rather than dropped, and the best 25 rows of each `type` are kept. **A large range asks first:** more than 30 listed expirations inside `dte_min`..`dte_max` (counted on Schwab's `daysToExpiration`) and no `expiry_choice`, and the answer is the choices — no chain is fetched. `expiry_choice` is one of `next_30` (DTE ≤ 30) · `next_90` (DTE ≤ 90) · `monthly` (Schwab `expirationType` `S` only) · `all`, and builds only those expirations; it is **ignored** on a range of 30 or fewer (the whole range is scanned); an unknown key is refused and answers with `error` set. The Income Window never asks. Measured live, whole chain: NVDA 13.5 s, SPY 26–27 s, `$SPX` 40.1 s | `cache:options:swing` — `{signals[], view, filtered_out, vol_filtered, not_shown, spot, chain_missing, no_expiries_in_range, expiries_failed, needs_choice, expiration_count, expirations_scanned, choices, expiry_choice, symbol, params, error?}`. Published for **every** request: a scan that raises still answers, with `signals: []` and `error` = the exception's class name (test it for truthiness). `spot` is `null` when no price was read, never 0; `expiries_failed` is `null` when the expiration list was unavailable (not counted, which is not zero), and on a choice counts only the **chosen** expirations that failed. `needs_choice: true` marks the chooser answer (`signals: []`, `expiries_failed` 0). `expiration_count` is the in-range count (`null` with no expiration list). `choices` (a large range only, else `null`) is `[{key, label, count, est_seconds}]` in the order above, `est_seconds` = `count × 0.75` rounded half up; a count of 0 is still listed. `expiry_choice` is the key applied and `expirations_scanned` how many it kept — both `null` unless a choice was applied. The IV analysis and expected move stay the whole chain's under any choice. Each row carries `earnings_status`, and a row open through a report adds `spans_earnings: true` + `earnings_date` |
| `refresh_paper` | — | `cache:options:paper_account` |
| `paper_entry` | — | `cache:options:paper_account` |
| `paper_manage` | — | `cache:options:paper_account` |
| `paper_reset` | `{starting_balance}` | `cache:options:paper_account` |
| `paper_create` | `{signal, qty}` — opens into the Paper **Ledger** only if every risk cap in `shared.book_caps` clears against the Ledger's own open trades ($750 per trade, plus the Account's symbol / sector / expiry / deployment caps); a refusal writes nothing | `cache:options:paper_trades` + `cache:options:paper_create` — the outcome, written on **every** answer with a 600 s TTL: `{status, symbol, type, expiration, qty, rungs[], seq, ts}` plus, by `status`: `opened` → `trade_id`; `refused` → `code` (the binding rung), `message` (a plain sentence), `max_quantity` (the largest quantity that clears every cap now; `0` when none does); `error` / `stale` → `message`. `status` is one of `opened` · `refused` · `stale` · `error`. `seq` rises on each publish, so two identical refusals are two answers; the `:ver` counter can reset to 1 when the TTL expires, so compare versions with `!=`. Each rung is `{code, kind, scope, used, after, cap, binds, skipped}` — `kind` `count` or `risk`, a skipped rung has `used`/`after`/`cap` `null` and `binds: false`; `rungs` is `[]` on `stale` and on errors raised before the caps ran. `qty` is `null` on `stale`, the as-sent value on a bad quantity, and absent when the command raised. A command that **raises** publishes an `error` (`message` *The paper ledger could not process the request.*) and is then dead-lettered |
| `paper_reload` | — | `cache:options:paper_trades` |
| `paper_close` | `{trade_id, debit}` | `cache:options:paper_trades` |
| `paper_delete` | `{trade_id}` | `cache:options:paper_trades` |
| `paper_delete_closed` | — | `cache:options:paper_trades` |
| `paper_analyze` | `{trade_id}` | `cache:options:paper_analyze` |
| `x_post` | `{text, tags[], link, image_b64?}` — an ad-hoc marketing post from the `/x` page. The image is refused over 5 MB or when it does not decode (logged, nothing posted). Replay-guarded | `cache:options:x_log` |
| `x_post_report` | `{report, mtime}` — enqueued by market_svc when the published market report changes; `report` is the `cache:market:summary` payload, `mtime` the epoch seconds of `latest.html`. Posted once per report (`report_date`, `slot`, `as_of`, `headline`); a report older than `x.report_max_age_min` (45), or of unknown age, is skipped. Replay-guarded | `cache:options:x_log` + `cache:options:x_reports` |
| `captured_reload` | — | `cache:options:captured` |
| `captured_reprice` | — | `cache:options:captured` + `cache:options:captured_flags` |
| `captured_close` | `{signal_id, exit_val, reason}` | `cache:options:captured` |
| `gamma_refresh` | `{symbol}` | `cache:options:gamma` + `cache:options:gamma_hist_{view}` |
| `gamma_explain` | `{symbol}` | `cache:options:gamma_explain` |
| `gamma_analyze` | — | `cache:options:gamma_analyze` |
| `sim_fetch` | `{symbol, lazy?, expiries?}` — `lazy` lists every expiration and fetches contracts for the nearest two plus `expiries` | `cache:options:sim_chain` (the thinned chain from the same fetch — written first), then `cache:options:sim_meta` (a lazy fetch adds `expirations`: every listed expiry) |
| `sim_fetch_expiry` | `{symbol, expiry}` | adds one expiry to the stashed snapshot; merges its chain into `cache:options:sim_chain`, then `cache:options:sim_meta` with `added`. A symbol with no snapshot or an unlisted expiry writes nothing |
| `sim_run` | `{symbol, legs[], dt, mult}` (legs: `{kind, strike, expiry, side, qty}`; legacy `{expiry, kind, strike, direction}` single-leg args still accepted) | `cache:options:sim_result` — `{spot, symbol, legs, dt, mult, whatif_rows, whatif_baseline, ivshock: {base, shock, units: "position"}}`; the four inputs are echoed so a reader can match a result to what it asked for |
| `sim_replay` | `{symbol, legs[], lookback}` (same multi-leg shape; legacy single-leg args still accepted) | `cache:options:sim_replay` — adds `value` + `pnl` per bar and `units: "position"` (Greeks × 100 × qty) |
| `calc_load` | `{symbol, lazy?, expiries?}` — `lazy` (the Calculator) lists every expiration via Schwab `/expirationchain` and fetches strikes for the nearest two plus `expiries`; without it (Rescue) the fixed today..+60-day fetch | `cache:options:calc_chain` (a lazy load adds `expirations`) |
| `calc_load_expiry` | `{symbol, expiry}` | merges one expiry's strikes into `cache:options:calc_chain`, marked `added` (and `failed` if Schwab returned nothing). A click for another symbol or an unlisted expiry writes nothing |
| `calc_compute` | `{strategy, spot, iv, rate, ivadj, qty, expiry, legs[], range_*}` (each leg carries its own `expiry`/`qty`; `strategy="CUSTOM"` or any non-PCS/CCS/IC/single code → generic numeric summary) | `cache:options:calc_result` |
| `calc_rate` | `{request_id, symbol, structure, legs[]}` — `legs` in the Calculator's shape (`option_type`, `side`, `strike`, `expiry`, `qty`, `premium`); `structure` a Calculator template code or `"CUSTOM"`. Grades against `cache:options:calc_chain` (no chain fetch) with the Strategy Finder's `score_all` and `stamp_candidate`, without the quality cut or the volatility drop. Replay-guarded | `cache:options:calc_rating` — `{request_id, symbol, legs, row, error}`: `row` is a Strategy Finder candidate plus `grade`, `composite_score`, the checklist stamps, `vol_gate_blocks` and `structure_known`; `error` is a sentence when `row` is None (no chain, another symbol's chain, a contract the chain lacks, a failure). Always written, so a request is never left unanswered |
| `expected_move` | `{symbol, expiry, legs[], lookback}` | `cache:options:expected_move` |
| `dossier` | `{symbol}` — cleaned by `shared.symbols.clean_symbol` (upper-cased, `[A-Z$][A-Z0-9$.]{0,7}`); anything it refuses is logged and writes nothing, because the symbol becomes part of the key name. Replay-guarded (a command older than 180 s is dropped). **Deduplicated** (`DOSSIER_DEDUP_SEC` = 60): if that symbol's dossier was written less than 60 s ago — measured on the envelope's own `ts`, the write time — the command is dropped and nothing is fetched or re-published. A recent success or `no_quote` blocks the fetch; a recent `fetch_failed` does **not** (a retry costs at most one quote call). An unreadable envelope counts as no recent dossier. Otherwise one fetch of **4 Schwab calls, 5 at most** (quote · GEX chain today..+7 d · 1-year daily price history · IV chain +20..+45 d · the IV analysis' own today..+60 d fallback when that window is empty); a `no_quote` or `fetch_failed` answer spends one | **`cache:options:dossier:<SYMBOL>`** (event `events:options:dossier:<SYMBOL>`), **TTL 900 s**, per symbol so two tabs never share a slot — `{symbol, error, fetched_at, spot, day_pct, flip, put_wall, call_wall, net_gex, iv_rank, current_iv, hv_current, earnings_status, earnings_date}`. Every key is always present; an absent reading is `null`, never 0. `fetched_at` is naive Central ISO. `current_iv` / `hv_current` are **percents** (48.5 = 48.5%); `iv_rank` is the scan's Vol Rank; `day_pct` is always `null` (no day-change parser is shared for a raw quote). `earnings_status` is three-valued: `upcoming` · `none_scheduled` · `not_listed` (the calendar has no data — not "no report"). Walls are assigned by side of spot, and both are `null` when `net_gex` is exactly `0.0` (the after-hours all-zero grid, whose walls would be an argmax tie-break); `flip` is kept either way. `error` is `null` on success, `"no_quote"` when Schwab **answered** and quoted nothing usable for the symbol (a typo — the other legs are skipped), or `"fetch_failed"` when the quote request itself failed (non-200 or raised: proxy down, timeout, token) — the ticker may be fine. One of the three later legs failing blanks only its own keys and records a degrade (`options.dossier_gex` · `_vol` · `_earnings`) |
| `rescue` | `{position_id}` | `cache:options:rescue:<position_id>` |
| `rescue_apply` | `{position_id, candidate}` | `cache:options:rescue:<position_id>` |

**Scheduled (not command-driven):** `rescan` (auto-scan window), `refresh_header`
(per tick, skip-unchanged), `collect_gex_snapshots` + `publish_gex_status` +
`publish_gamma_symbols` (GEX window), `run_manage_and_refresh` (paper-manage window).
The paper-manage cycle also overlays `rescue_state` / `heat` onto
`cache:options:paper_account` and publishes `cache:options:rescue_summary` (tested +
critical counts) for the nav badge.

**Candidate stamps.** Every candidate row on `cache:options:scan` (and so
`scan_day`), `cache:options:swing` and `cache:options:income` is stamped at publish
time by `compute.stamp_candidate` — from `rescan` (the 0-DTE, swing and Directional
lists), `swing_scan` and the income scan. Stamping is best-effort **per row**: a row
whose stamping raises may be left without some stamps, the rows after it are still
stamped, and the pass records one degrade (`options.stamp_scan` · `options.stamp_swing` ·
`options.stamp_income`). On a scan, a symbol whose earnings lookup fails also records
`options.stamp_scan_earnings` (once per symbol) and is stamped `not_listed` with no
date. Every stamp is `null` when unknown, never a guessed zero.

| Field | Meaning |
|---|---|
| `ledger_risk_basis` | The unrounded figure the Paper Ledger books risk from: `{"per_share": x}` (credit structures) or `{"per_contract": y}` (debit structures). `shared.book_caps.booked_risk(basis, qty)` is exactly the `max_loss_total` the Ledger books for `qty` contracts. `null` when the Ledger would refuse the structure or book no positive risk |
| `ledger_risk_per_contract` | `booked_risk(basis, 1)` — the booked risk of one contract, for display. ⚠ Do not multiply it by a quantity: it is cent-rounded, and a sub-cent per-share figure books differently (use the basis) |
| `friction_pct` | The round-trip bid-ask width as a percent of the per-share credit or debit. `null` for any row holding a share leg, and when a leg's quote is missing or crossed |
| `em_to_expiry` | The daily expected move × √max(DTE, 1), in dollars |
| `vol_floor` | The IV-rank floor for the row's trade type (`config/scanner.toml`); a Directional row takes its DTE window's — DTE 0–4 the 0-DTE floor, later the swing floor |
| `iv_rank_known` | Whether the row had an IV rank to gate on |
| `earnings_status` · `earnings_date` | The earnings coverage and next report date. Stamped on scanner and Strategy Finder rows; an income row already carries its own |

**`cache:options:scan_day`** — the day union the Market Scanner and the Symbol
Dossier render: `{date, scan_seq, signals_0dte[], signals_swing[],
signals_directional[], setups, truncated?}`. `date` is the **Central** trading date —
check it before trusting any row's `live`, because a failed first merge of a new day
leaves yesterday's envelope in place. `scan_seq` is this scan's 1-based number within
the day. Each row is the scan's row plus `live`, `stale_since` (when it dropped out;
`null` while live) and **`setup_key`** — `SYMBOL|TYPE|EXPIRATION`, strikes excluded
(the row's front expiration — its `legs` only when the top-level field is
unreadable), or `null` when a part is
missing. ⚠ `setup_key` is a **lookup into `setups`, never a row key**: row identity
stays `id`, and two adjacent strikes on one expiry are two rows sharing one entry.
`setups` is `{setup_key: {seen, scores[], gaps, last_seq, last_live, first_seen |
age_unknown}}` — `seen` the scans it was live in, `scores` its best composite per
scan (the last 40), `gaps` how many times it went absent and came back. A setup whose
start was not observed (the map rebuilt mid-session) carries `age_unknown: true` and
**no** `first_seen`; nothing is ever stamped with the current time to fill the gap.
`setups` is always present; an empty map on a scan whose persistence step failed
(degrade `options.merge_setups`) means "no reading", never "all new". `truncated`
(`{list: n_dropped}`) appears only when the 2000-per-list cap evicted stale rows; the
map is trimmed after the rows and never loses an entry a surviving row references.

**`cache:options:scan_funnel`** — `{timestamp, symbols: {SYMBOL: account}}`, the
per-symbol account of why a symbol did or did not produce a signal. Each account is
`{price, iv_rank, hv_current, current_iv, earnings_date, stop, buckets}`:
`hv_current` is 30-day realised volatility and `current_iv` the ATM implied
volatility, both **percents**, `null` when the IV analysis did not measure them;
`stop` is `null`, `"no_quote"` or `"no_data"`; `buckets` is keyed `0DTE` / `SWING` /
`DIRECTIONAL`. Written after every scan with `skip_unchanged`.

**`cache:options:ledger_caps`** (event `events:options:ledger_caps`) — the Paper
Ledger's book, as the Paper dialog's preview reads it:
`{limits, starting_balance, realized_pnl, equity, open[], sectors, unmapped_prefix}`.
`limits` is the Ledger's rung map (`max_risk_per_trade`, `max_deployed_risk_pct`,
`max_positions_per_symbol`, `max_risk_per_symbol`, `max_positions_per_sector`,
`max_risk_per_sector`, `max_positions_per_expiry`); `equity` is `starting_balance` +
`realized_pnl` of closed trades; each `open` row is
`{symbol, expiration, max_loss_total, sector}`; `sectors` is the **whole**
`config/sectors.toml` symbol → sector table, so a reader buckets any symbol with
`shared.book_caps.sector_bucket(sectors, symbol)` (an unmapped symbol is its own
`unmapped_prefix` + symbol bucket; `unmapped_prefix` is informational). Written by
`handlers.refresh_ledger_caps` with `skip_unchanged` (an unchanged book bumps no
version and fires no event), under a lock, at the end of every `refresh_paper_trades`
— after every Ledger-changing command (`paper_create`, `paper_reload`, `paper_close`,
`paper_delete`, `paper_delete_closed`; not `paper_analyze`, which publishes only
`cache:options:paper_analyze`) and the paper-manage cycle — even when that view's
own publish raises, and once at service start. A failure is a degrade
(`options.ledger_caps`), never a raise. No TTL.

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

**Entry:** `services/trade_svc/app.py`. **Scheduler:** `services/trade_svc/scheduler.py`,
one job — the watchlist dividend pull (`dividends.refresh`), once a trading day at or
after `[calendar.dividends] refresh_at` (06:40 CT) in `config/news.toml`, one proxy
`/quotes` passthrough call per followed symbol, into `services/trade_svc/data/dividends.db`
(`shared/dividends.py`). Gated by the environment's `schedulers` flag.

**Commands (`cmd:trade`):**

| Type | Args | Effect |
|------|------|--------|
| `analyze` | `{symbol}` | MTF technical + fundamental analysis, plus the **validated swing model** verdict (Position), the **Markov 2.0** forecast (5-band composite-score chain → band-probability forecast + bounded drift tilt), and the Investor verdict → `cache:trade:analysis`. |
| `dividends_refresh` | none | Runs the dividend pull now, past the once-a-day guard (~one proxy call per followed symbol). Writes the store only — `news_svc` picks it up on its next calendar pass. Ignored while `[calendar.dividends] enabled = false`; a command older than **180 s** (`DIVIDENDS_REFRESH_MAX_AGE_SEC`) is dropped as a replay. |

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

## Market service — :8215

**Entry:** `services/market_svc/app.py`. Publishes the macro-ticker board that backs
`/market` and the market summary that feeds both the bottom ticker and the Desk's
MARKET SUMMARY frame.

**Scheduler cadence** (`services/market_svc/scheduler.py`):

| Constant | Value | When |
|---|---|---|
| `RTH_INTERVAL_SEC` | `3` | Regular trading hours. |
| `OFFHOURS_INTERVAL_SEC` | `15` | Outside RTH — futures trade nearly around the clock, so the board stays live. |
| `WEEKEND_INTERVAL_SEC` | `60` | Saturday and Sunday before 17:00 CT, when futures are closed. |

Each tick polls the proxy's raw `/quotes`, normalizes `change` across INDEX / EQUITY
/ FUTURE instrument types, computes the `$ADVN-$DECN` breadth spread and the
`BIG10` basket, reads the cap-weighted put/call from `cache:sentiment:composite` and
the dollar-weighted premium skew from `cache:options:matrix`, and publishes
`cache:market:dashboard`.

**The summary is read off the published market report, with no Claude call**
(2026-09-16). The daily market reports are rendered outside this repo and uploaded
five times a trading day into `deploy/site/reports/` as `latest.html` (the page)
and `latest.txt` (`"<day> <n> <slot> <as_of>"`). On the same poll loop,
`scheduler.refresh_summary(bus, last_stamp)` stats those two files
(`report_summary.report_stamp` — modification time and size) and, only when the
stamp has changed, parses the page (`report_summary.read_report`): the
`div.slotchip` text ("<label> · <as_of>"), the `h1` verdict headline, and each
section's `h2` headline. Highlights are the section headlines in report order,
capped at `MAX_HIGHLIGHTS` (`5`), falling back to the headline when the report has
no sections. The payload is validated against `MarketSummary` and written with
`skip_unchanged=True`. A report that does not parse (no `h1`) publishes nothing —
the last good summary stays — and its stamp is still remembered, so the same
bytes are not re-read until the report is replaced. A stat per poll is the whole
steady-state cost; the service no longer uses `ANTHROPIC_API_KEY` or the
environment's `allow_claude` flag.

**Commands (`cmd:market`):** none. `handle_command` dispatches nothing and ignores
every command type, including a replayed `enable_summary` / `disable_summary` from
an older webgui — those, and the `cache:market:summary_enabled` key they wrote,
were retired 2026-09-10. The ticker toggle only hides the marquee.

## News service — :8216

**Entry:** `services/news_svc/app.py` (`make_app("news", scheduler=scheduler.loop,
command_handler=handlers.handle_command)`). Polls free public feeds —
`config/news.toml [[feeds]]`, read through `shared/news_config.py` — and the economic
calendar's sources into `services/news_svc/data/news.db`, and publishes eight views.
**No Schwab call, no Claude call, no proxy**; the dividends come from a store
`trade_svc` writes, opened read-only. One optional credential, `FRED_API_KEY`, read
from the process environment (the stack `.env`). Designs:
`docs/plans/2026-09-25-news-feed-design.md`, `docs/plans/2026-09-26-news-v2-design.md`.

**Scheduler** (`services/news_svc/scheduler.py`): the loop wakes every `TICK_S`
(30 s), beats the heartbeat, and launches three branches as keyed background tasks —
a branch whose previous task is still running is skipped, never doubled, and one
branch's failure never stops another:

| Branch | Runs | Cadence |
|---|---|---|
| `feeds` | `compute.poll_now` | `[collector]`: `rth_poll_min` 5 (08:30–15:00 CT on a trading day), `offhours_poll_min` 15, `weekend_poll_min` 60 (weekends, NYSE holidays); counted from when the last poll **ended**; never faster than `MIN_INTERVAL_S` (60 s) |
| `calendar` | `econ_calendar.refresh_now` | every tick; fetches only the sources whose own `refresh_min` is due, then republishes (`skip_unchanged`) |
| `watch` | `econ_calendar.watch_now` | every tick; fetches only a series whose release just passed and whose value has not landed, every `release_poll_min` (2) for `release_watch_min` (60); does nothing otherwise |

Config is re-read every pass (mtime-cached), so an edit applies without a restart.
Gated by the environment's `schedulers` flag like every other loop.

**Commands (`cmd:news`):**

| Type | Payload | Effect |
|---|---|---|
| `news_refresh` | none | The private page's Refresh: re-checks the calendar (only the sources that are due — a click never forces one early), then polls every enabled feed. Each cycle runs one at a time: while one runs, that step returns at once and is logged as skipped; a calendar failure never costs the feed poll. Its end is visible as a new `cache:news:status` publish. |

**Views:**

| Key | Payload | Written |
|---|---|---|
| `cache:news:feed` | `{"items": [item, ...]}` — the newest `view_items` (300) **headlines**: every kind but `edgar_form4` / `edgar_filings` | `skip_unchanged`; no timestamp in the payload, so read "last confirmed current" from `cache:news:feed:ts` |
| `cache:news:feed_public` | the same shape, only rows whose **primary** feed is public under the CURRENT `[feed_flags]`; `sources` and `tickers` cut to what public feeds contributed; `impact` re-scored from that row | `skip_unchanged` |
| `cache:news:sec` | `{"items": [...]}` — the newest `sec_view_items` (100) `edgar_form4` / `edgar_filings` items | `skip_unchanged` |
| `cache:news:sec_public` | the SEC items under the same public rule as `feed_public` | `skip_unchanged` |
| `cache:news:status` | `{"feeds": [{name, kind, enabled, public, last_ok, last_poll, error, inserted}], "ts": <ISO UTC>}` — one row per configured feed, `inserted` is this poll's count | every poll, even one that failed |
| `cache:news:calendar` | the calendar payload (below), dividends for every followed ticker | `skip_unchanged`; no timestamp |
| `cache:news:calendar_public` | the same, BUILT with the GEX collection list as the dividend symbol set, so `[tickers] extras` never appear | `skip_unchanged`; no timestamp |
| `cache:news:calendar_status` | `{"sources": [{name, last_ok, last_poll, error}]}` — one row per source, `error` redacted. **Private** | every calendar pass, `skip_unchanged`; no timestamp (read `cache:news:calendar_status:ts`) |

An **item** is `{id, source, sources, original_source, title, teaser, url,
published_at, first_seen, tickers, kind, topics, detail, public, impact}`: `id` a hash
of the canonical URL; `source` the feed whose title / url / teaser are stored and
`sources` every feed that carried the story (primary first); times are UTC ISO strings
(an unparseable publish time is stored as `"undated"`); `kind` is the adapter (`rss`,
`yahoo_ticker`, `google_news`, `edgar_form4`, `edgar_filings`); `topics` is structural
only (`SEC Filing`, `Insider Transaction`, `Offering`); `detail` is `{}` except for
EDGAR — a Form 4 carries `symbol, company, insider, insiders, relationship, groups,
total_value, transaction_date`, an offering `form, cik, accession`. **`impact`** is
`{"band": "high" | "med" | "low", "score": int, "reasons": [code, ...]}`, or `null`
when never scored; a High older than `[impact] stale_after_h` is published as `med`
with `"stale"` appended. Reason codes: `kw:<tier>:<word>`, `source:<feed>`,
`sources:<n>`, `watchlist`, `form4:$<total>`, `officer`, `filing:<form>`,
`untracked`. Every string in an item is third-party text: render it escaped, and never
use `url` as a link without checking it is http(s).

The **calendar payload**:

```
{"events":    [{"title", "at", "date", "high"}],
 "dividends": [{"symbol", "ex_date", "pay_date", "amount", "high"}],
 "ipos":      [{"symbol", "company", "date", "price", "price_range", "offer_usd",
                "high"}],
 "data":      [{"key", "label", "tile", "unit", "high", "next_release_at",
                "next_date", "last_release_at", "latest", "prior"}],
 "sources":   {name: "ok" | "stale" | "never" | "off"},
 "settings":  {"release_watch_min", "actual_fresh_h"}}
```

`at` / `*_release_at` / `first_seen` are aware UTC ISO instants or `null`; dates are
`YYYY-MM-DD`. `latest` / `prior` are `{obs_date, value, first_seen, bootstrap}` or
`null`, `value` the DERIVED figure per `unit` (`pct_mom`, `change_k`, `level_pct`,
`level_k`, `pct_saar`). `amount` is a positive per-payment figure or `null` (never 0).
`high` is a real bool on every row — the producer's "draw this highlighted": an event
whose title contains a `[calendar.events] high_impact` phrase (case-insensitive), a
data entry whose indicator has `high = true`; a dividend or an IPO is always `false`.
The page highlights only a real `true` (`news_view.calendar_groups`).
`sources` names `fed`, `bls`, `bea`, `dividends`, `nasdaq_ipo`, `fred_calendar`,
`fred_api`, `fredgraph` — the FRED observation path not in use reports `off`. The
payload carries FACTS only: whether an indicator is *released*, *awaiting* or
*upcoming* depends on `now`, so the reader decides it
(`webgui/pages/news_view.indicator_state`).

⚠ The public origin's Redis user reads `~cache:*`, which covers every private news key
too — `feed`, `sec`, `calendar` and `calendar_status` (which carries error text).
Keeping them off `live.neuralstrike.co` is the job of the code that chooses the key
(`pages/news_live.py`, `desk.bus_key`), not of the ACL.

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
cache:options:scan_day         events:options:scan_day      (the DAY UNION the Scanner renders, + setups)
cache:options:scan_funnel      events:options:scan_funnel   (per-symbol "why no trade?" account)
cache:options:dossier:<SYMBOL> events:options:dossier:<SYMBOL>   (TTL 900 s; the Symbol Dossier's look-up)
cache:options:matrix           events:options:matrix        (Opportunity Board)
cache:options:flow_alerts      events:options:flow_alerts   (Flow Alerts, today only)
cache:options:flow_alert_cooldowns  (uncapped seen-map behind the per-symbol counts)
cache:options:flow_skew        events:options:flow_skew
cache:options:net_premium      events:options:net_premium   (Net Prem subtab, 28 symbols)
cache:options:header           events:options:header
cache:options:swing            events:options:swing
cache:options:paper_account    events:options:paper_account
cache:options:paper_trades     events:options:paper_trades
cache:options:paper_create     events:options:paper_create   (TTL 600 s; the Paper button's answer)
cache:options:ledger_caps      events:options:ledger_caps    (the Paper Ledger's book, for the Paper dialog preview)
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
cache:options:trade_idea                                    (hourly post: last result + today's posted set)
cache:options:x_log                                         (every X post attempt, newest first, last 100)
cache:options:x_count                                       (X posts made today, CT day - the daily cap)
cache:options:x_reports                                     (market reports already posted to X)
cache:options:em_chain         events:options:em_chain      (Expected Move ladders)
cache:options:calc_iv          events:options:calc_iv
cache:options:calc_rating      events:options:calc_rating   (Rate my trade)
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
cache:options:sim_chain        events:options:sim_chain
cache:options:sim_replay       events:options:sim_replay
cache:options:calc_chain       events:options:calc_chain
cache:options:calc_result      events:options:calc_result
cache:options:gex_status       events:options:gex_status
cache:options:expected_move    events:options:expected_move
cache:options:rescue:<position_id>   events:options:rescue:<position_id>   (RescueAdvisory contract)
cache:options:rescue_summary   events:options:rescue_summary
cmd:options
```

**Trade / Portfolio / Market:**

```
cache:trade:analysis           events:trade:analysis          (TradeAnalysis)
cache:trade:deepdive           events:trade:deepdive
cache:trade:deepdive_query     events:trade:deepdive_query
cache:trade:markov_prior       cache:trade:universe_factors
cache:portfolio:positions      events:portfolio:positions     (PortfolioModel)
cache:market:dashboard         events:market:dashboard        (MarketDashboard)
cache:market:summary           events:market:summary          (MarketSummary)
cache:news:feed                events:news:feed               (headlines - every kind but the SEC ones)
cache:news:feed_public         events:news:feed_public        (public feeds only - a live-origin key)
cache:news:sec                 events:news:sec                (Form 4s and offering filings)
cache:news:sec_public          events:news:sec_public         (public feeds only - a live-origin key)
cache:news:calendar            events:news:calendar           (the economic calendar)
cache:news:calendar_public     events:news:calendar_public    (dividends cut to the collection list - a live-origin key)
cache:news:calendar_status     events:news:calendar_status    (private: per-source errors)
cache:news:status              events:news:status
cmd:trade   cmd:portfolio   cmd:market   cmd:news
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
| Redis | 6379 | `MEMURAI_PORT` / `MEMURAI_URL` |
| sentiment_svc | 8210 | `SERVICE_PORTS["sentiment"]` / `SERVICE_URLS["sentiment"]` |
| options_svc | 8211 | `SERVICE_PORTS["options"]` |
| portfolio_svc | 8212 | `SERVICE_PORTS["portfolio"]` |
| trade_svc | 8213 | `SERVICE_PORTS["trade"]` |
| market_svc | 8215 | `SERVICE_PORTS["market"]` |
| news_svc | 8216 | `SERVICE_PORTS["news"]` |
| webgui (NiceGUI) | 8500 | `NICEGUI_PORT` / `NICEGUI_URL` |
| webgui_live (public screens) | 8501 | `NICEGUI_LIVE_PORT` / `NICEGUI_LIVE_URL` |

> The web GUI is on **8500**. (The retired React frontend's `dashboard_frontend = 5173`
> entry was removed from `config/ports.toml` in September 2026.)

## Environments — the ports above are the *prod* profile

Two checkouts of this repo run simultaneously on one machine. `repo_paths.py`
resolves the identity and every port consumer follows it with no edit of its own:

| | prod | dev |
|---|---|---|
| `[services]` ports | 8210–8213, 8215, 8216 | **9210–9213, 9215, 9216** (`port_offset`) |
| webgui | 8500 | **9500** |
| webgui_live | 8501 | **9501** |
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
