# Observability and performance

> Moved here VERBATIM from `CLAUDE.md` on 2026-10-04 (audit CQ-09), when
> that file was cut from 327 KB to under 100 KB. `CLAUDE.md` keeps the rules;
> this file keeps the detail behind them: the incident, the measurement and
> the reasoning. It is a living reference. Correct it in place, exactly as
> the rules in `CLAUDE.md` say, and never append a correction under a wrong
> sentence. Its section headings are the ones `CLAUDE.md` used.

## Observability — a swallowed exception must leave a trace

**`services/_degrade.py` is the house guard-rail for the repo's most expensive bug
class**: `try/except Exception -> return a plausible default`, which turns a real
bug into a confident number with nothing in the log to say it happened. That shape
sat over all five NaN incidents, and the worst instance wrapped **294 lines** of
`sentiment_svc.compute_intraday_trend` and returned `_neutral_trend()` — so any bug
inside it rendered as a calm neutral reading.

```python
except Exception:                       # the guard STAYS - it keeps the refresh alive
    _degrade.degraded("sentiment.compute_intraday_trend")
    return _neutral_trend()
```

`degraded(area, *, detail=None)` logs at **WARNING with a traceback** and increments
a per-area counter that `_scaffold`'s `/health` publishes as **`degrades_total`** +
**`degrades`**. The Status page renders it on the service card as
`healthy - 12 degraded` (`status.service_detail`, zero stays a plain "healthy").
WARNING and not ERROR on purpose: most of these fire on real, expected conditions
(a symbol with no chain off-hours), so ERROR should stay meaning "look now" — **the
counter is the signal, the log line is the detail**. One degrade is noise; 340 in a
session is a bug that had nowhere else to surface.

**The scope rule is by SIZE, and it is deliberate** (census 2026-08-21, 542
`except Exception` in `services/` + `webgui/`, 289 of them silent):

| guarded body | policy |
|---|---|
| **>= 15 lines** (41 found) | must speak — `_degrade.degraded(...)` or its own log line. Pinned by `services/tests/test_no_silent_degrades.py`, which reads the handler's CALL NODES (text that merely mentions a log does not count) and also walks `options-scanner/` and `shared/`. |
| **< 15 lines** (248 found) | leave alone. These are one-statement parse guards (`try: return float(x) except: return None`) where the missing-value contract IS the point; a WARNING per row per tick is spam, not observability. |

⚠ **Do not "just enable ruff BLE001" instead.** It flags every `except Exception`,
so it would need ~542 grandfathered `noqa` comments — diluting the signal to
nothing — and it contradicts the standing rule that a new ruff rule class is added
only once the tree is already clean under it. The AST guard test above pins the
invariant that actually matters at a fraction of the noise. Note the existing
`# noqa: BLE001` comments scattered in the code are **decorative** — `BLE` is not in
the ruff select list, so nothing checks them.

**Tier 1 is out of scope for the counter** — `webgui/` cannot import `services.*`.
Its guards are all small, and its one large one (`status._probe_one`) is a health
probe that already surfaces the failure **in the UI** as `unreachable
(ConnectionError)`, which beats logging it.

**`webgui/logging_setup.py`** is Tier 1's own rotating file log (`logs/webgui.log`),
called once at `main.py` startup. It is a deliberate ~30-line copy of
`_scaffold._install_file_logging` rather than an import of it — the Tier-1
allow-list has no `services.*`, and that helper drags in FastAPI and the Bus.
Before it, the `webgui` logger had **no handler at all**: output went to the console
and died with the Windows Terminal tab.

## Performance characteristics & known hotspots

Single-user, localhost Redis — so most of these are *tolerable today* but are the
real levers if a page feels sluggish or a service churns CPU/network. Audited
2026-06-19; ranked by impact. Fix the High items first if optimizing.

**2026-07-18 re-audit — all Critical + High findings FIXED (TDD, per-layer tests):**
- **GEX collection was silently dropping ~37% of its 1-min slots** (measured in the
  live DB: 151 exactly-2-min gaps on 2026-07-17) — the ~24 per-symbol chain fetches
  ran SERIALLY (15–35 s of the 60 s budget) and the scheduler `await`-gathered ALL
  branches before sleeping, so a 30–90 s rescan also swallowed following slots. Now:
  `gex_collector.poll_once` fetches chains in a small pool (`POLL_FETCH_WORKERS=6`;
  engine compute + SQLite inserts stay on the calling thread — conn affinity +
  `engine._last_dte` mutation), and `scheduler.launch_branches` replaces
  `_gather_due`: due branches launch as KEYED background tasks with a
  still-running skip (a branch can only ever delay ITSELF), so the tick returns to
  its 30 s sleep immediately. This also fixes the flow-alert spike detector
  silently mixing 1-min and 2-min volume increments (a 2-min delta reads ~2×
  baseline).
- **`gamma_snapshot` re-decoded the WHOLE session's heatmap grids every minute**
  (4 views × ~440 rows × full-chain JSON by the close — tens of MB/min, the
  service's largest CPU burn). Now incremental: `compute._history_rows_incremental`
  memoizes decoded rows per `(symbol, view, session-date)` (lock-guarded,
  date-evicted) and appends only rows with `ts > last-seen` via
  `gex_history_db.load_date_with_grid(since_ts=…)` (sargable on the PK). Safe to
  share because `_crop_gamma_views` REBUILDS row tuples (never mutates memo rows).
- **The same 1-min tick fetched the viewed symbol's chain TWICE** (poll_once, then
  `refresh_gamma_current` seconds later). Now `poll_once(on_chain=…)` hands each
  fetched chain to the caller; `collect_gex_history` captures the currently-viewed
  symbol's chain (`_current_gamma_symbol`) into a CONSUME-ONCE stash
  (`_stash_tick_chain`/`_take_tick_chain`, 45 s TTL) that `gamma_snapshot` pops —
  only the same-tick refresh reuses it; every other caller still asks the proxy.
- **The webgui watcher regressed twice as the app grew:** `_freshness_facts` was
  full-deserializing 4 payload envelopes (incl. `options:scan` a SECOND time) every
  2 s tick per tab — `cache_set` now writes a tiny `{key}:ts` side key (same
  pipeline as the SET; still refreshed when `skip_unchanged` skips the payload, so
  it means "last confirmed current") and
  `bus_client.read_metas` probes `:ver`+`:ts` for all views in ONE pipelined
  round-trip (pre-upgrade keys fall back to the envelope once). And `_tick` called
  `_refresh_health` unconditionally (a proxy HTTP GET every 2 s per tab, bypassing
  the TTL memo) — it now re-warms via `cached_health`.
- **Sentiment was the biggest background Schwab-API burner:** the 120 s composite
  refresh fetched 11 NTM sector chains every cycle (~3,300 calls/day) for a
  slow-moving cumulative P/C — now TTL-cached 15 min in `live_composite`
  (`PCR_TTL_SEC`; an empty off-hours result is NOT cached so the first post-open
  refresh picks up volume). And `compute_30d_trend` refetched SPY 12-mo + 11
  sector histories on every 15-min trend recompute (~1,150 calls/day) for a
  ~daily-changing structural gauge — the self-fetching path is now cached hourly
  (`TREND_30D_TTL_SEC`; explicit-args calls bypass the cache).

**2026-07-19 — the Medium + Low tier from that audit was REMEDIATED (TDD per item):**
- **Flow alerts** now load only the trailing ~22 rows per symbol
  (`gex_history_db.load_flow_tail` + `handlers` `tail_limit = spike window + 2`) instead
  of the whole day's series every minute, normalize the series **once** per symbol
  (`flow_alerts._crossover_rows`/`_spike_rows` share one `_norm`), **exclude `$VIX`** (its
  option premium crossovers are noise), `skip_unchanged` the cooldown-map write, and
  mtime-cache the thresholds TOML (`load_thresholds` — was re-parsed every tick).
- **Storage:** the redundant **`idx_snap_today`** index (an exact duplicate of the PK
  autoindex) is dropped in `init_schema`, and **`gex_json` grids are zlib-compressed at
  insert** (`_encode_grid`/`_decode_grid`, ~5× smaller — the ~470 MB/day dominant cost;
  the reader is format-agnostic so legacy uncompressed rows still decode). `init_schema`
  runs **once per process** (a `_GEX_SCHEMA_READY` latch), not every 1-min collect.
- **Term structure** polls every **5 min** now, not every 1-min slot
  (`gex_collector.TERM_POLL_INTERVAL_MIN`; it's the widest SPX chain in the system).
- **Rescue advisories** read a **light GEX-only context** (`compute._light_gex_context` —
  single chain fetch + `calc_all_from_chain` + walls) instead of a full `gamma_snapshot`
  (which also builds the projection band / term grid / flow series / history decode, all
  discarded).
- **`reprice_captured`** now clears the repricer chain cache first (was pricing captured
  marks + the 3×/day action-alert reprice off up-to-5-min-stale chains — a freshness fix).
- **Proxy:** the stats counter uses **WAL + `synchronous=NORMAL`** (drops the per-call
  fsync on the ~60-70 calls/min hot path), `_rate_limit` serializes its spacing (a dedicated lock
  then, `rate_gate.RateGate` since 2026-10-04; concurrent fan-outs no longer burst
  past 5 req/s → 429 risk), and the
  30 s reconcile logs INFO only on an actual change (else DEBUG).
- **market_svc:** the deep-weekend poll throttles to 60 s
  (`WEEKEND_INTERVAL_SEC` — futures closed), and `read_sector_pcr` is **version-gated**
  (deserializes the composite only when it changes, not every 2 s).
- **sentiment_svc:** the state-transition phone push fires **outside `_TREND_LOCK`**
  (was holding it ~25 s on a flip day) and `sector_pc_delta` **closes its connection**
  (was leaking ~26 handles/day).
- **webgui:** ticker / market poll payloads now read **off the event loop**
  (`run.io_bound`), the scanner builds its ~5,238 display rows **off the loop**
  (`_read_and_build` → `_apply_populate`), and page-build reads `options:scan` **once**
  per navigation (shared by `_recompute_badges` + `_acknowledge`, was 2-3×).

Consciously **not** done: the gamma-page `_render_view` figure build stays on the loop —
the server-side ±20-strike crop (C2/P2) already reduced it to ~11k entries (~10-30 ms once
per 120 s), and splitting the entangled async render isn't worth the regression risk; the
gamma page's now-redundant 120 s RTH refresh (with the server refreshing every minute) is
also left as a minor duplicate.

**Costing model (important, non-obvious):** the version is stored in a SEPARATE
tiny Redis key (`{key}:ver`, `INCR`'d by `cache_set`), so version-polls are now
**cheap probes** — `bus_client.read_version()` → `Bus.cache_version()` reads just
that int (no payload deserialize); `read_versions()`/`Bus.cache_versions()` batch
many in one pipelined round-trip (use it when a page polls several views — e.g.
Gamma). `read()`/`read_full()` still deserialize the full envelope (use only when
you actually need the payload). `Bus.cache_set(key, payload, event=…, skip_unchanged=…)`
(a) **skips the whole write + publish when the payload is the one already stored**
(`skip_unchanged=True` → no `INCR`/`SET`/publish, version unchanged → GUI poller
doesn't repaint; decided from a stored digest, `{key}:sig`, since 2026-10-04, so the
payload is NOT read back to be compared — a write that keeps no signature deletes
any older one), and (b) pipelines `SET`+`PUBLISH` into one round-trip when `event`
is given. The `SET` can't fold into the `INCR` (the envelope still embeds the version
for `cache_get`). options_svc header + gex_status use `skip_unchanged`; other periodic
republishers (sentiment 120 s, portfolio per-tick) still bump
unconditionally — opt them in the same way if they prove chatty.
⚠ **A `skip_unchanged` view must carry no timestamp of its own** — a `ts` in the
payload makes every write unique, so it never skips and every reader repaints
every poll. The news feed views are built that way and take "updated at" from
the `{key}:ts` side key (`bus_client.read_meta`, what `ui_kit.header(view=)`
draws); the payload envelope's own `ts` means "last changed".

**Every service shares the proxy's 5 req/s, so a scheduled chain burst must stay
off the quarter hours (2026-09-16).** The 1-min GEX poll fetches ~92 chains in
~30 s on a quiet minute, and anything else fetching chains in the same minute
slows it; past 60 s, `launch_branches` skips the next slot and the heatmap loses
that minute. The options autoscan starts `windows.scan.offset_min` minutes after
each quarter hour (2 as shipped: :02/:17/:32/:47, about two minutes of fetches
each), so sentiment's hourly sector P/C burst runs at **:38**
(`sentiment_svc.scheduler.SECTORS_MINUTE`) and the Income board at **08:52**.
Before scheduling a new chain fan-out, read the proxy's access log
(`journalctl --user -u trading-prod-proxy`) for that minute. The skip warning is
`scheduler branch 'gex' still running`. Other services' load never shows in
options_svc's own log. With `config/marketdata.toml` `scan.wide_fetch` on (it
ships off) the autoscan makes ONE chain request per symbol in place of three,
for every symbol not listed in `scan.wide_fetch_exclude`.

**No burst in the first minute after the hour or half hour (2026-10-06).** Schwab
answers "429 Too Many Requests" there and almost never at :15 or :45, whatever
this app sends. Measured: on 2026-10-02 the scan's first two minutes held 4,944
requests across the twelve hour/half-hour scans and 5,051 across the twelve
quarter scans, and all 41 refusals fell in the first group; 2026-10-05 and
10-06 repeat the pattern (60 and 70+). On 2026-10-06 the proxy's true send rate
was sampled once a second: the 10:45 scan held exactly 5 a second for over a
minute (247 in 60 s) with no refusal, while 11:00 drew nine. A refusal hits
every endpoint for 10-30 seconds and costs the collector its chains for that
minute. That is why the scan has a start offset. The refusals are counted by
`journalctl --user -u trading-prod-proxy | grep 'Too Many Requests'`.

**The one-minute poll's requests go FIRST (2026-10-04).** The collector marks
its chain and price requests (`X-Priority`, passed only to a client whose
`supports_priority` is literally `True`), and the proxy's `rate_gate.RateGate`
sends a marked request ahead of waiting ordinary ones: 4 of every 5 calls while
the poll is fetching (`config/marketdata.toml` `[limiter] priority_run`; 0 =
arrival order, read per request). Measured before it: 22 collection slots lost
over four sessions, 20 of them in the minute after a quarter-hour scan started.
The total rate is unchanged, so this reorders a busy minute and makes no room
for more chains — the rule above still stands. ⚠ The lane is the one-minute
poll's alone: a second marked caller competes with it at the front. ⚠ The mark
is a HEADER, never a request parameter, which would reach Schwab and change
which stored chain matches.

**The proxy can answer from memory, and six rules follow from it.**
`schwab-proxy/market_store.py` keeps what the proxy fetched — chains, quotes,
daily bars; memory only, empty after a restart — and `config/marketdata.toml`
`mode` decides whether it is used: `off` passes every request to Schwab,
`shadow` (the shipped value) still sends every request to Schwab and only counts
what it WOULD have reused, `on` answers repeats locally. Design:
[the doc](../plans/2026-10-03-market-data-store-design.md).

1. **A local answer is not a Schwab call.** With the mode on, a repeat
   `/chains`, `/quotes`, `/quote` or daily `/pricehistory` request is answered
   from memory and skips both `_rate_limit` and `api_call_counter.record` — so
   the per-day counter still means "calls sent to Schwab". Requests, local ones
   included, are counted apart by endpoint, caller and outcome
   (`api_calls_detail`). Intraday `/pricehistory` always goes to Schwab.
2. **A chain is cut to a narrower date window only between PLAIN requests** —
   every strike, both sides, a stated window (`ChainKey.plain`). A request
   carrying `strikeCount`, a `range` other than `ALL` or one contract type is
   exact-match only: the sector put/call ratio sums volume over the strikes it
   receives, so extra strikes would change the ratio.
3. **An entry never crosses a market-session change, and is never served past
   its limit because Schwab failed.** An empty answer is never stored and an
   empty cut is never served: for a window holding no expiration Schwab answers
   200, `status: "SUCCESS"`, both maps empty and `underlyingPrice: 0.0`
   (measured 2026-10-03), which a cut from a stored header cannot reproduce.
   `scanner_engine.slice_chain` writes that 0.0 itself, because the scan funnel
   reads the field.
4. **A new caller states what it needs.** One that must have a real fetch sends
   `maxAge=0`. A poller faster than the store's limit sends its own `maxAge`, or
   it re-reads its own previous answer (the Market Dashboard's 3-second poll
   sends 1; the collector sends `collection.fresh_max_age_sec` for every symbol
   due a real fetch, in EVERY mode, with or without a tail; a paper FILL and a
   Rescue apply send 0, and a position mark sends `[marks] chain_max_age_sec`
   from `config/paper.toml`; the trade-idea settlement sends 0 on
   `/pricehistory`, which takes `maxAge` too). A caller's limit only tightens.
   The store's own configured limits are clamped in the loader
   (`marketdata_config.AGE_CEILINGS`). A bare `requests.get` is counted as
   caller `unknown` unless it sets `X-Caller` through `proxy_client.caller_label`.
5. **The collector has two tiers once `collection.tail_interval_min` is above 1**
   (it ships 1, and needs the chain store on). CORE symbols — `config/symbols.toml`
   `[collection]`, the symbol open on Dealer Positioning, the public page's hot
   symbols, the hedging-flow (HIRO) symbols and the gamma-flip alert's symbols —
   get a real fetch every minute. A watchlist-only symbol gets one every Nth
   minute; in between, its stored chain is CARRIED (`chain_carry.carry_chain`:
   re-priced at the live quote, volume and premium as last fetched), written to
   all five views, and **not passed to `on_chain`**. So a new detector that reads
   volume must not assume one-minute data for watchlist-only symbols, and a new
   consumer that needs a minute-fresh chain for a symbol must make that symbol
   core in `compute.collection_tiers` (the code is `options_svc/collection_tiers.py`).
   A carried row is marked in storage (`snapshots.carried_age_sec`: NULL for a
   fetched row, the chain's age for a carried one; a study adds
   `gex_history_db.fetched_only_clause`). When the carry's gamma cap binds on a
   symbol, that symbol is fetched for real in the same poll
   (`collection.cap_refetch_max` symbols at most) and is then a fetched row.
   ⚠ **A symbol with NOTHING LISTED in the seven-day window is left out of the
   poll for `collection.empty_retry_min` minutes** (60; any tier, any store
   mode; `gex_collector._NOTHING_LISTED`, memory only). Only Schwab's own
   answer counts: 200, `status: "SUCCESS"`, both maps empty. The store never
   keeps an empty answer, so before 2026-10-07 such a symbol was fetched for
   real every minute whatever its tier (measured: eight monthly-only names,
   about 8 wasted calls a minute). A new consumer must not read "no rows
   this minute" for a symbol as a collector fault without checking
   `gex_collector.resting_symbols()`.
6. **A changed store rule goes through `shadow` before `on`.** On daily bars,
   `shadow_hit_match` compares every bar EXCEPT today's during the session — the
   one bar a stored series can be stale on — so read **`shadow_moving_same` /
   `_under_10bp` / `_under_50bp` / `_over_50bp`** for how far the stored close
   sat from the fresh one (they claim no saving). Shadow makes
   `on`'s decision with `on`'s limits and compares the answer with Schwab's. It
   counts low — it cannot reproduce the wider-window refetch or two identical
   requests sharing one call. The chain verdict is `market_store.chain_difference`:
   the stable header fields, the full expiration keys (date AND day count), the
   strikes, the contracts per strike and the header's count; the first mismatch
   per request logs what differed. In `on`, a failed week-wide fetch is followed
   by the request as asked (outcome `wide_failed`), and with `bars.session_spread`
   each daily series has its own reuse window inside `session_ttl_sec`. Daily bars have a `closing` period from the
   regular close until the bar settles: nothing is served from the bar store
   during it, so a series fetched before the close is never the day's bar after it.

**Measure before you optimise a localhost read — twice now the estimate was the
bug (2026-08-20).** The Desk's 11-view seed was audited as "~50-100 ms of event-loop
block"; measured against prod it is **10.7 ms**, only 6.2 ms of it JSON parse.
Deferring it off-loop buys ~10 ms and costs a fill-in flash on the landing page,
and **pipelining the round-trips is SLOWER** (12.34 vs 11.25 ms — on localhost the
round-trips are nearly free and the pipeline setup is not). Both were written,
measured and reverted. The lever that IS real is the *cadence*: the app-wide 2 s
watcher read `options:scan` + `options:flow_alerts` (237 KB) ungated on every
tick per tab — **3.16 ms → 0.32 ms, 10.2 GB → 0.7 MB moved per tab per day** —
via `bus_client.read_gated(view, memo)`, a `:ver`-probe-then-deserialize helper.
⚠ `read_gated` deliberately does **not** gate a key with no `:ver`: a memo keyed
on `None` has no invalidation signal and would serve its first payload forever,
so versionless keys keep the old always-read behaviour. **Rule: a per-navigation
read of a few hundred KB on localhost is single-digit milliseconds — optimise the
thing that runs 43,200 times a day, not the thing that runs once per click.**

**A cropped payload is not a BOUNDED payload — split what the reader doesn't read
(2026-08-20).** `cache:options:gamma` was cropped in 2026-06 to a ±display-window
strike range, and the comment recording that said it cut the key to "well under
~1 MB". Measured in prod it was **4.99 MB**: the crop bounds the STRIKE axis, but
the TIME axis keeps growing all session, so the four views' `history` blobs reach
~1.1 MB **each** (376 rows by the close) against ~400 KB for everything else. The
page draws ONE view at a time, so every open tab was deserializing four times what
it could possibly paint, once a minute. Each view's history now lives in its own
key (`handlers.gamma_history_key`, written by `_publish_gamma`); the page fetches
only the visible view's, on demand, cached per gamma version
(`gamma.history_key`/`history_rows`, mirroring the existing `netprem` pattern).
- **Main payload 4.99 → 0.40 MB (−92%); page read per bump 4.99 → 1.65 MB (−67%).**
- ⚠ **The write side does NOT improve during collection** — all four histories move
  every minute, so it is the same bytes plus a little key overhead. The write win
  appears only once collection stops, where the frozen histories now
  `skip_unchanged` and the refresh costs 0.40 MB instead of 4.99 MB. Claiming a
  uniform "75% both ways" would have been wrong; measure both directions.
- **Ordering is load-bearing:** history keys are written BEFORE the main payload,
  because the page reacts to the main key's version and then reads history — so
  history-already-written is the only skew it can see. Every history payload
  carries its **symbol**, and the reader refuses a mismatch: within one symbol a
  stale history is benign (append-only for the session), across symbols it would
  draw one symbol's heatmap under another's bars. A view the snapshot lacks is
  published EMPTY, never skipped, for the same reason.

**The same class, next key over (2026-08-20 evening):** `cache:options:calc_chain`
was **8.77 MB — 53% of ALL prod Redis string bytes** — because `calc_load_symbol`
cached the raw 20-expiry Schwab chain, ~40 fields per contract, no TTL. The
Calculator/Rescue pages read exactly FIVE contract fields (`bid`/`ask`/`mark`/
`volatility`/`delta`) plus the two expiry maps' structure, so
`compute.thin_calc_chain` now cuts contracts to that whitelist at publish:
**8.77 MB → 0.68 MB (−92%)**, measured on the real prod payload, page extractors
verified unchanged on the thinned dict. ⚠ **The whitelist grew to TEN fields on
2026-09-12** (adds gamma, theta, vega, openInterest, totalVolume) for the entry
panel's chain grid, and the same thinned chain is now also published as
`cache:options:sim_chain`; the resulting size (~1.3 MB estimated) is unmeasured on prod. Fields were cut rather than strikes —
the leg builder legitimately offers far wings, so the strike ladder stays whole.

**One more unbounded-growth fix from the same audit.** `publish_bullbear` full-deserialized
the 304 KB momentum payload (a **nightly** view) plus its own 190 KB output on
every ~30 s tick; both are now version-gated memos (`handlers.reset_bullbear_memos`),
taking the tick from three full deserializes to none: the last one was inside
`cache_set(skip_unchanged=True)`, which read the stored payload to compare it and
now reads a digest (`{key}:sig`).

**HIGH — webgui event-loop pressure (runs on *every* page):** *(FIXED 2026-06-19)*
- The app-wide 2 s watcher now runs its blocking bus reads **off the event loop**
  (`main._tick` is async → `run.io_bound(_watcher_compute)`); `_watcher_compute`
  reads `options:scan` **once** and passes it to `_recompute_badges(scan)` (no double
  read), and uses the **in-memory-cached** `app_settings.load()` (no per-tick disk
  read; invalidated on `set()`). Badge/chime UI work happens back on the UI thread
  after the await.
- `proxy.health()` is memoized for `_HEALTH_TTL_SEC` (`main.cached_health`) and
  re-warmed off-thread by the watcher tick, so a navigation no longer makes a blocking
  3 s-timeout HTTP call before first paint.
- `pages/options/gamma.py` coalesced its **four** 2 s version-polls into **one**
  `_poll` that reads all four versions in a single pipelined `read_versions(...)` call
  (cheap `:ver` counters) and dispatches only the changed views. (`status.py` remains
  the model citizen — blocking sweep via `nicegui.run.io_bound`.)

**HIGH — service-side serial proxy fan-out (biggest wall-clock wins):** *(FIXED
2026-06-19)* These I/O-bound proxy loops now fan out concurrently via
`services/_parallel.py:parallel_map` (services) / an inline `ThreadPoolExecutor`
(engine files). The proxy rate-limiter only *spaces* upstream calls ~0.2 s apart
(it does **not** hold a lock across the Schwab round-trip — see `schwab_proxy.py:
_rate_limit`), so concurrent calls genuinely overlap; pools are kept ≤8.
- Sentiment sector load — the 11 `get_daily_history` + 11 `/chains` loops in
  `sentiment_svc/compute.load_sector_perf` + `load_industries` (extracted to shared
  `_fetch_closes`/`_fetch_pcr` helpers), and the per-sector chains+history loops in
  `live_composite.compute_live`. *(The `_load_all_industries` outer per-sector loop
  stays serial — each iteration's inner fetches are now concurrent; flatten later if
  needed.)*
- Portfolio — `portfolio_svc/compute.compute_baselines` (per-symbol) and the
  `build_portfolio` holdings build (`portfolio-analyzer/src/portfolio.py`) fan out
  concurrently. **Plus:** baselines now recompute only when
  `compute.baseline_signature` (equity holdings + entries + day) changes — the
  periodic 10-min rebuild reuses cached baselines when nothing changed (`state.
  baseline_sig`), instead of re-fetching ~2N histories every cycle.
- Trade analyze — `_fetch_timeframes` (5 timeframes) and the independent
  SPY + sector-ETF + fundamentals fetches in `analyze` now run concurrently
  (after the early daily-sufficiency gate, so output is unchanged).

**HIGH — service-side per-tick churn (runs 24/7, no browser needed):** *(FIXED
2026-06-19)* `options_svc` `refresh_header` (a `get_quotes` proxy call + bridge read)
+ `publish_gex_status` (SQLite read) used to run on **every 30 s tick with no
market-hours gate** → ~2 proxy calls + 1 DB open + 2 cache writes every 30 s, all
day/all weekend. Now gated by `scheduler.periodic_refresh_due` — every tick during
market hours, throttled to once per `_OFFHOURS_INTERVAL_MIN` (5 min) off-hours/
weekends — **and** both use `cache_set(skip_unchanged=True)` so an unchanged view
writes/publishes nothing. The remaining per-tick proxy/DB churn outside market hours
is ~1/10th of before. *(Other services' serial fan-outs below are still open.)*

**MEDIUM — engine compute:** *(all FIXED 2026-06-19)*
- `shared/analysis_lib/technical.calculate_ema` is vectorized via `.ewm` (SMA seed +
  `ewm(alpha, adjust=False)` from the seed = the former loop's exact values, no Python
  loop). `macd_histogram_series` computes the histogram once; trade analyze reads
  `[-1]`/`[-2]` from it (was two `calculate_macd` calls = 4 EMA passes → 2).
  `volume_profile` buckets via `np.digitize`+`np.bincount` (was O(bars×bins) nested
  `iterrows`). Characterization tests pin numeric equivalence
  (`services/trade_svc/tests/test_technical.py`).
- `shared/bus.consume_commands` creates the consumer group **once** per (stream, group)
  per Bus (`self._groups`), not on every ~50 ms poll.
- Static workbooks: `sectors_ref.load_sectors_data` is now mtime-cached (`reset_cache()`
  for tests). (`watchlist.get_scan_symbols` was already mtime-cached — the `Top 20.xlsx`
  GEX-poll read only `stat()`s, never re-parsed.)
- `schwab_proxy.trader_request` routes through the pooled `token_mgr.session` (was bare
  `requests.*` → fresh TLS per `/accounts`/`/positions`/`/orders` call).
- `gex_history_db.load_today` / `load_today_with_grid` use a sargable
  `ts >= ? AND ts < ?` range (`_today_local_unix_range()`) so the `ts` index applies,
  instead of `DATE(ts,'unixepoch','localtime')=DATE('now')`. *(Not done: per-worker
  SQLite connection reuse — opening a read-only connection is sub-ms and sharing one
  across the service's executor threads would need `check_same_thread=False`+locking;
  deliberately left as a fresh connect per read.)*

**Already done right (don't "fix"):** all data pages version-gate repaints; Gamma and
Sentiment charts update Highcharts in place (`el.options=…; el.update()`, no flicker); `ui_guard`
suppresses dead-client callback noise; portfolio's SSE loop only republishes on a
`dirty` flag; the Redis connection and the marketdata Schwab session are pooled
singletons. **Hygiene:** stale `*.log.err` manual stderr captures are now
`.gitignore`-d (and the old ones removed).
