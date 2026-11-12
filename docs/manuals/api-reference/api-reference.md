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
| **Flow Alerts** | `options_svc` | `cache:options:flow_alerts`, `:flow_sides`, `:flow_followup` |
| **Market News** | `news_svc` :8216 | `cache:news:feed`, `:sec`, `:calendar`, `:status` (the public copy reads `:feed_public`, `:sec_public`, `:calendar_public`); also the Desk's Headlines panel (`:feed`) and the Symbol page's news band (`:feed` + `:sec`) |
| **Market Dashboard** | `market_svc` :8215 | `cache:market:dashboard`, `:summary`, `:read` (the Desk's Market read), `:volume` (traded volume, for the Market read and the reports), `:holdings` (each fund tile's top holdings, quoted, for the hover card) |
| **Sentiment** | `sentiment_svc` :8210 | `cache:sentiment:composite`, `:regime`, `:regime_history`, `:intraday_history` |
| **Sector & Industry** | `sentiment_svc` | `cache:sentiment:sectors` |
| **Sector Rotation** · **RRG** | `sentiment_svc` | `cache:sentiment:rotation` |
| **Momentum** | `sentiment_svc` | `cache:sentiment:momentum` |
| **Calculator** | `options_svc` | `cache:options:calc_chain`, `:calc_result`, `:calc_iv`, `:calc_rating` |
| **Simulator** | `options_svc` | `cache:options:sim_meta`, `:sim_chain`, `:sim_result`, `:sim_replay` |
| **Market Scanner** | `options_svc` | `cache:options:scan_day` (rendered), `:scan` (live counts) |
| **Strategy Finder** | `options_svc` | `cache:options:swing` |
| **Expected Move** | `options_svc` | `cache:options:em_chain`, `:expected_move` |
| **Captured Signals** | `options_svc` | `cache:options:captured`, `:captured_flags`, `:captured_closed`, `:tracked` |
| **Paper Ledger** | `options_svc` | `cache:options:paper_trades`, `:paper_analyze`, `:paper_create` (the Paper button's answer, read by Market Scanner and Strategy Finder) |
| **Paper Account** | `options_svc` | `cache:options:paper_account`, `:paper_analytics` |
| **Rescue** | `options_svc` | `cache:options:rescue:<position_id>`, `:rescue_summary` |
| **Opening Scalp** | `options_svc` | `cache:options:scalp` |
| **Trade Analyzer** | `trade_svc` :8213 | `cache:trade:analysis`, `:deepdive`, `:deepdive_query` |
| **Portfolio** | `portfolio_svc` :8212 | `cache:portfolio:positions` |
| **EOD Report** | none — pure Tier-1 reader | aggregates the `options:*` keys |
| **Blog** | `blog_svc` :8217 | `cache:blog:drafts`, `:posts`, `:result`; the preview reads the service's data folder through `shared.blog_inbox` |
| **System Status** | none — probes `/health` directly | reads every domain's `:ver` / `:ts` side keys |

---

# 3-Tier Overview

{{diagram: system-overview | The processes, the three tiers and what each one talks to}}

**Rules of the model:**

- The GUI imports `nicegui` and `shared.bus` (never `redis` directly), plus a short
  allow-list: `shared.market_calendar`, `shared.symbols`, `shared.calibration`
  (only `bucket_key`), `repo_paths`, `requests` (only for the `/health` fan-out),
  `fastapi.responses` (report routes) and a lazy `edge_tts` (spoken alerts), and
  the shared request modules a page and its service must agree on, such as
  `shared.blog_inbox` (the Blog page). The complete list, with the test that pins
  each entry, is in `CLAUDE.md`. It
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
- `skip_unchanged=True` — if the payload is the one already stored, the
  whole write *and* publish are skipped (no version bump, so GUI pollers don't
  repaint). Used by tick republishers (header, GEX status). The check reads a
  third side key, `{key}:sig` (a digest of the stored payload), so the payload is
  not fetched back to be compared. A write without `skip_unchanged` deletes the
  digest; a key that has none yet is compared in full once.

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
why it failed under `dead_letter_key(stream)` (`dead:{domain}`). The list keeps the
newest `[dead_letters] keep` records (200) per stream. Never raises.

A record is `{"ts", "reason", "fields"}`. **A text field larger than
`[dead_letters] max_field_kb`** (`config/services.toml`; shipped 64, range 1 to
4096, measured in UTF-8 bytes) is cut to that size and ends
`...[truncated, N bytes total]`, and the record gains `"truncated": true`. A
command can carry a whole document (a blog draft is up to 512 KB), and 200 of
those kept whole were about 100 MB on one list. A command under the limit is
recorded exactly as before, with no `truncated` key. ⚠ A truncated record's
`data` is no longer a command anything could decode; nothing reads the list back
as commands, and a replay tool would have to check `truncated`. `reason` is cut
at 2,048 characters and ends `...[truncated, N characters total]`. The setting is
read each time a command is dead-lettered, so a change needs no restart.

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
(`{type, args}`) and `XADD`s it to the stream; returns the message id. Each write
trims the stream to its cap, `shared.service_limits.stream_keep(stream)`.

**The cap is per stream** (`config/services.toml [stream_keep]`, read on every
enqueue, so a change needs no restart):

| Key | Shipped | Range | Applies to |
|---|---|---|---|
| `default` | 1000 | 10–100000 | every stream not named below |
| `"cmd:blog"` | 50 | 10–500 | the Blog page's uploads and its Publish |
| `"cmd:futures_chain"` | 20 | 10–200 | the /ES and /NQ chain feed's snapshots: one chain per command, up to `config/futures_chain.toml` `[capture] max_snapshot_kb` (512 KB) each |

The blog stream is small because one of its commands carries a whole
document, up to `config/blog.toml [limits] max_html_kb` (512 KB): a thousand
would be about 500 MB of Redis, fifty is about 25 MB. ⚠ A new stream whose
commands carry a document needs a row of its own here before anything writes
it. `cmd:blog_inbox`, the stream of the connector that is not built, has none:
`shared/tests/test_service_limits.py` fails the day code names that stream. A value for a named
stream that is outside its range, or cannot be read, falls back to that stream's
shipped number, not to `default`.

⚠ **The cap is on the stream, not on its history.** Redis drops the oldest
entries past it whether or not a consumer has read them, with no dead letter and
no error. A service that is stopped or busy while more than N commands arrive
comes back to the newest N. The trim is approximate (`MAXLEN ~`): a stream of
small commands can run about a hundred over; a stream of documents, an entry or
two.

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
| `TradeAnalysis` | `trade.py` | `cache:trade:analysis` | `symbol`, `description`, `price`, `volume`, `bias`, `ema_alignment{}`, `momentum{}`, `volume_profile{}`, `sector{}`, `position_verdict{}`, `investor_verdict{}`, `fundamentals{}`, `fundamentals_available`, `markov{}` (optional), `swing_model{}` (optional), `classification{}` (optional: `name`, `sector`, `industry`, `sub_industry`, `industry_rank`, `industry_count`, `leaders[]` of `{rank, symbol, name, market_cap}`; `null` for a symbol the listed-stocks file does not list), `timestamp`, `errors[]` |
| `PortfolioModel` | `portfolio.py` | `cache:portfolio:positions` | `holdings_rows[]`, `sector_rows[]`, `performance_rows[]`, `suggestions{}`, `proxy_up`, `streaming`, `errors[]`, `timestamp` |
| `MarketDashboard` | `market.py` | `cache:market:dashboard` | `categories[]` (ordered frames of display-ready tiles), `proxy_up`, `errors[]` |
| `MarketSummary` | `market.py` | `cache:market:summary` | `headline` (the latest published market report's verdict title), `highlights` (its section headlines in report order, at most 5; the headline alone when the report has no sections), `slot` (`premarket` / `open` / `first_hour` / `midday` / `close`), `slot_label` (the report's own name for the slot, e.g. "Market close"), `report_date` (`YYYY-MM-DD`), `as_of` (the report's own time stamp, e.g. "16:20 CT"), `report_url` (`https://<SITE_HOST>/report.html`, the site's page for the report), `frame_url` (`https://<SITE_HOST>/reports/latest.html?v=<report_date>-<slot>`, the report itself, which the Desk frames in a dialog; the version changes with the report and is omitted when the stamp is unknown), `published_ts` (unix seconds `latest.html` was written, so a restart republishing the same report keeps its time; `0` = unknown). Empty until a report has been published. |
| `CompositeSnapshot` | `sentiment.py` | (validation only) | `total: float`, `bias: str`, `components{}` |
| `RescueAdvisory` | `options.py` | `cache:options:rescue:<position_id>` | `position_id`, `symbol`, `strategy`, `state`, `heat`, `mark`, `context[]`, `candidates[]`, `error` |
| `RescueCandidate` | `options.py` | (embedded in `RescueAdvisory.candidates`) | `action`, `label`, `apply_kind` (`execute`\|`advisory`), `gross_cash`, `commission`, `net_cash`, `new_max_loss`, `breakeven`, `short_delta`, `width`, `expiry`, `dte_after`, `est_fill_legs[]`, `rationale[]`, `context[]`, `warnings[]`, `score` |
| `FuturesChainSnapshot` | `futures_chain.py` | (the `snapshot` command's args on `cmd:futures_chain`) | `instrument`, `expiry`, `captured_at`, `descriptions{}`, `underlying_symbol`, `underlying_price`, `contracts[]` of `{series, side, strike, mark, bid, ask, volume, open_int, iv, delta}`; an absent reading is `null`. See *The /ES and /NQ chain feed* under the Options service |

---

# Per-Service Reference

Each service is an async FastAPI app built by `services/_scaffold.py:make_app`,
which provides the lifespan, the command-consumer loop, and a `GET /health`
endpoint. `up` is **false**, with a `reason`, when the service's scheduler has
stopped or its last tick is older than `[health] tick_stale_sec` (600 s) in
`config/services.toml`. Other keys: `scheduler_alive`, `scheduler_uptime_s`,
`scheduler_last_tick_age_s`, `dead_letters` (commands the service could not run,
kept for reading and never re-run; `null` if the count cannot be read),
`degrades_total` and `degrades`. A service may add keys of its own through
`make_app(health_extra=...)`, a function of the bus that returns a dict: it can
add keys and never change one the body already has, `up` included, and a
function that raises is counted in `degrades` and leaves the body as it was. The
options service adds `futures_chain`, the /ES and /NQ chain feed's report
(`in_session`, `instruments` keyed by the page's label with the capture's
`captured_at`, `age_s` and `state`, and `pc`, the capturing PC's last reason). It
describes the feed and never decides `up`.

Each command stream has a thread of its own for its blocking read and its
handler; scheduled work shares a pool of `[pool] workers` threads
(`config/services.toml`). On `cmd:options` the long, self-contained commands
(`rescan`, `swing_scan`, `gamma_analyze`, `gamma_explain`, `gamma_history`,
`calc_rate`, `dossier`, `x_post`, `x_post_report`, `scalp_refresh`) run on a second, serial
**slow lane**, so a paper, reprice or rescue command never waits behind one.

The consumer drops a command older than `[age] replay_max_sec` (900 s) unless the
service lists it as safe to run late; the options service also refuses fourteen
side-effect commands older than `side_effect_max_sec` (180 s). A dropped
`paper_create` or `x_post` is answered on its result view.

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
skip-unchanged), the Opening Scalp's morning call (08:36 CT, `[slots.scalp]`), its 15-minute
calls (08:51, 09:06 ... 13:51 CT, `[intraday]`) and its nightly pass (16:20 CT,
`[slots.scalp_bars]`: bar top-up, study rebuild, grading, intraday grading, the scorecard
republished into `cache:options:scalp`, then the intraday study rebuild) and its weekly refit
of the chance-of-target weights (Saturday 08:00 CT, `[slots.scalp_prob]`, weekday
`[prob] refit_weekday`; fires on no trading-day gate and publishes nothing).

**GEX collection and the raw feed.** Each one-minute poll is
`gex_collector.fetch_minute` (the chains, through the proxy) then `derive_minute`
(the rows in `gex_history.db`, priced at the minute). In `config/feed.toml`
`[feed] mode = "tee"` the fetched minute is also written to the raw chain store
(`shared/raw_store.py`: one SQLite file per session, tables `chains`, `minutes`,
`gaps`), and a shadow check re-derives it and publishes its counts and timings to
`cache:feed:shadow`. It adds no Schwab call. The Technical Reference's *The
one-minute GEX collection and the raw feed* has the diagrams and fields.

{{diagram: minute-collection | The one-minute collection today, and what tee adds beside it}}

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
| `paper_close` | `{trade_id, debit}` — on a trade-idea row (`source == "trade_idea"`) the close is booked by `idea_ledger.manual_close`, exit reason `MANUAL`, and the typed `debit` is the stored `exit_debit` (the Ledger's convention: paid for a credit row, received for a debit row) | `cache:options:paper_trades` |
| `paper_delete` | `{trade_id}` | `cache:options:paper_trades` |
| `paper_delete_closed` | — (deletes every CLOSED / EXPIRED row **except trade-idea rows**, which are the weekly review's evidence; `paper_delete` of a single idea row still removes it) | `cache:options:paper_trades` |
| `paper_analyze` | `{trade_id}` | `cache:options:paper_analyze` |
| `x_post` | `{text, tags[], link, image_b64?}` — an ad-hoc marketing post from the composer on the `/social` page (Social Cards; `/x` now redirects there). The image is refused over 5 MB or when it does not decode (logged, nothing posted). Replay-guarded | `cache:options:x_log` |
| `x_post_report` | `{report, mtime}` — enqueued by market_svc when the published market report changes; `report` is the `cache:market:summary` payload, `mtime` the epoch seconds of `latest.html`. Posted once per report (`report_date`, `slot`, `as_of`, `headline`); a report older than `x.report_max_age_min` (45), or of unknown age, is skipped. Replay-guarded | `cache:options:x_log` + `cache:options:x_reports` |
| `scalp_refresh` | — | `cache:options:scalp` — runs the Opening Scalp's `scalp_call.run` now: before the 08:36 CT call time or on a closed day it republishes the last call under a `waiting` / `closed` state and fetches nothing. A call already published today is reused whole, so it only fills in a symbol with none, and no new call is built after entry + `time_stop_min` (09:35 CT). It then runs `scalp_intraday_call.run_slot` for the 15-minute slot whose window is open (`current_slot()`): inside a slot's window (its time up to `[intraday] grace_min`, 5 min) it fills only the symbols that slot has not frozen; outside every window it republishes the slots already published today and makes **no call**, so no `entry_ts` falls between slots. Replay-guarded and on the slow lane (it can fetch bars and three chains, about 30 Schwab calls, and a slot adds about 26 more) |
| `scalp_prob_refit` | — | none published: fits the Opening Scalp's eight chance-of-target models (the 08:36 call and the 15-minute calls, each x four trades) from the stored history, walk-forward checks each and stores them in `scalp.db` `prob_models` under a **new version** (`scalp_prob_fit.refit`; older versions are never touched). The next call reads the new weights; no published call changes. Logs the version, the rows read per kind and the seconds. A failure stores nothing and is counted as a degrade (`options.scalp_prob`). The Saturday 08:00 CT scheduler slot runs the same refit. Replay-guarded (a replay would store another version for nothing) and on the slow lane |
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
| `calc_rate` | (a command older than 180 s is dropped, as for `dossier`) `{request_id, symbol, structure, legs[]}` — `legs` in the Calculator's shape (`option_type`, `side`, `strike`, `expiry`, `qty`, `premium`); `structure` a Calculator template code or `"CUSTOM"`. Grades against `cache:options:calc_chain` (no chain fetch) with the Strategy Finder's `score_all` and `stamp_candidate`, without the quality cut or the volatility drop. Replay-guarded | `cache:options:calc_rating` — `{request_id, symbol, legs, row, error}`: `row` is a Strategy Finder candidate plus `grade`, `composite_score`, the checklist stamps, `vol_gate_blocks` and `structure_known`; `error` is a sentence when `row` is None (no chain, another symbol's chain, a contract the chain lacks, a failure). Always written, so a request is never left unanswered |
| `expected_move` | `{symbol, expiry, legs[], lookback}` | `cache:options:expected_move` |
| `dossier` | `{symbol}` — cleaned by `shared.symbols.clean_symbol` (upper-cased, `[A-Z$][A-Z0-9$.]{0,7}`); anything it refuses is logged and writes nothing, because the symbol becomes part of the key name. Replay-guarded (a command older than 180 s is dropped). **Deduplicated** (`DOSSIER_DEDUP_SEC` = 60): if that symbol's dossier was written less than 60 s ago — measured on the envelope's own `ts`, the write time — the command is dropped and nothing is fetched or re-published. A recent success or `no_quote` blocks the fetch; a recent `fetch_failed` does **not** (a retry costs at most one quote call). An unreadable envelope counts as no recent dossier. Otherwise one fetch of **4 Schwab calls, 5 at most** (quote · GEX chain today..+7 d · 1-year daily price history · IV chain +20..+45 d · the IV analysis' own today..+60 d fallback when that window is empty); a `no_quote` or `fetch_failed` answer spends one | **`cache:options:dossier:<SYMBOL>`** (event `events:options:dossier:<SYMBOL>`), **TTL 900 s**, per symbol so two tabs never share a slot — `{symbol, error, fetched_at, spot, day_pct, flip, put_wall, call_wall, net_gex, iv_rank, current_iv, hv_current, earnings_status, earnings_date}`. Every key is always present; an absent reading is `null`, never 0. `fetched_at` is naive Central ISO. `current_iv` / `hv_current` are **percents** (48.5 = 48.5%); `iv_rank` is the scan's Vol Rank; `day_pct` is always `null` (no day-change parser is shared for a raw quote). `earnings_status` is three-valued: `upcoming` · `none_scheduled` · `not_listed` (the calendar has no data — not "no report"). Walls are assigned by side of spot, and both are `null` when `net_gex` is exactly `0.0` (the after-hours all-zero grid, whose walls would be an argmax tie-break); `flip` is kept either way. `error` is `null` on success, `"no_quote"` when Schwab **answered** and quoted nothing usable for the symbol (a typo — the other legs are skipped), or `"fetch_failed"` when the quote request itself failed (non-200 or raised: proxy down, timeout, token) — the ticker may be fine. One of the three later legs failing blanks only its own keys and records a degrade (`options.dossier_gex` · `_vol` · `_earnings`) |
| `rescue` | `{position_id}` | `cache:options:rescue:<position_id>` |
| `rescue_apply` | `{position_id, candidate}` — the service applies **its own** candidate from the menu it published for the position, found by the echoed candidate's action and contracts; the echo's prices, cash and risk are ignored. No menu, or no matching row, applies nothing | `cache:options:rescue:<position_id>` |

**Scheduled (not command-driven):** `rescan` (auto-scan window), `refresh_header`
(per tick, skip-unchanged), `collect_gex_snapshots` + `publish_gex_status` +
`publish_gamma_symbols` (GEX window), `run_manage_and_refresh` (paper-manage window).
The paper-manage cycle also overlays `rescue_state` / `heat` onto
`cache:options:paper_account` and publishes `cache:options:rescue_summary` (tested +
critical counts) for the nav badge.

**Candidate stamps.** Every candidate row on `cache:options:scan` (and so
`scan_day`), `cache:options:swing` and `cache:options:income` is stamped at publish
time by `compute.stamp_candidate` — from `rescan` (the 0-DTE, swing and Directional
lists and the two `structures_*` lists), `swing_scan` and the income scan. Stamping is best-effort **per row**: a row
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

**`cache:options:scan`** carries five candidate lists. `signals_0dte` and
`signals_swing` are credit spreads and iron condors in the engine's flat shape
(`short_strike` / `long_strike`, per-share dollars, the premium composite).
`signals_directional`, `structures_0dte` and `structures_swing` are the
**normalized** shape: a `legs` list, per-contract dollars, `rr` (a ratio),
`breakevens` (a list) and the Fit + Quality score. The two `structures_*` lists
(since 2026-10-06) hold everything else the 0-DTE and Swing windows build — debit
spreads, straddles and strangles, butterflies and condors, and on Swing calendars
and diagonals, and since 2026-10-07 ratio backspreads. Each of their rows also
carries `group` (`VERTICAL` / `STRADDLE` / `BUTTERFLY` / `CALENDAR` / `RATIO`,
the family that built it), on a backspread `target_breakeven` (the far
breakeven, the one the score reads), and, when it buys premium and
was kept through an earnings report, `spans_earnings: true` with `earnings_date`.
A payload cached before the two lists existed validates with both empty. They are
not pushed and not counted by `compute.build_matrix`; they are recorded as
tracked rows (`cache:options:tracked`, below). Do not assume one shape across the
five lists, and do not rank a row of one shape against a row of the other.

**`cache:options:tracked`** — the scanner's structures that are recorded to be
measured, never traded (`services/options_svc/tracked.view`). Published at
service start, after each tracked cycle (every 15 minutes in session) and on
`captured_reload`; an unchanged view is not rewritten.

```
{date, counts: {open, closed, closed_today},
 open:  [{signal_id, symbol, strategy, family, scanner_type, expiration,
          dte_at_entry, entry_credit, entry_max_loss, entry_max_profit,
          entry_score, entry_grade, entry_underlying, unbounded,
          entry_spans_earnings, first_seen_ts, current_value, unrealized_pnl,
          current_underlying, recommendation, recommendation_reason,
          last_mark_ts, legs: [{kind, side, strike, expiration, qty, ...}]}],
 closed_today: [{signal_id, symbol, strategy, exit_reason, exit_value,
                 realized_pnl, close_ts, ...}],
 stats: [{strategy, family, n, wins, win_pct, total_pnl, avg_r, unmarkable,
          unbounded, through_earnings}]}
```

`entry_credit`, `current_value`, `exit_value`, `entry_max_loss` and
`entry_max_profit` are per share. `entry_credit` is signed (a debit is negative),
and `current_value` / `exit_value` are the cost to close, so a position worth
money carries a negative one. `unrealized_pnl` and `realized_pnl` are dollars for
one contract. `realized_pnl: null` with `exit_reason: "UNMARKABLE"` means the row
has no result; it is not a zero. `expiration` is the FRONT expiration; a calendar's
back month is on its second leg.

**Reading `signals.db` yourself.** Tracked rows have `scanner_type` `0DTE_STRUCT`
or `SWING_STRUCT` (`shared.structures.TRACKED_SCANNER_TYPES`) and no strike
columns. Every multi-row reader in `signal_db` leaves them out unless called with
`tracked=True`, which returns only them. A query of your own over `signals` must
add `shared.structures.not_tracked_sql(prefix)` unless it means to read them.

**`cache:options:scan_day`** — the day union the Market Scanner and the Symbol
Dossier render: `{date, scan_seq, signals_0dte[], signals_swing[],
signals_directional[], structures_0dte[], structures_swing[], setups, truncated?}`. `date` is the **Central** trading date —
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
`DIRECTIONAL` / `STRUCT_0DTE` / `STRUCT_SWING`. The two `STRUCT_*` buckets (since
2026-10-06; a funnel written before then has neither) are flat, like
`DIRECTIONAL`: `{built, vol_gate, earnings, score_cut, capped, outside_rth,
emitted, build_failed}`, with `built == vol_gate + earnings + score_cut + capped
+ outside_rth + emitted` unless `build_failed`. `earnings` counts candidates that
sell premium (or whose vega could not be read) and would be held through a report.
A window the scan had no chain for leaves its `STRUCT_*` bucket at zero; the credit
bucket for the same window (`chain: false`) says why. `outside_rth` (a spread
bucket's `spreads.outside_rth`, a flat bucket's own `outside_rth`) counts the rows
held back because the scan finished outside the regular 08:30–15:00 CT session;
outside it all five of `cache:options:scan`'s candidate lists are empty and
`warnings` carries the reason. A funnel written before 2026-10-02 has no such key.
Written after every scan with `skip_unchanged`.

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
(`options.ledger_caps`), never a raise. No TTL. **Trade-idea rows are not in it**: they
are left out of `open` and out of `realized_pnl` (so out of `equity`), because they
are not held to the caps.

**Trade-idea rows on `cache:options:paper_trades`.** The hourly trade idea
(`handlers.run_trade_idea`) books each posted idea as an ordinary row of `trades.db`
with `source = "trade_idea"` (the only marker; `notes` reads "Trade idea") through
`idea_ledger.book`, which evaluates no cap. Beyond the Ledger's usual fields it carries
`idea_id`, `idea_posted`, `idea_grade`, `idea_score`, `idea_label`, `target_pnl` and
`stop_pnl` (dollars per contract, positive), `last_mark_pnl` / `last_mark_at` (the latest
5-minute mark, per contract), `mae_pnl` / `mae_at` (the worst point, per contract, `0.0`
until a mark goes negative; a settlement or a hand close lowers it too), `exit_time` and
`exit_reason` (`TARGET` · `STOP` · `EXPIRED` ·
`MANUAL`), `exit_debit` and `exit_debit_total`. An OPEN idea row's `unrealized_pnl` is
`last_mark_pnl x quantity` at any hour. The 5-minute cycle (`scheduler.idea_marks_due` →
`handlers.run_idea_marks_and_publish`, gated by `[idea_trades] mark_interval_min`)
republishes the view without a chain reprice, and a mark appends no `trade_events` row.

**`cache:options:scalp`** (event `events:options:scalp`) — the Opening Scalp page's
only view. Validated on write by `shared.contracts.options.ScalpView`; one that fails is
not published and is counted as a degrade (`options.scalp_view`). Published at 08:36 CT,
on a `scalp_refresh`, and (as `waiting` / `closed` / `error`, carrying the last call) by
every other run. After the nightly grading it is republished with only `scorecard`
replaced (`scalp_call.republish_scorecard`: no build, no fetch, `symbols` unchanged).
`scalp_call.run` and that republish hold one lock, so two builds never run at once.

Top level: `{day, call_day, generated_at, state, symbols, rules, context, scorecard,
next_call, intraday}`. `intraday` is the 15-minute calls' section, described below; the
08:36 publish and the nightly scorecard republish carry it forward unchanged.
- `state` is `call` (a call was built), `waiting` (a trading day before the call
  time: the later of the 08:35 entry and the `[slots.scalp]` time, so 08:36 CT),
  `closed` (not a trading day) or `error` (an unexpected failure). In the last three,
  `symbols` is the **last published call** and `call_day` says which day's it is:
  compare it with `day` before reading a call as today's. `rules` (the target, stop,
  time stop, strikes, volatility multiplier and minimum sample the numbers were made
  under) is present only on `call`.
- `next_call` is the next scheduled call, ISO with the Central offset: today's slot while `waiting`, otherwise the next trading day's.
- `context` is `{report_url, news[], flow[]}`: the reports link, index headlines
  (`{title, url, source, published_at, tickers}`) and today's flow alerts on the scalp
  symbols since 08:30 (`{ts, symbol, type, side, text}`). Shown for context: **not in the
  study's rates.** The headlines and the report link are not in the chance of target
  either; the chance's `flow` factor is its own windowed count of bullish and bearish
  alerts, not this list.
  Both feeds are private views, so this view is read only by the private app.
- `scorecard` is `{days, window, symbols: {SYMBOL: {days, edge, caution, roles}},
  intraday}` from the graded record, or `{}` when nothing has been graded. `intraday`
  is present only once 15-minute calls have graded days: `{days, window, symbols:
  {SYMBOL: {days, blocks: {block: {days, edge, caution, by_word}}}}}`. `days` is
  distinct days throughout (the 08:36 sections count role-days). In a block, `edge`
  and `caution` are `{days, rows, wins, rate, p_base}` (`rows` is slot-roles; `rate`
  and `p_base` are `null` with none) and `by_word` is `{Confirms, Mixed, Contradicts}`,
  each `{days, rows, wins, win_rate}` for the long-with-the-move role. With no 08:36
  grades the scorecard can be `{"intraday": ...}` alone (no `days`, `window` or
  `symbols` at the top). On an 08:36 card `edge` and `caution`
  are `{days, wins, rate, p_base}` (`rate` and `p_base` are `null` with no days);
  `roles` is keyed by role, each `{n, win, loss, timeout, win_rate, chain_n,
  chain_win_rate}`. A win is the model's result; the chain's is beside it.

`symbols` is keyed by the symbol (`$SPX`, `SPY`, `QQQ`). An entry:

| Key | Meaning |
|---|---|
| `direction` | `1` opened up, `-1` down, `null` for none (a flat open, no opening bar, a failed build) |
| `bucket`, `alignment` | `low` / `mid` / `high`, and the 0–1 share of inputs that took a side and agreed; `null` when nobody voted |
| `entry_spot`, `entry_ts` | the close of the 08:30–08:35 bar and the 08:35 boundary (epoch seconds) |
| `inputs`, `agree` | each input's reading and its verdict: `1` agrees, `-1` disagrees, `0` dead zone, `null` absent. `open_range_ratio` is in `inputs` only |
| `vol_at_entry`, `vol_source` | the volatility index read (as a fraction) and which one |
| `chain` | `{spot, atm_iv, expiry}` of the 08:36 chain, or `null` when it failed |
| `sigma`, `sigma_source` | the volatility the strikes and points were priced at, and `chain` or `model` |
| `roles` | keyed `long_with`, `long_against`, `credit_with`, `credit_against`; see below. Empty when the symbol has no call |
| `highlight`, `avoid` | the role with an edge **above** its base rate (or `null`), and the roles with an edge **below** it |
| `chance` | the fitted chance of target (below). **Absent** when `[prob] show` is off, or on a call frozen before the feature existed; `null` for a symbol with no roles (flat, missed, failed, early close); otherwise `{version, fitted_at, by_role, beats_usual, pushes}` |
| `note`, `points_note`, `made_at` | why there is no call or no edge (`no measured edge`, `no opening bar yet`, `opened flat — no direction`, `missed the call window`, `the call could not be built`), why there are no points, and when the call was made. `missed the call window` is only for a morning that had a direction; after the window a flat open or a missing bar keeps its own note, with its inputs, and no `made_at` |
| `rules` | on a called symbol only (one with roles): `{target_pct, stop_pct, time_stop_min, min_premium, iv_scale}` as they were when the call was made. Grading walks the trade under these, never the evening's config |

A role is `{structure, p, lo, hi, n, p_base, n_base, edge, strikes, entry_value,
entry_source, win_points, loss_points, chance}`: `structure` is that morning's contract
(`long_call`, `long_put`, `pcs`, `ccs`); `p`, `lo`, `hi` the bucket's win rate and its
Wilson 95% interval, `n` the bucket's sample, `p_base` / `n_base` the all-mornings
rate; `edge` the study's verdict (Brier below the base rate at every multiplier); and
`strikes` (`[strike]` or `[short, long]`), `entry_value` (a debit for a long option, a
credit for a spread), `entry_source` (`chain` or `model`) and the underlying points to
the target and the stop (`win_points`, `loss_points`; a put's target is negative). A
rate that is not known is `null`, never `0.0`; `ScalpView` rejects a `p`, `lo`, `hi`,
`p_base` or `chance` outside 0..1. Every field has a default, so a payload Redis kept across
a restart still validates.

**`chance`** (an entry's, on the 08:36 entries and the intraday `latest` entries alike;
each role's own `chance` is that role's figure, copied from `by_role`):

```text
{version, fitted_at, by_role, beats_usual, pushes}
  version     the prob_models version the figure was made with; null = no weights stored,
              or the call had no direction (every figure is then null, never 0.5)
  fitted_at   when that version was fitted (ISO, Central offset), or null
  by_role     {long_with, long_against, credit_with, credit_against}: the chance 0..1 that
              the trade wins, or null (a model fitted on no rows says nothing)
  beats_usual {role: bool}: that role's model's walk-forward verdict at the fit - true only
              when its Brier score beat the usual rate's by at least [prob] min_brier_margin
              on weeks it was not fitted on
  pushes      [{factor, reading, points, days, assumed}] for the long_with trade, largest
              |points| first: the factor's key (price:<input>, slope_5|15|60, rsi_5|15|60,
              room, breadth_room, flip_side, netprem, flow, sentiment, block:midday,
              block:afternoon), its raw reading signed for the call (null for a gap),
              its push in percentage points of chance, the distinct days its weight rests
              on (for display), and true while the model's data_share for that factor
              is under [prob] assumed_share (0.5): its weight still rests mostly on the
              starting assumption (less than half of it learned from data). A model
              stored without data_share reads every factor as assumed
```

The figure is frozen with the call: a later refit never changes a published entry, and
the grader reads the frozen one. A symbol's chance is computed after its verdict and
changes no rate, price or highlight.

**The `intraday` section** (the 15-minute calls; `scalp_intraday_call.run_slot`, validated
as part of the same `ScalpView`; `{}` before the first slot or in a payload written
before it existed):

```text
{day, slot, next_slot, time_stop_min, latest, timeline}
  day        ISO date the section belongs to; compare it with today's Central date
  slot       the latest slot published ("08:51" ... "13:51")
  next_slot  when the next call is made (ISO, Central offset); the next trading day's
             first slot after 13:51; on an early close, a slot whose trade would pass
             the close is skipped
  time_stop_min  the configured time stop: the page ends a slot that made no trade
             (every symbol flat or short of bars) at its entry plus this (60 when absent)
  latest     {symbol: entry}        the slot's call for each symbol
  timeline   [{slot, symbols: {symbol: {direction, highlight, word,
              edge_p, edge_p_base, n_days, chance, note}}}]   one row per slot published today,
              oldest first; chance is the named trade's (the highlighted role's, else
              long_with's)
```

An `latest` entry has the 08:36 entry's shape (`direction`, `bucket`, `alignment`,
`entry_spot`, `entry_ts`, `inputs`, `agree`, `vol_at_entry`, `vol_source`, `chain`,
`sigma`, `sigma_source`, `roles`, `highlight`, `avoid`, `note`, `points_note`, `rules`,
`made_at`) plus: `slot`; `block` (`morning` / `midday` / `afternoon`); `rates_basis`
(`"all stored days"`: the rates are over every stored day, only the edge flag is
walk-forward); `inputs_failed` and `inputs_note` (the bar symbols the call was built
without fresh bars for); `confirmation`; `dealer_line`. `entry_ts` is the slot floored to
the 5-minute boundary. `made_at` is the wall clock when the entry was built (the slot's
window is judged by the scheduler's tick, which can be earlier by the wait for the run
lock). Its inputs are `since_open`, `vwap_side`, `fut_15m`, `vol_change`,
`breadth_up_share` (a 0..1 share, never a percent) and one `etf:<fund>` per sector fund.
Its roles carry **`n_days` / `n_base_days`** (distinct days) in place of `n` / `n_base`,
and `wall_context` (a list of words naming any call or put wall between entry and the
target or stop). `note` is `no measured edge` (the usual), `flat over the last 15 minutes
— no direction`, `no bars for the last 15 minutes yet`, `missed the call window` (the
slot's trade was already over when it was built), `the market closes early today` (a
half day in `[intraday] early_close_days`, and the slot's trade would pass the early
close: no roles, nothing fetched or frozen) or `the call could not be built`.

`confirmation` is `{flow, netprem, dealer, sentiment, score, word, n_present,
n_sources, word_note, dealer_line, truncated}`. Each component is `{verdict, raw,
stale}`: `verdict` is `1` agrees, `-1` disagrees, `0` looked and found no side, `null`
no reading; `raw` holds the values it was made from, with a `state` (`ok`, `absent`,
`stale`, `stale_point`, `no_reading`, `no_entry_ts`, `other_day`, `error`) for flow,
net premium and the dealer read (flow is `other_day`, and stale, when the flow view's
`date` is not the slot's day). The **sentiment** component differs: its `raw.state` is
the regime name (`bullish`, `neutral`, ... or `null`) and its status is in `raw.source`
(`ok`, `absent` or `stale`). `score` is the weighted mean in -1..1 (`ScalpView` rejects
one outside it) and `word` is `Confirms`, `Mixed` or `Contradicts`. `n_present` is how
many of the `n_sources` (4) components gave a reading (a zero weight does not count);
with fewer than `[confirm] min_sources` (2) `score` and `word` are `null` and
`word_note` is `"too few sources"` (else `null`). **It is not in the study's rates or in the chance of target** (which reads the recorded
sources, never the word); the page no longer shows the word or its count, and never
printed the score. The dealer component's `raw` carries the matrix row's
`spot`, walls, `flip` and `net_gex`, then `room_spot` (the price the room is measured
from) and `room_spot_source` (`entry`: the call's own `entry_spot`; `matrix`: none was
given), `wall_kind`, `wall`, `room`, `room_pct`, `win_points`, `own_verdict`, `breadth
{with_room, of, share, verdict}` (the big ten at their own matrix spots), `above_flip`
and `walls_untrusted` (net GEX missing or 0: the walls are then no reading).

The service reads **five** views to build the confirmation, none of them through the
proxy: `cache:options:matrix`, `cache:options:net_premium`, `cache:options:flow_alerts`,
`cache:options:flow_sides` (the bought / sold estimate per alert id; used only when its
`date` equals the flow view's) and `cache:sentiment:composite`.

**`scalp.db`** (`services/options_svc/data/scalp.db`, `repo_paths.SCALP_DB`) is the
Opening Scalp's store. It is **kept forever and in the nightly backup**: it holds price
history Schwab no longer serves. All times are epoch seconds (a bar's START); a
function takes `db_path=None`, resolved when it is called.

| Table | Key | Holds | Written by |
|---|---|---|---|
| `bars` | `(symbol, interval, ts)` | open / high / low / close / volume, `interval` 1 or 5 | `scalp_fetch` (backfill, the nightly top-up, and each 15-minute slot's `top_up_today`, which starts one bar before the latest stored bar when that is from an earlier day, so no hole is ever skipped) |
| `chain_slices` | `(day, symbol)` | the 08:36 0DTE chain cut to the strikes near the money, a zlib JSON blob | `scalp_chain` |
| `outcomes` | `(day, symbol, iv_scale, role, interval)` | the modelled result (`win` / `loss` / `timeout` / `skip`), minutes, entry value, `both_in_bar` | `scalp_study.rebuild` |
| `mornings` | `(day, symbol)` | the measured morning, or the reason it was skipped | `scalp_study.rebuild` |
| `verdicts` | `(symbol, role, bucket)` | the Brier scores per multiplier and the stored `edge` | `scalp_study.rebuild` |
| `calls` | `(day, symbol)` | each published call, frozen as shown | `scalp_call` |
| `grades` | `(day, symbol, role)` | `model_result`, `chain_result`, `highlighted`, `avoided`, `p_base` | `scalp_grade` |
| `iv_calibration` | `(day, symbol)` | the chain's at-the-money volatility, the morning's reading and their ratio | `scalp_grade` |

Six more tables hold the 15-minute calls, and two more (`prob_sources`, `prob_models`,
below) the chance of target. They were added with `CREATE TABLE IF NOT EXISTS`;
**the eight tables above are never altered** (prod holds them), and the new ones are
keyed by `(day, slot, symbol)` rather than `(day, symbol)`:

| Table | Key | Holds | Written by |
|---|---|---|---|
| `intraday_readings` | `(day, slot, symbol)` | the confirmation inputs recorded at the slot (`json`: `slot`, `entry_ts`, `recorded_at`, `direction`, `components` with their raw values, `snapshot` of each source's `{state, age_min}` and of walls, flip, net GEX, net premium levels, sentiment (with its `age_min`) and flow counts for the symbol and the big ten; a flow list of another day is recorded as `{date, state: "other_day"}` with no counts). Written for every slot **and** for 08:36, with or without a direction | `scalp_record` |
| `intraday_calls` | `(day, slot, symbol)` | each published 15-minute call, frozen as shown (`json`) | `scalp_intraday_call` |
| `intraday_chain_slices` | `(day, slot, symbol)` | that slot's 0DTE chain cut to the strikes near the money, a zlib JSON blob, with its `ts` | `scalp_intraday_call` |
| `intraday_outcomes` | `(day, slot, symbol, iv_scale, role)` | the modelled result (`win` / `loss` / `timeout` / `skip`), `structure`, `block`, `bucket`, minutes, entry value, `both_in_bar` | `scalp_intraday_study.rebuild` |
| `intraday_verdicts` | `(symbol, block, role, bucket)` | `json`: the Brier scores per multiplier, `edge`, `unit` (`"day"`), and the `min_margin` and `min_samples` the edge was judged under | `scalp_intraday_study.rebuild` |
| `intraday_grades` | `(day, slot, symbol, role)` | `model_result`, `chain_result`, `highlighted`, `avoided`, `confirm_word`, `p_base`, `block` | `scalp_intraday_grade` |

The chance of target's two tables (`scalp_store.save_prob_sources` / `latest_prob_models`
and friends):

| Table | Key | Holds | Written by |
|---|---|---|---|
| `prob_sources` | `(kind, day, slot, symbol)` | `kind` is `morning` (the 08:36 call) or `intraday`; `json` is a `scalp_confirm.snapshot`-shaped record of the dealer, net-premium and flow sources as they stood at that call (sentiment is never loaded here: its history is the live records only): **history only**, rebuilt from old gamma-history copies. A fit reads the live `intraday_readings` row first and this table only where there is none | `tools/scalp_prob_backfill.py load` (re-running replaces the rows of the files it reads) |
| `prob_models` | `(version, kind, role)` | one fitted model: `json` = `{weights, intercepts, scaling, days, n_days, rows, priors, prior_sd, walk_forward}` where `walk_forward` is `{weeks, brier, brier_usual, beats_usual, calibration}`; `fitted_at` is the Central ISO time of the fit. Eight rows per version (`morning` and `intraday` x the four roles), saved in one transaction | `scalp_prob_fit.refit` (the Saturday slot, or the `scalp_prob_refit` command) |

`prob_models` is **append-only**: a refit takes the next version number and never touches
an older one; the live chance reads the highest version. Both tables are kept forever and
in the nightly backup, like the rest of `scalp.db`.

`outcomes`, `mornings` and `verdicts` are deleted and rewritten together in one
transaction every night, so a reader sees the old study or the new one; so are
`intraday_outcomes` and `intraday_verdicts`, together, by the intraday rebuild. `calls`,
`grades`, `iv_calibration`, `intraday_readings`, `intraday_calls`, `intraday_chain_slices`
and `intraday_grades` are records of what was shown or seen and are **never rebuilt**.
A recorded `intraday_readings` row is **write-once**, with one exception: a row stored
with no `direction` is replaced by one that has it (a one-way upgrade). `intraday_calls`
and the grades are written once from what was published. Like `scalp.db` as a whole, they
are **kept forever and in the nightly backup**.
`tools/scalp_prob_backfill.py` (a CLI, not a service) fills `prob_sources` in two steps so
the slow part can run on any box: `extract --gamma <gex_history.db copies> [--flow-db ...]
--out file.jsonl` reads the copies read-only (`mode=ro&immutable=1`), recomputes each call
time's walls, flip, net GEX and net premium as the live matrix does, and writes one JSON
line per `(kind, day, slot, symbol)`; `load --in file.jsonl [...] --db <scalp.db>` merges
the files (the first non-null value of each field wins) and never writes a key
`intraday_readings` already holds.
Dated price history is fetched through the proxy's `/passthrough` (`endpoint=/pricehistory`):
a tool that calls it by hand (`tools/scalp_backfill.py`) must have the stack's `.env`
loaded, or every request is a 401 (`PROXY_SHARED_SECRET`).

### The /ES and /NQ chain feed (`cmd:futures_chain`)

The options service also takes the 0-DTE option chains of /ES and /NQ from a capture on
the operator's Windows PC (ThinkorSwim through Excel), because Schwab serves no
futures-option chains. Nothing here reaches Schwab or the proxy. The feed **ships hidden**
(`[capture] show_in_dropdown = false`). The Technical Reference's *The /ES and /NQ chain
feed* has the checks, the gamma and the cadence.

{{diagram: futures-chain-feed | The /ES and /NQ chain feed, from ThinkorSwim to the page}}

**One direction.** The PC sends; the server never connects to it. The PC runs two commands
over one ssh each, through `tools/futures_chain_remote.sh` (which loads the stack's `.env`
and the checkout's venv, so the capture needs to know one path):

| Command | What it does |
|---|---|
| `futures_chain_cli.py plan` | Prints the capture's plan as one line of JSON and exits 0. Reads nothing. |
| `futures_chain_cli.py ingest` | Reads `{"snapshots": [...], "status": {...}}` from standard input and puts one `snapshot` command per instrument, then one `pc_status`, on `cmd:futures_chain`. It prints `{"queued": ["snapshot", ...]}`. |

`ingest` checks the shape and the size and nothing else: the contract, the age, the terms and
the checks against the last capture are the service's. A byte order mark (PowerShell adds
one) is stripped. A snapshot whose `instrument` is not `ES` or `NQ` is refused whole; a
snapshot over `[capture] max_snapshot_kb` is dropped and named on standard error while the
others still go. Exit status: **0** written; **2** input or usage it cannot use (nothing
sent); **3** a snapshot over the limit was dropped (the rest were written), or standard input
was larger than a capture can be; **4** Redis refused a write (its message says how many
commands were queued first). The reason is on standard error.

**The plan** (`futures_chain.build_plan`): `expiry` (ISO date), `date_text` (`2 NOV 26`, as a
contract description prints it), `in_session`, `opens` and `valid_until` (epoch seconds),
`interval_sec`, `min_fill_fraction`, `max_snapshot_kb`, the PC's own settings
(`min_read_fraction`, `fill_settle_sec`, `fill_wait_max_sec`, `probe_round_steps`,
`probe_reach`, `recentre_share`, `ssh_timeout_sec`, `no_plan_retry_sec`,
`plan_retry_max_sec`, `hang_limit_sec`, `hang_exit_after_sec`, `fallback_interval_sec`,
`fallback_clean_captures`), and `instruments`, keyed by root (`ES`, `NQ`):
`symbol`, `multiplier`, `exchange`, `strike_step`, `strike_width_pct`, `parity_limit_pts` and
`candidates`, the series names to try. A key a capture lacks takes a stated default in the
script, because the PC and the server do not update together.

**Commands (`cmd:futures_chain`):**

| Type | Args | Effect |
|---|---|---|
| `snapshot` | a `FuturesChainSnapshot` (below) | One instrument's capture. Ends as exactly one of: dropped without comment (not newer than the stored capture), refused with a reason on the status view, or written: the five views into `gex_history.db` at the capture's own minute, the chain into `cache:options:futures_chain:<symbol>` (and `…:frozen` while it is far enough before expiry), then a repaint of the page's gamma view when it is on that symbol. A payload that does not match the model is counted as a degrade (`options.futures_chain`) and refused |
| `pc_status` | `{reason, at}` | The PC's own report: why it is not capturing (ThinkorSwim logged out, Excel would not start) or `""` when it is. `reason` is cut to 300 characters; `at` is epoch seconds (the service's own clock when it is missing or not a number). Stored on the status view's `pc` entry, apart from the instruments' entries |
| anything else | | Logged and ignored |

The stream has its own `[stream_keep]` cap (`"cmd:futures_chain"`, shipped 20), because each
command carries a document. A snapshot older than `[capture] max_age_sec` (900 s) when it
arrives is refused, which is also what keeps a consumer group created at id 0 from replaying
the stream's history as current. Nothing on this stream reaches a paper book.

**`FuturesChainSnapshot`** (`shared/contracts/futures_chain.py`):

| Field | Type | Meaning |
|---|---|---|
| `instrument` | string | The root, `ES` or `NQ` |
| `expiry` | date | The expiry the capture meant to read |
| `captured_at` | float | Epoch seconds the values describe, on the PC's clock |
| `descriptions` | `{series: string}` | One contract's ThinkorSwim `DESCRIPTION` per series; the adapter reads the multiplier, expiry, underlying and `AM Settled` off it |
| `underlying_symbol` | string | The future the options are written on, e.g. `/ESZ26:XCME` |
| `underlying_price` | float | What it traded at |
| `contracts` | list of rows | Below |

Each row is `{series, side, strike, mark, bid, ask, volume, open_int, iv, delta}`: `side` is
`"C"` or `"P"`, `strike` a positive number, and the rest readings. A reading that is `N/A`,
empty, not a number, not finite or a boolean is `null`, never 0; a price or count below zero is
`null`; `iv` is the percent as ThinkorSwim prints it (`"12.51%"` is `12.51`).

**Views.**

| Cache key | Payload |
|---|---|
| `cache:options:futures_chain:<FOP.ES or FOP.NQ>` | `{chain, captured_at, expiry, hours_left}`: the adapted chain in Schwab's layout (one expiry key, `YYYY-MM-DD:0`, every contract at `daysToExpiration` 0, open interest and volume already scaled by multiplier / 100, a calculated `gamma`, and `series`), the capture's epoch time, the ISO expiry and the hours to the close. No event is published |
| `cache:options:futures_chain:<symbol>:frozen` | The same, for the last capture taken at least `frozen_before_expiry_min` (60) before expiry |
| `cache:options:futures_chain_status` | The feed's health: `{<symbol>: entry, pc: {reason, at}}`, below |
| `cache:options:futures_chain_desk` | The private Desk's rows: `{<symbol>: {latest, frozen}}`, each `{symbol, spot, flip, call_wall, put_wall, net_gex, gex_regime, captured_at, expiry, not_moving}`. Written on each accepted capture; `frozen` stops `frozen_before_expiry_min` before expiry. The published Desk never reads it. |
| `cache:options:futures_chain_alert` | What the stopped-capture alert has told the operator: `{<symbol>: {alerted_at}}` for each instrument last reported stopped. Its own key: the status view is read, changed and written back by the stream's consumer thread, and a second writer would lose a write. Nothing else reads it |
| `cache:options:gamma` | For these symbols only, the usual snapshot plus a **`feed`** block: `{label (/ES), captured_at, expiry, frozen, stale, reason, why, strike_low, strike_high, last_row_at}`. `stale` and `frozen` are worked out when the payload is built and are as old as it, so a reader works the state out again against its own clock; `reason` is the age sentence plus `why`; `why` is the cause alone; `strike_low` / `strike_high` are the range captured; `last_row_at` is where the session's rows end. A stale or frozen payload carries no `forward` band. `compute.gamma_snapshot` is called with `with_term=False`, so there is no Term |
| `cache:options:gamma_symbols` | Lists `FOP.ES` and `FOP.NQ` only while `show_in_dropdown` is on. The public list (`public_gamma.SYMBOLS_KEY`) never does |

**A status entry** (per `FOP.` symbol; every field is always present): `captured_at` (the
newest accepted capture, or `null`), `expiry`, `listed` (contracts that read a value),
`with_gamma` (of those, the ones with a calculated gamma), `parity_gap` (`null` when no pair
could be read), `reason` (a sentence in whole words, empty when well; a snapshot that was
refused leaves the last good one's other fields in place), `repeats` (the run of captures with
the same price, volume and open interest), `refused_at`, `price`, `activity` (volume plus open
interest, to compare captures) and `refused_listed` (the count of the last refused half-filled
capture). The comparisons between one capture and the one before it read it from here, so a
service restart does not forget it.

**`/health`** carries a `futures_chain` block, through `make_app(health_extra=)`:
`{in_session, instruments, pc}`. `instruments` is keyed by the page's label (`/ES`) and holds
only an instrument with an accepted capture or a refusal: `{captured_at, age_s, refused_at,
state, repeats, listed, with_gamma, reason}`, where `state` is `capturing`, `stopped` or
`starting` during the session and `between sessions` or `no capture accepted` outside it.
`pc` is `{reason, at}` or `null`. It describes the feed and never changes `up`: a PC that is
off is not a failed options service.

**Rules for a reader.** The cached chain holds every contract's bid, ask and mark, and Redis
is readable by the public process, so no public page may read these keys. A reader must not
fall back to another symbol's chain: a stale capture is labelled, never shown as current.

### Live orders to ThinkorSwim (`cmd:live_order`)

A trade can be sent to the Schwab account as a real order, and cancelled while it works (Phases 1-3 of the
[design](../../plans/2026-11-05-live-order-to-thinkorswim-design.md)): a single option, a
vertical, an iron condor, a straddle, a strangle, a butterfly, a condor, a calendar, a
diagonal, or any other all-option mix as `CUSTOM` (`live_order.SENDABLE_TYPES`). The ticket
is in the private app. A detail panel (`live_order_link.open_ticket`) and the idea banner
open it as a WINDOW over the page (`pages/send_order.open_window`, built on `kit.window`);
the route `/send-order` serves the same ticket as a page. Either way it names its trade
one of two ways:

- `trade=<trade_id>`: a trade idea the Paper Ledger booked; the service reads
  its legs from the Ledger.
- `spec=<token>`: any other detail panel. `webgui/live_order_link.py` stashes
  the panel's trade as a SPEC `{symbol, legs, label, price}` under a 32-hex token (in the
  webgui process's memory, the newest 64), and the commands carry the spec itself. The
  service checks it again with `live_order.read_spec` (the ticker allow-list, each leg,
  a sendable structure, no expired leg, no short far leg, no uncovered call), refuses a
  feed symbol, and prices it itself; `price` is only what the panel showed, for the drift
  warning.

The service is `options_svc` (`live_order_handlers.py`, store `live_order_store.py`).
Everything is off while `config/live_orders.toml mode = "off"`, the shipped value.

**Commands (`cmd:live_order`, domain `live_order`).** Each is dropped unread when it carries
no time or is older than `max_command_age_sec` (30).

| Command | Args | Does |
|---|---|---|
| `quote` | `trade_id`, or `quote_id` (32 hex) + `spec` | Reprices the trade's legs from a fresh chain (`maxAge: 0`, every expiry the legs use) and publishes the quote view under the `trade_id` or the `quote_id` |
| `preview` | `ticket_id` (32 hex, new), `trade_id` + `idea_posted` or `quote_id` + `spec`, `alias`, `contracts`, `limit` | Builds the order, sends it to `/orders/preview`, stores the ticket (`previewed` if Schwab accepted it, else `refused`; good for `ticket_ttl_sec` 120) and publishes the ticket view. A repeated `ticket_id` is ignored |
| `place` | `ticket_id` | Sends a `previewed` ticket once: mode `live`, the regular session, not expired, the order unchanged (a digest), the size inside the ceilings. Claimed with one conditional UPDATE, then ONE POST to `/orders/place`. Ends `sent`, `rejected` or `unknown` (settled by listing the account's orders: exactly one match on legs and price) |
| `status` | `ticket_id` | Expires a stale preview, settles an `unknown`, or reads a `sent` order's Schwab status; republishes the ticket view and the day's list |
| `cancel` | `ticket_id` | (Phase 3) Reads the ticket's order from Schwab and, only when Schwab says `cancelable`, sends ONE `DELETE /orders/{hash}/{order_id}`. Refused while the mode is `off` or for a ticket that is not `sent`; any hour. The outcome is the ticket view's `error` line ("Cancel sent", "Schwab refused the cancel", "No answer"), with Schwab's status read again |
| `today` | none | (Phase 3) Reads each open ticket sent since midnight Central from Schwab and publishes the day's list |

**Views** (24-hour TTL):

| Key | Holds |
|---|---|
| `cache:live_order:quote:<trade_id or quote_id>` | `mode`, `aliases`, `in_session`, `symbol`, `structure`, `strategy`, `expiration`, `expirations[]`, `price_source` (`idea` or `panel`), `debit`, `posted_price`, `legs[]` (each with its `mid`), `live_price`, `max_loss_per_contract`, `max_profit_per_contract` (`null` when unbounded, or with two expiries when it depends on volatility), `default_contracts`, `drift_pct`, `drift_warn`, `exits` (`target`, `stop`, `stop_on_order`, `exit_tif`, `pcts`); `error` when it could not be priced |
| `cache:live_order:ticket:<ticket_id>` | `status`, `alias`, `contracts`, `limit`, `max_loss`, `verdict`, `messages[]`, `legs[]` (instruction, quantity, option symbol), `expires_epoch`, `order_id`, `order_status`, `error`, `exits`, `in_session`, `can_cancel` |
| `cache:live_order:today` | `checked_at`, `mode`, `orders[]`: each `ticket_id`, `alias`, `symbol`, `structure`, `contracts`, `limit`, `debit`, `max_loss`, `status`, `order_id`, `order_status`, `sent_at`, `error`, `can_cancel` (newest first, at most 50) |

⚠ Neither view carries an account number or hash: the alias is looked up to a hash in the
service on each send (`[[accounts]]` in the operator's `config/local/live_orders.toml`), and
Schwab's answers pass through `live_order.scrub` first. The public Redis user can read
`cache:*`, so this is the rule, not a courtesy; no public module reads these keys.

**Store:** `services/options_svc/data/live_orders.db` (`tickets`), kept and backed up: it is
the record of what was sent.

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
`trade-analyzer/fit_swing_model.py` (run monthly by `trading-<env>-swing-refit.timer`,
shipping only past `config/swing_model.toml`'s gate — never imported by a
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
Market report button and dialog.

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

**The tile map is a function, `symbols.symbol_map(today=None)`, not a constant.**
Two of its entries are futures, and a futures contract expires every quarter, so
their `display` (`/ES[Z26]`), `quote_symbol` (`/ESZ26`) and `description` are
built on each call from `shared.futures.front_month(root, today)`. A consumer that
finds a tile by `display` must not hard-code the contract: ask
`front_month("/ES").display`, as `tools/nq_instruments.py` does. The roll offset
is `[futures] roll_days_before_expiry` in `config/symbols.toml` (default 8),
read on every call.

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
| `feeds` | `compute.poll_now` | `[collector]`: `rth_poll_min` 2 (08:30–15:00 CT on a trading day), `eth_poll_min` 5 (06:30–08:25 and 15:00–15:15 CT), `offhours_poll_min` 30 (a trading day outside those), `weekend_poll_min` 30 (weekends, NYSE holidays); counted from when the last poll **ended**; never faster than `MIN_INTERVAL_S` (60 s) |
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

## Blog service — :8217

**Entry:** `services/blog_svc/app.py` (`make_app("blog", scheduler=scheduler.loop,
command_handler=handlers.handle_command, on_dropped=handlers.on_dropped)`). Owns the
site Blog's drafts and entries and is the only writer of the blog's public files
under `deploy/site/`. **No Schwab call, no Claude call, no proxy.** The one request
that leaves the box is the typeface copy at upload, to `fonts.googleapis.com` and
`fonts.gstatic.com` only. Design: `docs/plans/2026-10-06-site-blog-design.md`;
detail: `docs/reference/blog.md`.

The design also describes a connector (a second stream, `cmd:blog_inbox`, fed by a
separate gate process). **It is not built**, and this service reads `cmd:blog` only.

### Commands (`cmd:blog`)

Built by `shared.blog_inbox` and sent by the private Blog page. Every command
carries a `request_id` (16 hex characters, `blog_inbox.new_id()`); one without a
usable id is dropped, because nothing could be told how it ended.

| `type` | `args` | Effect |
|---|---|---|
| `draft_submit` | `request_id`, `source`, `html`, `fields` `{title, summary, tags, slug}`, and optionally `revises` (the address of the entry this replaces) | The document is cleaned, its typefaces copied, and the result stored as a draft. Never writes to the site. `source` is ignored: the service stamps `upload`. |
| `publish` | `request_id`, `draft_id`, `fields` | The draft becomes an entry (or replaces the entry it revises), the draft is deleted, the site is rebuilt. A title is required. A new entry needs an address `clean_slug` accepts; a replacement keeps its entry's address. With `[site] enabled = false` the entry is stored and the answer says nothing was written to the site. |
| `discard` | `request_id`, `draft_id` | The draft and its staged document are deleted. Nothing public changes. |
| `unpublish` | `request_id`, `slug` | The entry is removed and the site rebuilt. With `[site] enabled = false` the entry is removed from the store and the answer says its page is still on the site; it comes down at the scheduler's next pass after the site is switched back on. |

A replacement draft (`draft_submit` with `revises`) takes its tags from the entry
it replaces when the command's `fields` carry none; its title and summary are the
new document's.

`blog_inbox.submit_command(html, fields, *, source, request_id, revises=None)`
builds the first. `revises` is the address of the entry being replaced: left out,
the command has no `revises` key; given, it must be an address `existing_slug`
accepts or no command is built. `blog_inbox.owner_command(kind, request_id, **args)` builds the other three, and
returns `None` for an unknown kind, a bad id, an unusable target, or an argument
the kind does not take. The service runs every validator again on what it reads.

⚠ `handle_command` never raises. The scaffold dead-letters a command whose handler
raised, and a `draft_submit` carries a whole document.

### Views

`cache:blog:*` is readable by the public live process. **No view carries a
document.**

**`cache:blog:drafts`** — `{"drafts": [row, …]}`, newest first. Written
`skip_unchanged`, so it holds no timestamp of its own.

| Field | Type | Meaning |
|---|---|---|
| `id` | str | The draft id, 16 hex characters |
| `source` | str | `upload` in this build |
| `revises` | str | The address of the entry this replaces, or `""` |
| `slug` | str | The proposed address |
| `title`, `summary` | str | As typed, else from the document's `<title>` / first `<h1>` and first paragraph |
| `tags` | list[str] | At most `[limits] max_tags` |
| `removed` | dict[str, int] | What cleaning took out, by kind: `script`, `form`, `handler`, `link`, `css`, `img`, `iframe`, `href` and any other removed element's name |
| `font_note` | str | `""`, or one sentence saying which typefaces were not copied |
| `bytes` | int | The staged document's size |
| `digest` | str | SHA-256 of the staged document; the preview serves only the file that matches |
| `received_at` | str | ISO, UTC |

**`cache:blog:posts`** — `{"entries": [row, …]}`, newest first publication first (a
revision does not move an entry up). `skip_unchanged`. Each row: `slug`, `title`,
`summary`, `tags`, `published_at`, `updated_at`.

**`cache:blog:social_cards`** — `{"cards": [row, …]}`, one row per published entry
that has a card on the site. `skip_unchanged`. A row is `shared.social_cards.row`:
`key` (`blog:<address>`), `kind` (`blog`), `at` (the publish time), `title`,
`caption`, `link` (the entry's address), `png` and `jpg` (paths under the site
folder) and `listed` (true when the post is on or after `[cards] listed_from`;
the Social Cards page shows only listed rows). The sibling view
`cache:options:social_cards` has the same rows for posted trade ideas, plus
`site_enabled`: false when trade ideas are not published to the site, in which
case `cards` is empty.

**`cache:blog:result`** — how the last command ended. Written plainly, so the same
answer twice is two events.

```json
{"request_id": "…", "command": "publish", "ok": true,
 "message": "The entry is published.", "draft_id": "…", "slug": "an-entry"}
```

Always all six keys; an id or address that is not one is `""`. `message` is one of
the service's own whole sentences (`handlers.MESSAGES`), never an exception's text.
A reader must match `request_id` against a request it sent: the view holds the last
answer whoever sent the command.

### `shared.blog_inbox` — the shared surface

Imports stdlib, `repo_paths` and `shared.config_toml` only (pinned by
`shared/tests/test_blog_inbox.py`), so Tier 1 may import it.

| Name | What it is |
|---|---|
| `OWNER_DOMAIN` (`"blog"`), `VIEW_DRAFTS`, `VIEW_POSTS`, `VIEW_RESULT` | The stream's domain and the three view names |
| `new_id()`, `is_id(raw)` | A fresh request or draft id; whether a string is one |
| `submit_command`, `owner_command`, `OWNER_KINDS` | The command builders |
| `html_ok(html)` | A non-blank `str` of at most `max_html_kb` in UTF-8 bytes |
| `clean_fields(raw)` | Always `{"title", "summary", "tags", "slug"}`, each cut to its limit, invisible characters removed; unusable is `""` or `[]` |
| `clean_slug(raw)`, `existing_slug(raw)`, `slugify(title)` | A NEW address held to `slug_chars`; an EXISTING entry's, held to the limit's ceiling; an address made from a title |
| `SLUG_RE`, `RESERVED_SLUGS`, `ID_RE`, `FONT_NAME_RE` | The patterns. Each ends in `\Z` |
| `ENTRY_SANDBOX`, `ENTRY_CSP` | The frame's `sandbox` tokens and the policy sent on an entry document (`img-src` is `data:` only). One definition for the site writer, the Caddyfile generator and the private preview |
| `STAGING_DIR`, `PUBLISHED_DIR`, `FONTS_DIR`, `DOC_NAME`, `NEXT_NAME` | The store's folder and file names |
| `read_document(folder, digest)`, `read_font(fonts_folder, name)`, `font_name_for(data)` | The only two ways to read a stored file from outside the service, and the name a typeface's bytes are stored under |
| `limits()`, `site()`, `fonts(over=None)`, `BOUNDS`, `DEFAULTS`, `reset_cache()` | `config/blog.toml`, validated |
| `INBOX_STREAM`, `revise_command`, `answer_view`, `INBOX_TYPES`, the `"chat"` member of `SOURCES` | Parked with the connector. Defined and tested; nothing in this build uses them, and `INBOX_STREAM` has no stream cap (see *the cap is per stream*) |

### On disk

```
services/blog_svc/data/            repo_paths.BLOG_DATA (gitignored; in the nightly backup)
  blog.db                          the rows
  staging/<draft id>/entry.html    a draft's cleaned document
  published/<slug>/entry.html      an entry's document
  fonts/<20 hex>.woff2             one pool of typefaces, named by content

deploy/site/                       repo_paths.SITE_ROOT (the blog paths below are gitignored)
  blog.json                        {"updated", "entries": [{slug, title, summary, tags, published, updated}]}
  blog/<slug>/index.html           the entry's page: the site menu around a sandboxed frame
  blog/<slug>/entry.html           the entry document
  blog/fonts/<20 hex>.woff2        the typefaces published entries use
  blog/sitemap.txt                 the entries' addresses
```

The data folder is the source of truth; the site folder is rebuilt from it. While
a replacement is being put in place, the current document can be under
`entry.html.next`: read with `read_document`, which picks the file whose SHA-256
is the row's, and never by opening `entry.html`.

### Scheduler and health

`scheduler.loop` wakes every 30 s. First pass: `store.repair()`, a full site
rebuild, the two list views. Then every `[site] republish_min` (30): the rebuild
again (idempotent: it rewrites only what differs from the store, which is how a
site switched back on catches up) and the views again. `/health`
degrade areas: `blog.handlers`, `blog.clean`, `blog.clean.too_slow`,
`blog.clean.unsettled`, `blog.fonts.fetch`, `blog.fonts.reader`,
`blog.fonts.apply`, `blog.fonts.guard`, `blog.store`, `blog.site`, `blog.repair`,
`blog.scheduler`.

### The private preview routes (webgui)

| Route | Serves |
|---|---|
| `GET /blog/preview/{draft_id}/entry.html` | A waiting draft's cleaned document, with `Content-Security-Policy: <ENTRY_CSP>` and `Cache-Control: no-store`. 404 unless `cache:blog:drafts` lists the id and a file with the listed digest is on disk. |
| `GET /blog/preview/fonts/{name}` | One typeface as `font/woff2`. 404 unless the name matches `FONT_NAME_RE` and the file's content hashes to it. |

Both are behind the login and exist only in the private process.

### The private Social Cards routes (webgui)

| Route | Serves |
|---|---|
| `GET /social` | The Social Cards page: the cards from `cache:blog:social_cards` and `cache:options:social_cards`, newest first, over the X composer. `GET /x` answers 308 to it. |
| `GET /social/card/{key}/{fmt}` | One card's image, `fmt` `png` (`image/png`) or `jpg` (`image/jpeg`); `?download=1` sets `Content-Disposition: attachment`. 404 unless the key is a listed card and the file is the one that row names under the site folder. |

Both are behind the login and exist only in the private process.

---

# Schwab Proxy

**Entry:** `schwab-proxy/schwab_proxy.py`, port **8100**. Central token manager +
HTTP gateway. Market-data calls are spaced ~200 ms apart (5 a second) and
retried up to 3× with backoff (0.25 / 0.5 / 1.0 s). Every call to Schwab is a
GET except one: **no route places an order**. `trader_request` refuses any
method but GET, and the one write, `POST /orders/preview`, only asks Schwab to
check an order (below).

**Request header: `X-Priority: 1`** on `/chains`, `/quotes` or `/quote` — send
this request ahead of ordinary ones that are waiting for a slot. It is for the
one-minute collection poll (`gex_collector`, through
`get_option_chain(..., priority=True)` / `get_quotes(..., priority=True)`).
After `[limiter] priority_run` marked requests (4 as shipped) have gone ahead of
a waiting ordinary one, an ordinary one is sent; `0` switches the lane off. The
rate is unchanged. A request answered from the local store never waits.

## Health & auth

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/health` | GET | `{status, has_token, token_expired, refresh_token_expired, refresh_token_rejected, refresh_token_expires_at, refresh_token_hours_left, account_routes, token_file, timestamp}`. `refresh_token_hours_left` is what the daily sign-in check reads (null when unknown); it counts down from 168 at the sign-in and a token refresh does not reset it. `account_routes` is `locked_no_secret` (every caller refused) or `secret_required` |
| `/auth` | GET | HTML OAuth login page |
| `/auth/callback` | GET | Exchanges the OAuth `code`/`url` for tokens |
| `/auth/info` | GET | `{auth_url, callback_url}` — Schwab's login link for this app, for the web app's Authorize dialog. Carries the app key (a public client id), never the secret |
| `/auth/exchange` | POST | Body `{"url": "<the address Schwab redirected to>"}`. Signs in and answers `{"ok": true}`; `{"ok": false, "error": ...}` with 400 when the address holds no code, 502 when Schwab refuses it. A POST so the one-time code is not in the access log |
| `/stats/api_calls` | GET | `{today, last_7_days, last_30_days, since, store, store_degrades}` — calls sent to Schwab per day, plus today's request breakdown (see *Local market-data store* below) |

## Market data

| Endpoint | Params | Returns |
|----------|--------|---------|
| `/quote` | `symbol, maxAge?` | Single quote |
| `/quotes` | `symbols` (comma-sep), `maxAge?` | Quotes, keyed by symbol |
| `/chains` | `symbol, contractType, range, fromDate?, toDate?, strikeCount?, maxAge?` | Options chain |
| `/pricehistory` | `symbol, periodType, period, frequencyType, frequency, needExtendedHoursData, maxAge` | Price bars. `maxAge` (seconds) is the oldest stored daily series the caller accepts; `0` forces a fetch. It can only tighten the store's own rules |
| `/instruments` | `symbol, projection` (e.g. `fundamental`) | `{instruments:[{fundamental, symbol, description, ...}]}` |
| `/passthrough` | `endpoint, params` | One of five market-data endpoints, matched exactly: `/expirationchain`, `/quotes`, `/instruments`, `/pricehistory`, `/chains`. Anything else is **400**. Needs `X-Proxy-Secret` when a secret is configured |

Each returns Schwab's JSON body as Schwab sent it, with one exception: a class
share. Ask for it with a dot (`BRK.B`, `BF.B`). The proxy sends Schwab the slash
form it requires (`BRK/B`) and names the symbol with a dot again in the answer,
so quote keys and each `symbol` field match what was asked. Option contract
symbols are unchanged. A failure is the upstream HTTP
status with `{"detail": ...}`. A token that cannot be made valid (none, or the
refresh token expired or rejected) answers **500** with a JSON `detail` naming the
error.

## Local market-data store

**File:** `schwab-proxy/market_store.py`. **Settings:** `config/marketdata.toml`
(Settings → Configuration → *Local market data*), read on every request, so a saved
change needs no restart. Design:
`docs/plans/2026-10-03-market-data-store-design.md`.

The proxy keeps what it fetches for `/quote`, `/quotes`, `/chains` and **daily**
`/pricehistory` in memory (nothing on disk, nothing in Redis; a restart starts
empty). Whether it answers from that copy is set by `mode`:

| `mode` | What the proxy does |
|---|---|
| `off` | Sends every request to Schwab. |
| `shadow` (**shipped**) | Sends every request to Schwab and returns Schwab's answer. It also records what `on` *would* have answered and whether that matched. No request is answered locally. |
| `on` | Answers a repeat request from the stored copy when the copy is fresh and covers the request. |

Anything else reads as `off`. Each store also has its own `enabled` switch
(`[chains]`, `[quotes]`, `[bars]`). Intraday price history always goes to Schwab.

**Request: `maxAge=<seconds>`** on `/quote`, `/quotes` and `/chains` — the oldest
stored answer this caller will accept. It is a hint, sent as text: a value that is
not a usable number (text, negative, NaN) means "none given" and never fails the
request with a 422. It is capped at 3,600. `maxAge=0` always fetches. With none
given the configured limit applies: chains `chains.max_age_sec` (45) while a session
is open and `chains.closed_max_age_sec` (1,800) while every session is closed;
quotes `quotes.max_age_sec` (5). `proxy_client.SchwabPyProxyClient.get_option_chain`
takes it as `max_age=`.

**Request header: `X-Caller`** — who is asking, for the per-caller counts. Both
client classes in `proxy_client.py` set it from the service's folder name (or
`TRADING_CALLER`); a caller using plain `requests` sets it with
`proxy_client.caller_label(name)`. The proxy keeps letters, digits, `_`, `.` and
`-`, at most 40 characters; a request without it is counted as `unknown`; a dev
checkout's label is prefixed `dev.`. One proxy process counts at most 64 distinct
names — a new name past that is counted as `other`.

**Response headers** on the four endpoints above, in every mode:

| Header | Value |
|---|---|
| `X-Store` | Answered locally: `hit` (the stored answer to this exact request), `subset` (a chain cut from a wider stored chain), `coalesced` (shared the fetch another identical request was already making), `composed` (a daily series whose bar for today was built from the live quote). Fetched from Schwab: `miss`, `partial` (quotes — only the symbols that were missing or too old were fetched), `pass` (the store played no part: mode `off` or `shadow`, an intraday series, or a store fault). |
| `X-Store-Age` | Seconds since the data left Schwab, one decimal. `0.0` for a fresh fetch. `proxy_client` exposes both as `FakeResponse.store_kind` / `store_age`. |

**Rules the store keeps.** A chain is cut to a narrower date window only when both
the stored chain and the request ask for every strike and both sides; a request with
`strikeCount`, a `range` other than `ALL` or one contract type is answered only by
its own exact stored copy. An entry stored in one market session is never served in
another. An entry past its limit is never served because Schwab failed — the error
is returned. An empty answer is never stored, and a cut that would hold no
expiration is never served.

**`/stats/api_calls`** gains more keys. `today` / `last_7_days` / `last_30_days` still
count calls **sent to Schwab** — a locally answered request is not in them. A
count that cannot be read is `null`, never 0. `?day=YYYY-MM-DD` returns `store`
for an earlier day (`store_day` names it; an unreadable day is a 400).
`market_store` is `{mode, stores, faults, faults_by_area}` — the same block
`/health` carries — or `null`. A chain request that just missed and whose
week-wide refetch failed records the outcome `wide_failed` before its own
`upstream`.

| Key | Contents |
|---|---|
| `store` | Today's requests: `served_locally` (total answered without a Schwab call), `by_outcome` (`{outcome: count}` over every row), and `rows` — `[{endpoint, caller, outcome, n}]`, the 500 largest. Outcomes: `upstream`, `hit`, `subset`, `coalesced`, `composed`, `partial`, and in shadow mode the `shadow_*` names (for example `shadow_hit_match`, `shadow_subset_mismatch`, `shadow_bar_match`, and for a daily series in the session `shadow_moving_same` / `_under_10bp` / `_under_50bp` / `_over_50bp` — how far the stored close for today sat from the fresh one). |
| `store_degrades` | `{area: count}` — store faults since this process started that fell back to a plain fetch. Empty is normal. |

## Trader API

Read-only, and closed unless the caller proves itself. Each route needs the
header **`X-Proxy-Secret`** equal to `PROXY_SHARED_SECRET`. With no secret
configured they answer **503** with a `detail` naming the variable (they refuse
everyone, where before 2026-10-03 they answered everyone); with a wrong or
missing header, **401**. `proxy_client._apply_secret(session)` attaches it. See
`docs/SECURITY.md`.

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/accounts` | GET | `[{hashValue, accountNumber, ...}]` |
| `/positions/{account_hash}` | GET | Normalized positions (net qty; options carry underlying) |
| `/transactions/{account_hash}` | GET (`start_date, end_date`) | Normalized TRADE transactions |
| `/orders/preview` | POST `{account_hash, order}` | Schwab's `previewOrder` answer, with Schwab's status code: it CHECKS the order and places nothing. **403** unless `config/live_orders.toml mode` is `preview` or `live` (read per request); **400** for a bad hash or an order over 16 KB |
| `/orders/place` | POST `{account_hash, order}` | PLACES the order: `{order_id, unknown, error}` with Schwab's status code. ONE attempt, never retried; a timeout, a 5xx or a 201 with no order id in `Location` sets `unknown: true` (settle it by listing the orders, never by sending again). **403** unless the mode is `live`; **400** as above. Its one caller is `options_svc` (`cmd:live_order place`) |
| `/orders/{account_hash}/{order_id}` | GET | Schwab's order (its `status`, legs, fills); **400** for an id that is not digits |
| `/orders/{account_hash}/{order_id}` | DELETE | CANCELS the order (`trader_cancel`): `{cancelled, error}`, `cancelled` true, false (Schwab refused) or null (no answer). ONE attempt. **403** only while the mode is `off`, so it still works in `preview`; **400** for a bad hash or id. Its one caller is `options_svc` (`cmd:live_order cancel`) |
| `/orders/{account_hash}` | GET (`from_time, to_time`, ISO UTC) | The account's orders entered in that window; how an `unknown` place is settled |

## Trade-stream tracker

Daemon threads register paper trades and stream option ticks (degrade gracefully if
streaming fails).

| Endpoint | Method | Body |
|----------|--------|------|
| `/track` | POST | `{trade_id, symbol, strategy, expiration, quantity, entry_credit, short_strike, long_strike, call_short, call_long, target_mid, stop_mid}` |
| `/untrack` | POST | `{trade_id}` |

Both routes need the **`X-Proxy-Secret`** header, like the Trader API (503 with
no secret configured, 401 with a wrong one). `/track` tracks the Ledger's own
open row for `trade_id`; the body's other fields are not trusted, and a
`trade_id` that is not an open Ledger trade is `refused`.

`/track` otherwise answers HTTP 200 with a `status`:

| `status` | Meaning |
|----------|---------|
| `ok` | The trade is tracked; `legs` maps each leg to its option symbol. |
| `skipped` | The tracker will not follow this trade, and `detail` says why: a structure other than a put credit spread, a call credit spread or an iron condor; a missing strike or credit; or an expiration already past. No option chain is fetched. |
| `error` | A failure that may clear (the chain could not be fetched, or a strike is not in it); `detail` says which. |

Every 30 seconds the proxy also reconciles against the paper ledger's open trades. A skipped trade is not tried again while it stays open. A failed one is tried again after 30 seconds, then 60, 120 and so on: up to 5 minutes when Schwab did not send the chain (an error status, or a Schwab sign-in that needs renewing), and up to 30 minutes otherwise. Both limits are in `config/marketdata.toml` under `[tracker]`. `GET /stats/api_calls` reports the tracker's state as `tracker: {tracked, not_followed, failing}`.

## Streaming (server-sent events)

Two read-only streams fan level-one ticks out from the proxy's one Schwab
streaming session. Each is a long-lived `GET` answering `text/event-stream`: one
`data: {json}` event per tick, and a `: keepalive` comment after 15 seconds of
silence. A consumer should treat about 45 seconds with nothing at all as a dead
connection and reconnect.

| Endpoint | Query | Tick |
|----------|-------|------|
| `/stream/quotes` | `symbols=SPY,QQQ` | `{symbol, last, net_change, bid, ask, bid_size, ask_size, last_size, total_volume}` |
| `/stream/options` | `symbols=<contract symbol>,…` | `{symbol, last, last_size, bid, ask, total_volume}` |

- Every value is a float or `null`. **A tick after the first carries only the fields
  that changed**, so a consumer must merge ticks per symbol.
- A contract symbol is Schwab's 21-character form and is case- and space-sensitive:
  `SPY   261009C00770000`. A symbol Schwab does not know is accepted without an
  error and never carries a real quote, so a typo looks subscribed.
- `total_volume` is the day's cumulative volume. Level one merges rapid trades, so
  `last_size` is one trade of several; the **change** in `total_volume` is the only
  complete size.
- The whole set travels in the query string. Keep it to a few hundred contracts
  (`options_svc` never sends more than 500), and remember Schwab's own limit of 3,000
  streamed option symbols, shared with paper-trade tracking.
- A flow subscription can never drop a leg the paper-trade tracker is following.

Consumers today: `portfolio_svc` and `sentiment_svc` (`/stream/quotes`),
`sentiment_svc` and `options_svc/flow_stream.py` (`/stream/options`).
`services/_sse.py` has the line parser and the reconnect backoff.

---

## The Market read view

**`cache:market:read`** is one reading of the Desk's scorecard, replaced on each clock
slot in the regular session:

```
{enabled, date, ts, slot, interval_min, glow_min, next_slot, final, public, tally, rows, history}
```

- `enabled: false` is a **retraction**: the operator switched the Market read off. It
  has no rows, and a reader must show nothing, not "no reading yet".

- `slot` and `next_slot` are `"HH:MM"` Central; `next_slot` is `null` after the close
  reading, which has `final: true`.
- `glow_min` is how many minutes the Desk's two header buttons glow after a new
  reading or report (`config/market_read.toml`); `0` = never.
- `tally` is `{tailwind, headwind, neutral, none}` counts.
- `rows` is seven objects in a fixed order — `direction`, `breadth`, `structure`,
  `volatility`, `flow`, `cross_asset`, `volume` — each `{key, verdict, facts, prev}`. `verdict`
  is `tailwind`, `headwind`, `neutral` or `none`. **`none` means no reading and must
  never be shown or counted as `neutral`.** `facts` are the numbers the verdict was
  decided from, and differ per row; `prev` is the same row's `{verdict, facts}` at the
  previous slot, or `null`. The `flow` row carries `estimate: true` and its own
  `public`, which is the bought/sold estimate's switch and can be `false` while the
  reading's is `true`. The `structure` row's facts carry `stale: true` when the dealer
  levels were not current.
- `history` is `[{slot, verdicts: {key: code}}]` for the day.

A reader must check `date` against today, treat a reading older than two
`interval_min` in the session as stopped, and on the public origin show it only when
`public` is exactly `true`, and a row that carries its own `public` only when that is
exactly `true` too (recounting `tally` without it). The rules and thresholds are in the Technical Reference,
*Market read*.

## The fund holdings view

**`cache:market:holdings`** (`MarketHoldings`) is built by
`services/market_svc/holdings.py` from `config/etf_holdings.toml`, every
`rth_refresh_sec` (15) seconds in the session and `offhours_refresh_sec` (300)
outside it, with ONE proxy `/quotes` request for every holding at once:

```
{enabled, ts, as_of, funds: {<fund>: {rows: [{symbol, name, last, change_pct, stale, traded}], note}}}
```

- `<fund>` is the tile's display symbol. The Macro Board draws a holdings card on any
  tile whose symbol is a key here, and the one-line description on every other.
- `change_pct` is the quote's own percent change from the previous close, as a tile's.
- `stale` is true when the line's last trade is older than `stale_after_h` (96):
  `change_pct` is then `null` and `traded` is that trade's `YYYY-MM-DD`. A line with
  no trade on record at all is `stale` with `last`, `change_pct` and `traded` all
  `null`. A holding Schwab returned nothing for has `last` and `change_pct` `null`
  and `stale` false.
- `as_of` is the date the lists were checked by hand; `ts` when the quotes were fetched.
- Switched off (`enabled = false`), the view is published once as `{enabled: false,
  funds: {}}` and the cards go.

## The traded volume view

**`cache:market:volume`** (`MarketVolume`) is built by
`services/market_svc/volume_read.py`, every 5 minutes in the session and once for each
other part of the day:

```
{enabled, date, ts, phase, session_date, through, symbols, sectors, range_hint, statements}
```

- `date` is the Central day it was built. `session_date` is the session its figures
  describe, an **earlier** one when `phase` is `before_open` or `closed` (`session` and
  `after_close` are the other two).
- `through` is `"HH:MM"` Central, the end of the last finished five-minute bar.
- `symbols` is `[{symbol, pace, up_share, word, bars, baseline_sessions}]`. `pace` is
  volume so far over the median of the last `baseline_sessions` sessions by the same
  time of day. `up_share` is volume in rising bars over volume in rising and falling
  bars. `word` is `heavy`, `usual`, `light` or `null`.
- `sectors` is `{up_share, funds}`, the share of the dollars traded across the sector
  funds in rising sectors, or `null`. It is present only in the session and at the close.
- `range_hint` is `wider` or `narrower` when every symbol reads heavy or every one
  reads light, in the session only, else `null`.
- `statements` are the figures as finished sentences. The market reports print them
  word for word.
- **A missing figure is `null`, never 0**, and must not be shown as light volume.
- ⚠ `pace` and `up_share` are on the basis of the five-minute bars, and a sector's
  `totalVolume` is not: a bar series sums to about 0.6 to 0.7 of a daily bar. Do not
  compare across them.

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
cache:options:flow_sides       events:options:flow_sides    (bought/sold estimate per contract alert, today)
cache:options:flow_followup    events:options:flow_followup (previous session's flagged contracts + next-day open interest)
cache:options:hiro             events:options:hiro          (HIRO-model hedging flow, per-symbol summary; no reader yet)
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
cache:feed:shadow                                           (config/feed.toml mode "tee" only: the shadow check's counts and timings)
cache:options:x_log                                         (every X post attempt, newest first, last 100)
cache:options:x_count                                       (X posts made today, CT day - the daily cap)
cache:options:x_reports                                     (market reports already posted to X)
cache:options:social_cards     events:options:social_cards  (posted trade ideas' social cards; site_enabled)
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
cache:options:scalp           events:options:scalp         (Opening Scalp; ScalpView contract)
cache:options:futures_chain:<FOP.ES | FOP.NQ>   (the newest /ES or /NQ chain, adapted; no event)
cache:options:futures_chain:<FOP.ES | FOP.NQ>:frozen   (the last capture far enough before expiry)
cache:options:futures_chain_status   (the feed's health, per instrument, and the PC's report)
cache:options:futures_chain_alert    (what the stopped-capture alert has told the operator)
cache:options:futures_chain_desk     (the private Desk's /ES and /NQ rows)
cmd:futures_chain                    (the PC's snapshots, over ssh through tools/futures_chain_cli.py)
cmd:options
```

**`cache:options:flow_alerts`** is `{date, alerts: [...]}`, oldest first, capped at
300, today only. Each alert carries `type` — `crossover`, `uoa`, `gamma_flip`,
`big_delta`, `hiro_surge` or `hiro_flip` — plus `side`, `symbol`, `ts`, `id` and
`text`. The two `hiro_*` types also carry two flags the service stamps from
`[hiro]` in `config/flow_alerts.toml`: **`quiet`** (true while `push` is off — not
pushed, and the Desk does not speak it) and **`public`** (false while `public` is
off — hidden on the public live screens). A reader that
shows flow-alert rows must go through `webgui/pages/options/flow.alert_rows` (its
`_shown` filter does the hiding); an alert with no `public` key is shown. The
`hiro_*` alerts are not counted in `flow_alert_cooldowns`-derived counts (Hotness,
`n_alerts`) while the model is unvalidated. A `uoa` or `big_delta` alert also carries
**`osi`**, the contract's own Schwab symbol (`null` when the chain row had none).

**`cache:options:flow_sides`** is `{date, public, contracts: {<alert id>: {poll,
stream, volume, at_alert, side, osi}}}`. `side` is `call` or `put` and `osi` is the
contract's symbol, so a reader that pools the estimate needs this view alone. `poll`, `stream` and `at_alert` are each `{bought,
sold, unlabelled}` in contracts; `stream` is `null` until the contract has been
streamed; `volume` is the contract's volume for the day and equals the sum of
`poll`. `at_alert` is `poll` as it stood the minute the alert fired and never
changes; it is `null` when that is not known. `poll` minus `at_alert` is the poll's
reading over the window `stream` covers. It is an **estimate**
(see the Technical Reference, *Bought / sold estimate on flow alerts*). Three rules
for a reader:

- Join it to `flow_alerts` by alert id **and check that `date` matches**: it is
  published only once a contract is flagged, so a quiet morning still holds
  yesterday's.
- It is written with `skip_unchanged` and carries no timestamp of its own.
- On the public origin show it only when `public` is exactly `true`. Go through
  `webgui/pages/options/flow.sides_view_shown`; a missing flag is closed.

**`cache:options:flow_followup`** is `{date, public, rows: [...]}`, where `date` is
the **previous** session and each row is a `flow_contract_days` row: `session_date,
alert_id, symbol, osi, side, strike, expiry, alert_type, fired_ts, oi_prev, volume,
poll_bought, poll_sold, poll_unlabelled, stream_bought, stream_sold,
stream_unlabelled, at_bought, at_sold, at_unlabelled, oi_next, oi_next_date,
verdict, oi_ratio`. `verdict` is `opened`,
`closed`, `mixed`, `expired`, `none`, or `null` while the next open interest has not
been read. The same `public` rule applies.

**`cache:options:hiro`** is `{date, symbols: {SYM: {ts, spot, impact, cum,
window_impact, sigma, mult, unclassified_share}}}`, `ts` being that symbol's newest
stored minute. Gate on `date` **and** each symbol's `ts` against the clock — a stalled
collector also leaves a today-dated view.

**Trade / Portfolio / Market:**

```
cache:trade:analysis           events:trade:analysis          (TradeAnalysis)
cache:trade:deepdive           events:trade:deepdive
cache:trade:deepdive_query     events:trade:deepdive_query
cache:trade:markov_prior       cache:trade:universe_factors
cache:portfolio:positions      events:portfolio:positions     (PortfolioModel)
cache:market:dashboard         events:market:dashboard        (MarketDashboard)
cache:market:summary           events:market:summary          (MarketSummary)
cache:market:read              events:market:read             (MarketRead - the Desk's seven readings)
cache:market:volume            events:market:volume           (MarketVolume - traded volume, also read by the market reports)
cache:market:holdings          events:market:holdings         (MarketHoldings - the Macro Board's fund hover card)
cache:news:feed                events:news:feed               (headlines - every kind but the SEC ones)
cache:news:feed_public         events:news:feed_public        (public feeds only - a live-origin key)
cache:news:sec                 events:news:sec                (Form 4s and offering filings)
cache:news:sec_public          events:news:sec_public         (public feeds only - a live-origin key)
cache:news:calendar            events:news:calendar           (the economic calendar)
cache:news:calendar_public     events:news:calendar_public    (dividends cut to the collection list - a live-origin key)
cache:news:calendar_status     events:news:calendar_status    (private: per-source errors)
cache:news:status              events:news:status
cache:blog:drafts              events:blog:drafts             (drafts waiting - metadata and a digest, never a document)
cache:blog:posts               events:blog:posts              (published entries - metadata)
cache:blog:social_cards        events:blog:social_cards       (blog entries' social cards - paths and captions)
cache:blog:result              events:blog:result             (how the last cmd:blog command ended)
cmd:trade   cmd:portfolio   cmd:market   cmd:news   cmd:blog
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
3. **Health** — `GET /health` → `{"domain": ..., "up": true}` plus the keys listed under
   *Per-Service Reference*. A service may add keys of its own with
   `make_app(health_extra=lambda bus: {...})`; it can add and never change one the body
   already has, and a function that raises is counted in `degrades`.

> **Process isolation matters.** Several app folders expose same-named top-level
> modules (`config`, `scoring`, `notifier`, `src`). Running two service test suites
> in one process re-triggers those collisions — run service suites **one folder at a
> time**.

---

# End-to-End Flow Example

{{diagram: request-flow | One request, from a click to the repaint}}

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
| blog_svc | 8217 | `SERVICE_PORTS["blog"]` |
| webgui (NiceGUI) | 8500 | `NICEGUI_PORT` / `NICEGUI_URL` |
| webgui_live (public screens) | 8501 | `NICEGUI_LIVE_PORT` / `NICEGUI_LIVE_URL` |

> The web GUI is on **8500**. (The retired React frontend's `dashboard_frontend = 5173`
> entry was removed from `config/ports.toml` in September 2026.)

## Environments — the ports above are the *prod* profile

Two checkouts of this repo run simultaneously on one machine. `repo_paths.py`
resolves the identity and every port consumer follows it with no edit of its own:

| | prod | dev |
|---|---|---|
| `[services]` ports | 8210–8213, 8215–8217 | **9210–9213, 9215–9217** (`port_offset`) |
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
