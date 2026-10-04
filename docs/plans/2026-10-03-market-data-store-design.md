# Local market-data store — evaluation and design

**Date:** 2026-10-03 · **Status:** approved 2026-10-03; code built and reviewed on the branch, shipped dark, not yet promoted · **Module:** `schwab-proxy/market_store.py` (new) + `config/marketdata.toml` (new) · **Plan:** [2026-10-03-market-data-store-plan.md](2026-10-03-market-data-store-plan.md)

**Decisions taken (operator, 2026-10-03):** the store is owned by the proxy;
the collector moves the watchlist-only symbols to a 3-minute fetch with the
minutes between carried forward locally; the autoscan may read a chain up to
45 seconds old.

## The question

Can one local raw store on the VPS become the source the rest of the app reads
and computes from, so that the app as a whole makes fewer Schwab API calls?

**Short answer: yes, and it is worth building, but it removes about a quarter of
the calls, not most of them.** A store removes calls that fetch data the app
already holds. 62% of today's calls fetch data nobody holds yet (a new chain
every minute, a new quote every three seconds). Only a cadence change reduces
those, and that is a separate decision listed at the end.

## What was measured

Prod, Friday 2026-10-02, from the proxy's own access log
(`journalctl --user -u trading-prod-proxy`) and its call counter. The counter
read **84,125** upstream calls that day (it was 68–76k in August).

| Endpoint | Requests | Share |
|---|---|---|
| `/chains` | 50,505 | 64% |
| `/quotes` + `/quote` | 15,653 | 20% |
| `/pricehistory` | 11,994 | 15% |
| everything else (positions, passthrough, accounts) | ~170 | <1% |

The proxy holds no response cache today and does not coalesce identical
concurrent requests. Its counter stores one number per day, so it cannot say
which caller or endpoint a call belonged to.

### Chains (50,505)

| Requests | Window and filter | Symbols | Caller |
|---|---|---|---|
| 40,345 | today → +7 days, all strikes | 92 | GEX collector, every minute 08:00–15:20 CT |
| 2,320 | today → +4 days, all strikes | 80 | autoscan 0-DTE window, every 15 min |
| 2,320 | +5 → +15 days | 80 | autoscan swing window |
| 2,351 | +20 → +45 days | 80 | autoscan volatility window |
| 1,874 | today → +30 days, near the money, 50 strikes | 79 | sector and industry put/call |
| 578 | today → +8 days, near the money, 40 strikes | 2 | order-flow contract list, every 5 min around the clock |
| ~480 | one expiration | few | repricing open signals and positions |
| ~240 | other | | term structure, income scan, on-demand pages |

8,750 requests were a second fetch of a symbol already fetched in that same
clock minute. An exact-match cache would have absorbed only 160 of them,
because the windows differ. The saving needs **subset serving**: answering a
request from a stored chain that covers it.

### Price history (11,994)

Only **951** distinct symbol-and-range pairs were requested. The other
**11,043** requests repeated a pair already fetched that day. One year of daily
bars was fetched 6,794 times for 97 symbols (about 70 times per symbol); SPY's
alone 436 times. The three-month and one-month daily requests for those symbols
are subsets of the one-year series.

### Quotes (15,653)

| Requests | Symbols per call | Caller |
|---|---|---|
| 10,090 | 72 | Market Dashboard poll (3 s in session, 15 s outside) |
| 2,316 | 375 and 352 | Bull/Bear map, every 30 s |
| ~1,230 | 2–11 | sentiment composite, crisis check, trend (volatility indices, breadth, sectors) |
| 695 | 1 | single `/quote` calls |
| ~1,300 | ~91 | Opportunity Board spot overlay, autoscan, off-hours spot re-anchor |

### Payload sizes (prod, 2026-10-03)

| Chain | Contracts | JSON | zlib | Fetch time |
|---|---|---|---|---|
| SPY today → +7 | 1,550 | 1.84 MB | 0.16 MB | 1.5 s |
| $SPX today → +7 | 2,392 | 2.87 MB | 0.26 MB | 2.8 s |
| SOFI today → +7 | 64 | 0.08 MB | 0.01 MB | 0.3 s |
| SPY today → +45 | 4,724 | 5.60 MB | 0.50 MB | 4.6 s |
| SOFI today → +45 | 366 | 0.43 MB | 0.04 MB | 0.6 s |

The latest chain for all 92 collected symbols is about 3 MB compressed. The box
has 8 GB of memory (6 GB available), 4 cores and 119 GB of free disk.

## What a store can and cannot remove

| | Calls per day | Removable by a store |
|---|---|---|
| Repeated daily bars | ~11,000 | yes |
| Chain windows already inside a stored chain | ~2,900 | yes |
| Quote requests for symbols another poller just fetched | ~1,200 | yes |
| Scan windows that one wider fetch could cover | ~4,600 | yes, with a small caller change |
| Quote lists that one poller could carry | ~1,400 | yes, with a small caller change |
| **One new chain per symbol per minute (GEX)** | **40,345** | **no — cadence** |
| **Dashboard quote poll** | **10,090** | **no — cadence** |
| **Bull/Bear 727-symbol quotes** | **2,316** | **no — cadence** |

Expected result: **84k → about 70k (−17%)** with the store alone, **about 65k**
once the autoscan fetches one wide chain per symbol, and **about 48k (−43%)**
with the 3-minute collector cadence that was approved alongside the store (see
"Collector cadence" below). These are estimates from one day's log; phase 0
replaces them with measured hit rates.

Two effects matter more than the daily total:

- **The autoscan burst halves.** One scan is about 325 upstream calls today,
  which is at least 65 seconds on the 5-per-second limiter. That burst is what
  delays the one-minute GEX poll and costs heatmap slots. It falls to about 165
  calls in phase 1 and about 85 in phase 2.
- **Calls become attributable.** The store counts hits and misses per endpoint
  and per caller, so the next audit reads a table instead of a journal.

## Where the store lives — three options

**A. Inside the proxy, behind its existing endpoints (recommended).** The proxy
is already the only process that talks to Schwab, so every response passes
through it. It keeps what it fetches and answers later requests from that copy
when the copy is fresh and covers the request. No caller changes.

**B. Raw files on disk that every service reads directly.** A collector writes
files; services read them through a shared library and fall back to the proxy.
Every caller must be rewired: two client classes plus six modules that call the
proxy with a plain GET (`market_svc/compute.py`, `sector_rotation_assessment.py`,
`portfolio-analyzer/src/data.py`, `fit_swing_model.py`,
`flow_delta_instrumentation.py`, `label_journal.py`). Freshness rules would live
in each process, two services that miss at the same moment would both fetch, and
a dev checkout would need file access to prod's tree.

**C. Redis.** The existing Tier-3 store. Rejected: the public screens' Redis
user can read `cache:*`, so raw quotes written there are published quotes (the
open quote-terms question), and 20–30 MB rewritten every minute would bloat the
nightly Redis dumps and backups.

**Decision: A, held in the proxy's memory.** It delivers what the request
describes — one raw local copy that everything computes from — and keeps a
single access path. A dev checkout borrows prod's proxy, so it gets the store
for free.

The first draft of this design also wrote the store to disk. That was cut
before planning: everything in it is refetched within minutes of a restart
(chains within one to three minutes, daily bars on first use, at most about 950
calls), so files would add encoding, reload and test-isolation code for no call
saved. The whole store is about 20 MB of memory.

## Design

### Shape

```
caller ──GET /chains|/quotes|/pricehistory──▶ proxy handler
                                                 │
                                    market_store.lookup(request, max_age)
                                       hit │            │ miss
                                           ▼            ▼
                                    stored bytes   token_mgr.api_request → Schwab
                                                        │
                                               market_store.put(response)
```

`schwab-proxy/market_store.py` holds three stores and nothing else. The proxy's
market-data handlers are plain `def` functions, so FastAPI already runs them on
worker threads; store work never runs on the event loop that serves `/health`.

A hit skips `_rate_limit()` and `api_call_counter.record()`. The counter
therefore keeps meaning "calls sent to Schwab", and the Settings → API usage
number falls by exactly what the store saved.

### Chain store

- **Key:** symbol plus the full parameter set (`contractType`, `range`,
  `strikeCount`, `fromDate`, `toDate`).
- **Value:** the response header fields, and each expiration's call and put
  maps as separately compressed JSON fragments, with the fetch time. A date
  window is assembled by joining fragments, not by re-parsing 2 MB of JSON.
- **Exact hit:** same key, age within `max_age`.
- **Subset hit:** the request asks for all strikes and all contract types, and a
  stored entry for that symbol, also all strikes and all types, covers its date
  window. The response is the stored entry cut to the requested expirations,
  with `numberOfContracts` recomputed.
- **Never a subset hit:** any request carrying `strikeCount` or a `range` other
  than `ALL`. The sector put/call sums volume across the strikes it receives, so
  extra strikes would change the ratio. Those requests are exact-match only.
- **Refetch the wide window on a near miss.** The scan and the collector run in
  the same minute in either order. If the scan asks for today → +4 and the
  store holds a today → +7 entry that is a few seconds too old, the proxy
  fetches today → +7, stores it and cuts it. The collector's request seconds
  later is then a hit. Without this rule the saving depends on which branch
  wins the race.
- **Freshness:** `chains.max_age_sec` (default 45) while a session is open;
  `chains.closed_max_age_sec` (default 1,800) while every session is closed. An
  entry stored in one session state is never served in another, so a closed-hours
  entry cannot survive the open. A caller may send `maxAge=<seconds>`; the
  collector sends 20, and `maxAge=0` always fetches.
- **Size bound:** one wide entry per symbol plus an LRU of other windows,
  `chains.max_entries` (default 400).

### Bar store

- **Daily bars.** Keyed on symbol and the exact range asked for. The measured
  repeats were all same-range repeats (951 distinct pairs in 11,994 requests),
  so serving a shorter range out of a longer one is not needed and is not built.
  The first request for a pair in each **bar period** fetches from Schwab; later
  requests in that period are served from the store. A day has three periods:
  before the open, the session until the bar settles, and settled. Refetching at
  each boundary revalidates the whole series, which is what catches a split
  adjustment.
- **The bar for the session in progress** is the only part that moves. Two
  modes, selected by `bars.today_bar`:
  - `quote` — build it from that symbol's entry in the quote store (open, high,
    low, last, volume) when that entry is at most `bars.today_quote_max_age_sec`
    old (default 120). The autoscan fetches all its quotes in one batch before
    its history calls, and the dashboard poll covers SPY and the sector funds,
    so the entry is almost always present. Otherwise fall through to a fetch.
  - `ttl` — re-serve the last fetched response for `bars.session_ttl_sec`.
    Simple, but a 15-minute scan saves nothing below a 15-minute TTL, and above
    it the last bar is stale.
- **After the close** the first request at least `bars.settle_min` minutes
  (default 10) after the session ends refetches once, so the store holds the
  settled bar. At most three upstream fetches per symbol and range per day.
- **Intraday bars** (5- and 15-minute SPY series, 1-minute idea tracking) pass
  straight through. They are about 400 calls a day, and a time limit short
  enough to be safe for the regime read would save almost none of them.

### Quote store

- **Key:** symbol. **Value:** Schwab's raw quote block and its fetch time. The
  flattening that `proxy_client.get_quotes` does stays in the client.
- A request is a hit when every requested symbol is present and within
  `quotes.max_age_sec` (default 5). Otherwise the proxy fetches only the missing
  and stale symbols in one upstream call, merges, and answers.
- `/quote` and `/quotes` read the same store. Requests with other `fields`
  values (fundamentals) pass through untouched.
- In memory only. Nothing is written to Redis.

### In memory only

Nothing is written to disk or to Redis. A proxy restart starts the store empty
and it refills from the next requests. No history of raw chains is kept: a full
day would be about 1 GB compressed, and `gex_history.db` already holds what the
app reads back.

### Concurrency

- One lock per key around fetch-and-store, so identical concurrent misses make
  one upstream call and share the answer.
- Readers take no lock on the fetch path; they read an immutable entry object
  that a writer replaces whole.

### Failure behaviour

- An upstream error is returned as it is today. **The store never serves an
  entry older than `max_age` because the upstream failed** — a stale chain
  presented as current is the failure this app guards against everywhere else.
- Any exception inside store code is caught at the handler, counted through the
  same pattern as `services/_degrade.py` (WARNING with traceback, a counter on
  `/health`), and the request falls through to a plain upstream fetch.
- `marketdata.mode = "off"` restores today's pass-through with no store code on
  the request path.

### Observability

- Callers identify themselves with an `X-Caller` header set once in
  `services/_proxy.py` and `proxy_client.py` from the service name.
- `api_call_counts.db` gains `api_calls_detail(day, endpoint, caller, outcome, n)`
  with outcome `upstream`, `hit`, `subset_hit` or `coalesced`.
- `/stats/api_calls` returns the breakdown; Settings → General shows hits beside
  calls.
- Every response carries `X-Store: hit|subset|miss` and `X-Store-Age: <seconds>`.

### Configuration

`config/marketdata.toml`, read through `shared/config_toml.toml_loader`, with an
entry for every key in `webgui/config_schema.py` so it appears in Settings →
Configuration.

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"shadow"` | `off`, `shadow` or `on`; anything else reads as `off` |
| `chains.enabled` / `quotes.enabled` / `bars.enabled` | `true` | per-store switch |
| `chains.max_age_sec` | 45 | freshness while a session is open |
| `chains.closed_max_age_sec` | 1800 | freshness while all sessions are closed |
| `chains.max_entries` | 400 | most chains kept; the oldest stored are dropped |
| `chains.shadow_compare_max_age_sec` | 120 | shadow only: how old an entry may be and still be compared |
| `chains.wide_refetch_max_days` | 7 | longest held window refetched in place of a narrower one; equals the collector's window |
| `quotes.max_age_sec` | 5 | freshness for quote hits; the dashboard poll sends its own 1 |
| `quotes.max_symbols` | 5000 | most symbols kept |
| `bars.today_bar` | `"ttl"` until measured | `quote` or `ttl` |
| `bars.session_ttl_sec` | 1740 | used in `ttl` mode, and in `quote` mode when no usable quote is held |
| `bars.session_spread` | `true` | each series has its own reuse window inside that limit, offset by a stable hash, so the series one scan fetched together are not all refetched by the same later scan (added 2026-10-04, audit PF-100) |
| `bars.today_quote_max_age_sec` | 120 | oldest quote used to build today's bar |
| `bars.settle_min` | 10 | minutes after the close before the settled refetch |
| `bars.max_entries` | 4000 | most series kept |
| `scan.wide_fetch` | `false` | the autoscan fetches one today → +45 chain per symbol |
| `scan.wide_fetch_exclude` | `$SPX, $NDX, SPY, QQQ` | symbols too large for one wide fetch |
| `collection.tail_interval_min` | 1 | minutes between real fetches for watchlist-only symbols; 3 once measured; above 5 reads as 5 |
| `collection.fresh_max_age_sec` | 20 | the age limit sent for one-minute symbols, and the age past which an answer is treated as carried; at most 30 |
| `collection.max_gamma_ratio` | 10 | the most a carried contract's gamma may grow over Schwab's value |
| `collection.cap_refetch_max` | 8 | the most symbols one poll fetches for real because that cap bound on their carried chain; 0 writes the capped chain (added 2026-10-04, audit AC-120) |
| `collection.carry_slack_sec` | 30 | added to the interval when asking for a stored chain |

## Rollout

**Phase 0 — attribution and shadow mode.** Ship the `X-Caller` header, the
detail counter, and the store in `shadow` mode: every request still goes
upstream, the store is filled, and for each request the proxy also computes
what it would have answered and records whether it matched. Shadow mode costs
no extra Schwab calls. One trading day of it answers three questions the design
depends on:

1. Does a chain cut from today → +7 contain exactly the contracts Schwab returns
   for today → +4? (Compare the sets of expiration, strike and side.)
2. Does Schwab's daily series include the bar for the session in progress, and
   does a bar built from the quote match it? This decides `bars.today_bar`.
3. What are the real hit rates per caller? These replace the estimates above.

**Phase 1 — turn the stores on**, one at a time: bars, then chains, then quotes.
The only caller change is the dashboard poll sending `maxAge=1`, so it never
re-reads its own three-second-old quotes. Expected: 84k → about 70k.

**Phase 2 — one wide chain per scan symbol.** The autoscan fetches today → +45
once per symbol and cuts its three windows from it locally (three calls become
one). SPY, QQQ, $SPX and $NDX keep their three separate windows because their
wide chain is 5.6 MB and 4.6 s; the list is `scan.wide_fetch_exclude`.
Expected: about 65k.

Three smaller ideas from the first draft were dropped as not worth their code:
widening the dashboard poll's symbol list (about 850 calls a day), fetching the
$SPX term chain before the 7-day chain (88), and keeping `/expirationchain` for
the day (about 100).

**Phase 3 — collector cadence.** See the next section. Expected: about 48k.

## Collector cadence — 3 minutes for watchlist-only symbols

The collector polls 92 symbols every minute. 28 of them are named in
`config/symbols.toml` `[collection]` (the indices, the broad and sector funds,
the mega-caps); the other 64 come only from the watchlist workbook.

- **Core symbols keep a real fetch every minute.** Core is the 28 configured
  symbols, the symbol open on the Dealer Positioning page, the public page's
  hot symbols, and the hedging-flow (HIRO) symbols.
- **Watchlist-only symbols get a real fetch every third minute**, staggered by
  symbol so each minute fetches about a third of them (about 49 chains a minute
  in place of 92).
- **On the two minutes between**, the collector asks the proxy for the stored
  chain (`maxAge` = 210 seconds), reads the live price for those symbols in one
  batched quote call, and **carries the chain forward**: `underlyingPrice`
  becomes the live price, and each contract's gamma and delta in the nearest
  expiration are moved by the Black-Scholes change between the fetch price and
  the live price. Schwab's own value stays the base and only the change is
  modelled, so the heatmap does not step every third minute. Charm and vanna
  need nothing: the engine already computes them from spot and volatility.
- **What is written.** All five views are written every minute for every
  symbol, so no reader sees a gap. On a carried minute the volume and premium
  totals repeat the last fetched values.
- **What is skipped on a carried minute.** The unusual-activity and big-delta
  detectors, and the same-tick chain hand-off. They read volume, and a carried
  chain has no new volume. An alert on a watchlist-only symbol can therefore
  arrive up to two minutes later than today. Crossover alerts read the stored
  premium series and are unaffected except for the same delay.
- **Scan minutes.** The scan asks for chains at most 45 seconds old, so on its
  slots the proxy refetches the symbols that were not due. That costs about
  1,300 calls a day and needs no code.
- **Requires the chain store to be on.** With the store off or in shadow, every
  request reaches Schwab, every chain is fresh, and the collector behaves
  exactly as today.

Net saving: 64 symbols × 440 minutes × ⅔ ≈ 18,800, less the scan-minute
refetches and one quote call a minute, about **17,000 calls a day**.

**Gate before switching it on.** `tools/measure_chain_carry.py` fetches a few
watchlist symbols every minute for a short window and compares the engine's
output from a carried chain with its output from the fresh one: net gamma, the
flip level and the walls. `collection.tail_interval_min` ships as 1 and moves to
3 only after that comparison is read.

## Testing

- **Pure functions, unit-tested without a proxy:** window coverage, cutting a
  chain to a window, recomputing the header, building today's bar from a quote,
  key normalisation, session-state invalidation.
- **Handler tests** with a fake upstream that counts calls: a second identical
  request makes no call; a subset request makes no call; a `strikeCount` request
  is never served from an all-strikes entry; two concurrent misses make one
  call; an upstream error is never answered from a stale entry; a store
  exception falls through and is counted.
- **Shadow mode on prod** for one session, read from the detail counter.
- **Before and after** on the daily counter and on skipped GEX slots
  (`scheduler branch 'gex' still running` in the options service journal).

## Risks

| Risk | Control |
|---|---|
| A stored chain is presented as newer than it is | `X-Store-Age` on every response; the collector's own `maxAge` is 20 s; no serving past `max_age` on upstream failure |
| A cut chain differs from what Schwab would return | proved or disproved in shadow mode before the chain store is switched on |
| The bar built from a quote differs from Schwab's daily bar | measured in shadow mode; `ttl` mode is the fallback |
| The proxy becomes stateful and a store bug takes market data down | store exceptions fall through to a plain fetch; `mode = "off"` |
| Off-hours quirks (index open interest zeroed, chain `underlyingPrice` pinned to the prior close) | unchanged — the store returns Schwab's bytes; entries do not cross a session change |

## What review changed during implementation

Each group of tasks was reviewed by an independent reviewer and the findings
fixed before the next group built on it. The changes that differ from the
sections above:

**Store**
- **An empty answer is never stored and an empty cut is never served.** Measured
  on prod 2026-10-03 (SOFI, a +1..+2 day window with no expirations): Schwab
  returns HTTP 200, `status: "SUCCESS"`, both maps empty, `numberOfContracts: 0`
  and `underlyingPrice: 0.0`. A cut that kept no expiration would carry the real
  price instead, so it is refused and the request is fetched as asked.
- **The wider-window refetch is derived, not configured.** It uses the narrowest
  window already held for the symbol that starts today, covers the request, has
  an expiration inside it and is at most `wide_refetch_max_days` (7) long. A
  ceiling of 10 would have turned the collector's one-minute `$SPX` request into
  the 10-day term-structure window.
- **Every stored entry is stamped when its fetch started,** not when it
  returned. It is the conservative age.
- **Age limits must be real numbers.** NaN, infinity and booleans never hit; a
  caller's `maxAge` is clamped to an hour; a malformed `maxAge` uses the
  configured limit and never fails the request.
- **Payloads holding NaN or infinity are never stored.**

**Gateway**
- **Shadow simulates `on`.** It makes `on`'s decision with `on`'s limits and
  does not re-store a would-be answer, so the held entry ages as it would under
  `on`. The first version re-stored on every call and showed 19 of 20 would-be
  hits for a caller that `on` would never serve. Shadow still counts low in two
  cases it cannot reproduce: the wider-window refetch and coalescing. It counts
  HIGH in one: in shadow the collector sends no age limit, so in the few minutes
  it polls while every session is closed (about 08:26-08:29 and 15:16-15:19 CT)
  it records would-be hits against its own previous chain, about 700 a day on
  `chains` for caller `options_svc`. Under `on` it sends 20 seconds, so those
  are not a saving.
- **A fetch failure that is not an upstream error is not a store bug.** An
  expired token raises inside the fetch; it now propagates once, with no degrade
  counted and no second call.
- **Requests waiting behind a failed fetch share its failure** instead of each
  retrying in turn (one failed fetch can take about 95 seconds).
- **Today's bar in `quote` mode** needs a quote fetched after the open and
  before the close, falls back to the time limit when no usable quote is held,
  and the shadow verdict compares open, close, high and low, with volume as its
  own outcome.

**Proxy and client**
- The caller label is sanitized on both ends, prefixed `dev.` from a dev
  checkout, and bounded to 64 distinct names per process (`other` past that).
  `/stats/api_calls` returns at most 500 breakdown rows with exact totals.
- A dead token still answers 500, now with a JSON detail body.

**Scan**
- A locally cut empty window is made to look like Schwab's own (price 0.0,
  zero contracts), because the scan funnel reads that field.
- A failed wide fetch logs one warning and falls back to the three window
  fetches, so the switch can never make a scan worse than before.

**Collector**
- A carried chain's skew readings, volume and premium totals and the per-strike
  premium grid come from the chain as stored, so those columns repeat on carried
  minutes instead of mixing a live price with fetch-time volatility.
- A Schwab delta of zero is never carried (the engine treats zero as missing and
  substitutes its own). After settlement Schwab's greeks stand and only the
  price moves. The gamma ratio is capped (`max_gamma_ratio`).
- The gamma-flip alert's symbols stay on the one-minute tier; with that alert
  watching every symbol there is no tail at all.
- One quote call per poll in every session state.

**Known limits, accepted**
- On an expiration day a fast move understates carried gamma for the strike the
  price walks onto (the cap binds). `tools/measure_chain_carry.py` reports
  expiration-day comparisons separately so the operator sees it before choosing 3.
- Futures roots (`/ES` answered as `/ESZ26`) never hit the quote store and are
  fetched every time, as before.

## Not in this design — remaining cadence levers

Each is independent and each changes what the app shows, so each is the
operator's call.

| Lever | Saves per day | Cost |
|---|---|---|
| Dashboard poll 15 s → 30 s outside the regular session | ~2,500 | slower overnight tiles |
| Bull/Bear quotes from the equities stream instead of polling | ~2,300 | a delta-merging stream consumer in the proxy |
