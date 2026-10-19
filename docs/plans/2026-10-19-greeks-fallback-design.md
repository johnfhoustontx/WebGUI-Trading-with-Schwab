# Greeks fallback for Dealer Positioning — design

**Status:** **approved by the user 2026-10-19** ("Design approved as written,
with the defaults for the three open questions"). Nothing here is live.
Plan: [2026-10-19-greeks-fallback-plan.md](2026-10-19-greeks-fallback-plan.md).
**Asked for by the user:** "Plan the Greeks fallback on TradeStation's
option-chain stream first, with Massive Starter as a possible second source.
Design first, nothing live until I approve." The user opted out of the
question round; every choice below not quoted from them is marked **(my call)**.

## Why

On 2026-10-19 Schwab's `/chains` sent gamma, delta and volatility = -999 ("not
computed") and `theoreticalOptionValue` = -1 on **every** contract of every
symbol, before and after the 08:30 CT open. Bid/ask were frozen at Friday's
close. Open interest, last trades, volume and `/quotes` spot stayed live. The
check went through the proxy's `/passthrough`, which skips its store. Every
Dealer Positioning exposure became 0.0. Commit `a944df2` made the page say so
(`services/options_svc/greeks_census.py`, `no_greeks`) instead of drawing flat
bars and tie walls. This design is the next step: **keep drawing real
positioning on such a day from a second source, and always say which source it is.**

The user holds accounts at TradeStation, Public.com and Moomoo. TradeStation
was chosen because its official API streams a whole option chain **with Greeks
already computed** (`GET /v3/marketdata/stream/options/chains/{underlying}`).
Massive Options Starter ($29/month) is the second source: its quotes are
15-minute delayed and its Greeks are computed from those quotes.

## Goals

1. When Schwab's chain for a covered symbol has no usable Greeks, fill
   gamma, delta and volatility from TradeStation, so all four Dealer
   Positioning views (GEX, Charm, DEX, Vanna), the walls, the flip and the
   forward projection draw again.
2. Say the source everywhere the numbers appear: on the page, in the
   snapshot, and in every stored row.
3. Prove agreement before trusting it: a **shadow** mode runs TradeStation
   beside a healthy Schwab and reports how far apart they are. The user reads
   that report and only then turns the fill on.
4. Ship **off**. No behaviour changes until the user flips the config.

## Non-goals

- Paper books, the scanner, the Account, Rescue, flow alerts (`big_delta`),
  HIRO. The user said "leave the paper books alone" for this outage; none of
  them read the filled chain.
- The Term view (next five expirations). It keeps the no-Greeks sentence.
- Replacing Schwab. Schwab stays primary; the fallback only fills a chain the
  census says is empty.
- Mixing sources inside one chain (see "Whole chain, not per contract").
- Public.com and Moomoo. They were looked at and are not part of this build.

## Anti-goals: it fails even if it works when

- A number from TradeStation or Massive is shown or stored as if it were Schwab's.
- A stale fallback (a dead stream) is used. Old Greeks drawn as live ones are
  worse than the no-Greeks sentence.
- The fallback costs a Schwab request, or slows the one-minute poll's priority lane.
- A raw-feed replay of a filled minute derives different numbers than it did live.

## Sources

### TradeStation (phase 1)

Facts from the official spec (`api.tradestation.com/docs/specification`), read 2026-10-19:

- `GET /v3/marketdata/stream/options/chains/{underlying}`, HTTP chunked,
  newline-delimited JSON (`application/vnd.tradestation.streams.v2+json`).
  Streams can end at any time, so reconnect.
- **At most 10 concurrent streams** per key. One stream covers one
  underlying and one `expiration`; the default is the next expiry.
  `strikeProximity` (default 5) sets how many strikes are streamed above and
  below `priceCenter`.
- Each message has `Delta`, `Gamma`, `Theta`, `Vega`, `Rho`,
  `ImpliedVolatility` (a **decimal**: 0.3118 means 31.18%), `DailyOpenInterest`,
  `Bid`, `Ask`, `Mid`, `Last`, `Volume`, `Side`, `Strikes` and `Legs`. Numbers
  come as **strings**.
- Greeks come from a Bjerksund–Stensland model priced off the **ask**, with
  `riskFreeRate` defaulting to the $IRX.X quote. They will not match Schwab's
  number for number. The shadow report measures by how much.
- Auth: OAuth 2 authorization-code flow. The access token lasts 20 minutes.
  The refresh token needs the `offline_access` scope and by default never
  expires (TradeStation can switch a key to rotating tokens, which last 24 h).
  `MarketData` is one of a key's default scopes.

### Massive Options Starter (phase 2, only if the user asks)

$29/month, individual use: unlimited calls, 15-minute delayed data, snapshot
endpoints, Greeks and IV, daily open interest. Index options use the `I:`
prefix (`/v3/snapshot/options/I:SPX`); bare `SPX` returns contracts with no
Greeks. Because the quotes are 15 minutes old, **take only the implied
volatility** and recompute gamma and delta locally with `bs_gamma` /
`bs_delta`, using Schwab's live spot and `options_calculator.expiry_time_to_years`.
IV moves slowly within a session; a 15-minute-old spot does not, for a 0-DTE
gamma. Label it "Massive (15-min IV)". Key: `MASSIVE_API_KEY` in the stack `.env`.

## How it works

```
TradeStation stream ──> schwab-proxy: ts_greeks worker (memory) ──> GET /alt/greeks
                                                                           │
gex_collector.fetch_minute: Schwab chain ──> re-anchor ──> carry ──> FILL ─┘──> RawMinute
                                                                            ├─> raw feed (tee)
                                                                            └─> derive_minute ──> gex_history (+ greeks_source)
gamma_snapshot (its own fetch path) ──> FILL ──> views (+ greeks_source) ──> page chip
```

### 1. Where the stream lives: the proxy (my call)

A new module `schwab-proxy/ts_greeks.py` runs one worker thread per stream,
the same way the proxy already runs the Schwab streamer. It holds the latest
message per contract in memory. Its route is

`GET /alt/greeks?symbol=SPY&expiry=2026-10-19`
→ `{"source": "tradestation", "as_of": <epoch of the newest tick>, "contracts":
[{"side": "C", "strike": 768.0, "gamma": …, "delta": …, "iv_pct": …, "oi": …, "tick_age_sec": …}]}`

It is protected by `X-Proxy-Secret` like the account routes, and not cached by
`market_store`.

Why the proxy: it already owns broker credentials and long-lived streams, it is
never on the public domain, and every service reaches it over loopback. Why
not a new service: that would mean a new port, unit, health probe and generated
unit for four streams.

Token handling: `tools/tradestation_auth.py` runs once on the box. It prints
the authorize URL, takes the pasted code, exchanges it, and writes
`schwab-proxy/tradestation_tokens.json` (gitignored, mode 0600). The proxy
refreshes the access token before its 20 minutes run out.
`TS_CLIENT_ID` / `TS_CLIENT_SECRET` go in the stack `.env`, never in config or git.

Streams: one per configured symbol, for the **nearest expiry** (the GEX engine
reads only the nearest expiry; see `GammaEngine.calc_all_from_chain`). Each
stream reopens on the new nearest expiry at the session flip
(`[windows.session_flip]`), and after any disconnect with backoff. Four
symbols use four of the ten streams.

The worker runs in every mode except `off`.

### 2. The fill: one pure function, two call sites

`options-scanner/greeks_fill.py` (pure, no I/O):

`fill_chain(chain, alt, now_ts, max_age_sec) -> (chain, source)`

- Returns the chain **unchanged** with `source = "schwab"` unless
  `greeks_census.greeks_missing(chain)` is true. Move that one function to
  `shared/` (or have both import it from a shared module) so the collector,
  which cannot import `services.options_svc`, can call it.
- `alt` is the `/alt/greeks` answer. It is refused (unchanged chain, source
  `"none"`) if `as_of` is older than `max_age_sec`.
- Contracts match on **(side, strike, expiry date)**, never on symbol strings.
  TradeStation's option symbol format and its index root (`$SPX.X`, SPXW) are
  different from Schwab's, and the join must not depend on either.
- For each matched contract it writes `gamma`, `delta`, and `volatility =
  iv_pct` (Schwab's chain carries volatility in **percent**; convert from
  TradeStation's decimal once, in the proxy). It leaves open interest, bid, ask
  and last alone; Schwab's open interest was live on the incident day.
  Unmatched contracts keep -999, so `shared.greeks` counts them as no reading.
- It stamps `chain["_greeks_source"] = "tradestation"` and
  `chain["_greeks_as_of"]` on the chain.

Call sites:

1. **`gex_collector.fetch_minute`**, after the re-anchor and the carry, before
   building the `RawMinute`. The tee therefore stores the filled chain, and
   `derive_minute` stays pure, so a replay derives exactly what ran live
   (raw-feed design, 2026-10-14). The `/alt/greeks` request is made only for
   chains the census flags, on loopback, and is not a Schwab call. It must not
   carry the poll's `X-Priority` header.
2. **`compute.gamma_snapshot`'s own fetch** (`_gamma_fetch_chain`). The stashed
   tick chain (`_take_tick_chain`) is already filled.

`_light_gex_context` (Rescue) and every scanner path are **not** filled (non-goals).

**What else sees a filled chain (my call).** The filled chain goes on through
`derive_minute` and the `on_chain` hooks (`collect_derive.py`), because the
gamma page's tick-chain stash rides those hooks and needs the filled chain.
Each hook that reads a Greek checks `chain.get("_greeks_source")` and **skips
a filled chain**, so the non-goals hold without carrying a second, unfilled
chain: contract-level UOA, `big_delta`, HIRO, and anything else the builder
finds reading `delta`/`gamma`/`volatility` there. The fill changes no volume,
premium, price or open-interest field, so the volume and premium hooks are
unaffected. The skew fields stored with each row (`rr_25d`, `atm_iv`) **are**
computed from the filled volatility and delta. They are stored with that
row's `greeks_source`, so a reader can tell.

**Which expiry is judged and filled (my call).** The census and the fill both
apply to the **nearest expiry only**, the one the engine reads
(`GammaEngine._find_nearest_exp_key`). `greeks_census.greeks_missing` (shipped
in `a944df2` as whole-chain) is changed to the nearest expiry, so the page's
no-Greeks sentence and the fill decide on the same contracts. Later expiries
stay as Schwab sent them. A metric that reads them counts their -999 as no
reading, which it already does today.

**Two roots on one date (my call).** On a monthly expiry, Schwab's `$SPX`
chain holds both SPX (AM-settled) and SPXW (PM-settled) contracts under one
date key, and `$NDX` holds NDX and NDXP. The join key includes the **root**
whenever both sources report it. A contract whose root has no match stays at
-999. The page reports how many contracts in the window were filled, so an
under-filled monthly expiry is visible rather than silent.

**No hysteresis (my call).** Each minute carries its own source. If Schwab
flaps, the stored `greeks_source` column shows it minute by minute; nothing
smooths it over.

### 3. Whole chain, not per contract (my call)

Fill only when the whole chain is flagged as missing, and then fill all of it from
one source. If Schwab's Greeks are missing on only some contracts, nothing is
filled. Mixing two models in one chain would make the walls and flip a blend
nobody can name. The incident was all-or-nothing; a partial outage can be
revisited if it is ever seen.

### 4. Modes: `config/greeks_fallback.toml`

```toml
mode = "off"            # "off" | "shadow" | "on"
symbols = ["$SPX", "SPY", "QQQ", "$NDX"]
strike_proximity = 40   # strikes streamed each side of spot
max_age_sec = 90        # older fallback data is not used
sources = ["tradestation"]    # order tried; "massive" in phase 2
public_uses_fallback = false  # see "Public screens"
wall_window_pct = 10.0  # filled strikes must reach this far for walls to be kept
```

The values are my call. The page's walls are searched unbounded
(`compute.gamma_walls` passes no `max_pct`), so the fill has a reach of its
own, `wall_window_pct` (10% of spot). For $SPX at 5-point strikes that is
about 150 strikes each side; the builder measures the payload and
TradeStation's maximum for `strike_proximity`. A wall picked from a window too
short to hold it is an artifact, so the census rule applies: if the filled
window does not reach `wall_window_pct`, clear
the walls the same way `mark_no_greeks` does. Every key goes in
`webgui/config_schema.py` (Settings → Configuration), and the loader goes
through `shared/config_toml`.

- **off:** nothing runs, and the chain is untouched (today's behaviour).
- **shadow:** the worker streams, and each minute where Schwab's Greeks *are*
  present, the fill is computed on a copy and compared, never used. The
  comparison runs in `options_svc` after `derive_minute`, on the collector's
  thread, and `options_svc` writes the view (the proxy publishes nothing to
  Redis) (my call). Its cost is logged with the poll's other per-minute costs. Each
  session gets one line in the journal and a view `cache:options:greeks_shadow`
  per symbol with: matched contracts, median and 90th-percentile relative gamma
  difference near spot (±10 strikes), net GEX total from each source, the
  distance between the two flips, and whether the walls agree.
- **on:** fill as above.

### 5. Recording the source

- The snapshot gets `greeks_source` (`"schwab" | "tradestation" | "massive" |
  "none"`) at the top level, beside `no_greeks` per view. `mark_no_greeks`
  still applies when the source is `"none"`.
- `gex_history.db` `snapshots` gets a nullable `greeks_source TEXT` column
  (NULL means Schwab, so old rows keep their meaning). It is added the way
  `carried_age_sec` was. Any study that reads these rows must decide whether to
  exclude filled minutes; the builder lists those readers
  (`gex_history_db` callers outside the page) in the plan.
- The raw feed carries `_greeks_source` inside the chain JSON, with no schema change.
- No contract model guards the gamma snapshot (see CLAUDE.md: contracts are a
  write-side gate on some views only), so the new keys need no model change.
  A source change is a payload change like any other; `skip_unchanged`
  publishes it.

### 6. The page

- When `greeks_source` is not `"schwab"`, the header strip shows one small
  label next to the collector status: **"Greeks: TradeStation"**, or **"Greeks:
  Massive, 15-min IV"**. Its hover text says Schwab sent none for this chain.
  It uses the kit's classes, and nothing changes when the source is Schwab.
- The no-Greeks sentence stays for `"none"`.
- `page_help.py` gets a line, and so do the User Guide and the Technical
  Reference (which model, priced off which price).

### 7. Public screens (`live.`)

`public_uses_fallback = false` by default (my call): the public process sees
`greeks_source` and draws the no-Greeks sentence instead of fallback-derived
GEX. The reason is redistribution. TradeStation's and Massive's individual
terms may not allow publishing figures derived from their data on a public
website. **The user decides this**; see the open questions.

### 8. Health and failure

- The proxy's `/health` gains `alt_greeks: {mode, streams_up, oldest_tick_age_sec}`.
  A dead stream does not make the proxy `up: false`, because Schwab is still primary.
- A swallowed error in the worker calls `_degrade.degraded("alt_greeks")`
  (15-line rule).
- In mode `on`, a fill that cannot happen (stream down, stale, unmatched)
  leaves the chain as Schwab sent it, and the page shows the no-Greeks sentence.

## Constraints the builder must keep

- `shared/greeks.py` keeps refusing -999; the fill writes real numbers, never a sentinel.
- `compute.py` and `gamma.render` are at their line ceilings; new code goes in
  sibling modules.
- No hard-coded port or path (`repo_paths`); no new tunable outside config.
- New dependencies go in `requirements.lock` by hand. Plain `requests`
  streaming may be enough for TradeStation; prefer no new dependency.
- Tests may not reach the network (root `conftest.py`). Feed the worker a
  recorded stream.
- Never promote between 06:30 and 15:20 CT on a trading day: the promote
  restarts the GEX collector.

## Tests (minimum)

- `fill_chain`: a -999 chain plus fresh TradeStation data gives non-zero GEX
  through the real `GammaEngine`; stale data is refused; a healthy Schwab chain
  is untouched in every mode; the percent/decimal conversion; unmatched
  contracts stay -999; the join is on (side, strike, expiry) with differently
  formatted symbols.
- Replay: a filled `RawMinute` stored by the tee and re-derived gives
  identical rows.
- Shadow: the chain used is byte-identical to Schwab's, and the comparison
  view is written.
- The stream worker against a recorded chunked payload: string numbers,
  reconnect after an end of stream, expiry rollover, token refresh.
- The page: the label shows for each non-Schwab source and not for Schwab;
  the public origin with `public_uses_fallback = false` shows the sentence.
- `config_schema` catalogue coverage (the existing test fails on a missing key).

## Rollout

1. The user requests a TradeStation API key (developer portal or client
   services) with `MarketData` and `offline_access`, and puts
   `TS_CLIENT_ID` / `TS_CLIENT_SECRET` in the stack `.env`.
2. Build behind `mode = "off"`, then merge, push and promote outside trading hours.
3. Run `tools/tradestation_auth.py` once on the box.
4. Set `mode = "shadow"` for at least one full normal session, and the user
   reads the agreement view.
5. The user decides whether to set `mode = "on"`.
6. Phase 2 (Massive) only if the user asks after phase 1.

## Open questions: answered 2026-10-19 (the defaults)

1. **API key:** the user requests or supplies it at rollout step 1. Nothing
   in the build needs it: tests run on recorded streams.
2. **Public screens and X:** **no.** Fallback-derived positioning does not
   appear on `live.neuralstrike.co` or in posts (`public_uses_fallback = false`).
3. **Symbols:** **`$SPX, SPY, QQQ, $NDX`**, with no fifth stream for the private
   page's selected symbol.

## Left to the builder

- `strike_proximity` against the wall bound and the payload size.
- TradeStation's index root and option symbol format. The join does not
  depend on it, but the stream's `underlying` path argument does.
- Whether a stream answers during 06:30–08:25 CT.
- The exact field names inside `Legs` (strike, side and expiry come from there or from `Strikes`/`Side`).
- How the proxy-side worker is started and stopped with the proxy's lifecycle.
