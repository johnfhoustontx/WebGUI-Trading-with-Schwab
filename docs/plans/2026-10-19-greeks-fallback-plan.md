# Greeks Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When Schwab's chain for $SPX, SPY, QQQ or $NDX has no usable Greeks
on its nearest expiry, fill gamma, delta and volatility from TradeStation's
option-chain stream, say so everywhere, and ship it **off** behind a shadow
mode that measures agreement first.

**Architecture:** A worker in the schwab-proxy streams TradeStation chains into
memory and serves them at `GET /alt/greeks`. One pure function, `fill_chain`,
writes those Greeks into a Schwab chain the census flags. It runs at the end of
`gex_collector.fetch_minute`, so the raw feed stores the filled chain and replay
stays exact, and in `compute._gamma_fetch_chain`. The source travels with the
chain (`_greeks_source`), the snapshot (`greeks_source`) and every stored row
(a new `greeks_source` column).

**Tech Stack:** Python 3.11, FastAPI (proxy), `requests` streaming (already pinned;
**no new dependency**), SQLite, Redis via `shared.bus`, NiceGUI page, pytest.

**Spec:** [2026-10-19-greeks-fallback-design.md](2026-10-19-greeks-fallback-design.md) (approved 2026-10-19).

## Global Constraints

- Ships `mode = "off"`. With mode off, every code path behaves exactly as today.
- Default symbols: `["$SPX", "SPY", "QQQ", "$NDX"]`. No fifth stream.
- `public_uses_fallback = false`: nothing fallback-derived is published to
  `cache:options:gamma_pub:*` or its history keys, or posted anywhere.
- Fill only the **nearest expiry**, and only when the census says it has no
  usable gamma. Fill the **whole** nearest expiry from one source; never mix sources in one chain.
- Never write a sentinel. `shared/greeks.py` keeps refusing -999.
- `volatility` in a Schwab chain is **percent**. TradeStation's
  `ImpliedVolatility` is a **decimal**. Convert once, in the proxy (`iv_pct = 100 * IV`).
- The join is on `(side, strike, expiry, root)`, never on symbol strings.
- `compute.py` stays at or under `COMPUTE_MAX_LINES = 10487` (it is at 10465);
  `gamma.render` stays at or under its ceiling of 1462. New code goes in sibling modules.
- Every tunable lives in `config/greeks_fallback.toml`, read through
  `shared/config_toml.toml_loader`, and is catalogued in `webgui/config_schema.py`.
- Secrets: `TS_CLIENT_ID` / `TS_CLIENT_SECRET` live in the stack `.env`; the
  token file `schwab-proxy/tradestation_tokens.json` is gitignored and mode 0600.
- Tests never reach the network. Feed the stream worker recorded payloads
  through a fake session.
- The fallback must not send `X-Priority` and must not call Schwab.
- Never promote between 06:30 and 15:20 CT on a trading day.

## Review Focus

1. **A stream that dies mid-session.** The book keeps its last ticks. Past
   `max_age_sec` the fill must refuse them (source `"none"`, the no-Greeks
   sentence), not draw them as live. Owned by Task 3 (fill) and Task 4 (book).
2. **The expiry rollover at the date change.** The engine keeps today's
   expiry after the 15:00 CT close, so the streams must reopen on the new
   nearest expiry when the CT date changes. A stream still on yesterday's expiry
   must never fill today's chain: the join's `expiry` refuses it. Owned by Task 5.
3. **A monthly expiry with two roots** (SPX/SPXW, NDX/NDXP). Contracts with an
   unmatched root stay -999, and the filled count shows it. Owned by Task 3.
4. **A raw-feed replay of a filled minute.** It must derive byte-identical
   rows after the JSON round trip of the raw store. Owned by Task 7.
5. **Schwab healthy while the fallback is on.** The chain must be left
   untouched and no `/alt/greeks` request sent. Owned by Task 7.

---

### Task 1: The census judges the nearest expiry, in `shared/`

**Files:**
- Create: `shared/chain_greeks.py`
- Modify: `options-scanner/gamma_tool.py` (`_find_nearest_exp_key` delegates)
- Modify: `services/options_svc/greeks_census.py` (`greeks_missing` delegates; new `today` parameter)
- Modify: `services/options_svc/compute.py` (the one `_census.greeks_missing(chain)` call passes `today`)
- Test: `shared/tests/test_chain_greeks.py`; update `services/options_svc/tests/test_greeks_census.py`

**Interfaces:**
- Produces: `nearest_expiry_key(exp_map: dict, today: str) -> tuple[str | None, int | None]`.
  It has exactly the behaviour of today's `GammaEngine._find_nearest_exp_key`,
  which becomes a one-line delegate so the two cannot drift.
- Produces: `greeks_missing(chain: dict | None, today: str) -> bool`. It is
  true when the nearest-expiry contracts of both maps exist and none has a
  usable gamma (`shared.greeks.gamma`).
- Produces: `nearest_contracts(chain: dict, today: str) -> Iterator[tuple[str, str, dict]]`,
  yielding `(side "C"/"P", expiry_key, contract)` for the nearest expiry only.
- Imports: `shared.greeks` and stdlib only.

- [ ] **Step 1: Write the failing tests** in `shared/tests/test_chain_greeks.py`:
  `test_nearest_expiry_prefers_today`, `test_nearest_expiry_smallest_dte_otherwise`
  (cases copied from `_find_nearest_exp_key`'s docstring);
  `test_missing_judges_only_the_nearest_expiry`: nearest expiry all -999 and a
  later expiry with real gammas gives `True`;
  `test_a_usable_nearest_gamma_is_not_missing`; `test_zero_gamma_is_a_reading`;
  `test_no_contracts_is_not_missing`. In `test_greeks_census.py`, pass
  `today="2026-10-19"` to every `greeks_missing` call and add the
  nearest-expiry case.
- [ ] **Step 2: Run them and confirm they fail:** `python -m pytest shared/tests/test_chain_greeks.py services/options_svc/tests/test_greeks_census.py -q`. Expected: ImportError / TypeError.
- [ ] **Step 3: Implement the three functions; delegate `_find_nearest_exp_key` and `greeks_census.greeks_missing(chain, today)`.** In `gamma_snapshot`, pass `now.strftime("%Y-%m-%d")` (`now` is already in scope).
- [ ] **Step 4: Run** `shared/tests`, the options_svc suite (alone, from the repo root) and `options-scanner/tests/test_gamma_tool*.py`. Expected: PASS.
- [ ] **Step 5: Commit** `refactor(gamma): the Greeks census judges the nearest expiry, in shared/`.

### Task 2: `config/greeks_fallback.toml` and its loader

**Files:**
- Create: `config/greeks_fallback.toml`, `shared/greeks_fallback_config.py`
- Modify: `repo_paths.py` (add `GREEKS_FALLBACK_TOML`), `webgui/config_schema.py` (a `ConfigFile` for it), `CLAUDE.md` (the config file list in "Paths, ports and configuration")
- Test: `shared/tests/test_greeks_fallback_config.py`

**Interfaces:**
- Produces accessors, each never raising and falling back to its default on
  a missing or bad value: `mode() -> str` (in `{"off","shadow","on"}`, default
  `"off"`); `symbols() -> tuple[str, ...]`; `strike_proximity() -> int`
  (default 40, range 5..200); `max_age_sec() -> float` (default 90);
  `sources() -> tuple[str, ...]` (default `("tradestation",)`);
  `public_uses_fallback() -> bool` (default False); and
  `wall_window_pct() -> float` (default 10.0, range 1..50: how far each side
  of spot the filled strikes must reach for the walls to be kept); and
  `ts_underlying(symbol: str) -> str`, which reads the table
  `[tradestation.underlying]` (default `{"$SPX" = "$SPX.X", "$NDX" = "$NDX.X"}`,
  identity otherwise).
- The config is read per call (mtime-cached, like `gamma_heat_config`), so a
  mode change applies **without a restart**. Catalogue it with `restart=()`.

- [ ] **Step 1: Write the failing tests:** `test_defaults_when_file_missing`
  (mode `"off"`, the four symbols, 40, 90.0, `("tradestation",)`, False);
  `test_bad_mode_reads_off` (`mode = "maybe"` gives `"off"`);
  `test_strike_proximity_out_of_range_reads_default`;
  `test_ts_underlying_maps_indices_and_passes_others`.
  `webgui/tests/test_config_schema.py` must still pass, which it does only once
  every key is catalogued.
- [ ] **Step 2: Run** `python -m pytest shared/tests/test_greeks_fallback_config.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement the loader and the tracked TOML with these defaults, and catalogue every key.** Write the copy from the reader's side; the mode's help text names the three modes in one sentence each.
- [ ] **Step 4: Run** the new test plus `webgui/tests/test_config_schema.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(config): greeks_fallback.toml, shipped off`.

### Task 3: `fill_chain`, the pure fill

**Files:**
- Create: `options-scanner/greeks_fill.py`
- Test: `options-scanner/tests/test_greeks_fill.py`

**Interfaces:**
- Consumes: `shared.chain_greeks.greeks_missing`, `nearest_contracts`, `nearest_expiry_key` (Task 1).
- Consumes: the `/alt/greeks` payload shape (Task 4): `{"source": "tradestation",
  "as_of": float, "expiry": "YYYY-MM-DD", "contracts": [{"side": "C"|"P",
  "strike": float, "root": str|None, "gamma": float, "delta": float,
  "iv_pct": float}]}`.
- Produces: `fill_chain(chain: dict, alt: dict | None, today: str, now_ts: float,
  max_age_sec: float) -> tuple[dict, str]`. It returns `(chain, "schwab")`
  untouched when the census says Greeks are present. Otherwise it returns
  `(new_chain, source)`, where `new_chain` is a **copy** (the input is never
  mutated) and `source` is `alt["source"]`, or `"none"` when `alt` is None,
  stale (`now_ts - as_of > max_age_sec`), for another expiry, or matches zero
  contracts. On `"none"` the copy is still stamped. Stamps:
  `_greeks_source`, `_greeks_as_of`, `_greeks_filled` (matched count) and
  `_greeks_window` (contracts in the nearest expiry).
- Produces: `is_filled(chain: dict) -> bool`: `_greeks_source` present and not `"schwab"`.
- Root: a Schwab contract's root is `contract["symbol"][:6].strip()` (for
  example `"SPXW"`). If `alt` gives `root` None, match on
  `(side, strike, expiry)` only where Schwab holds a single root at that key;
  otherwise leave the contract unmatched.
- Strike key: `round(float(strike), 4)`.

- [ ] **Step 1: Write the failing tests** (build Schwab-shaped chains the way
  `services/options_svc/tests/test_greeks_census.py::_chain` does, with
  `symbol` fields like `"SPXW  261019C07700000"`):
  - `test_healthy_chain_is_returned_untouched`: same object identity, source `"schwab"`.
  - `test_missing_chain_is_filled_and_drives_real_gex`: run the result through
    the real `gamma_tool.GammaEngine().calc_all_from_chain(chain, now=...)`
    and assert `abs(net_total) > 0` with non-empty walls.
  - `test_volatility_lands_in_percent`: `iv_pct=31.18` is stored as `volatility == 31.18`.
  - `test_stale_alt_is_refused`: `as_of = now_ts - 91`, `max_age_sec=90` gives source `"none"`, and every gamma is still -999.
  - `test_other_expiry_is_refused`.
  - `test_input_chain_is_not_mutated`.
  - `test_unmatched_root_stays_sentinel_and_is_counted`: a monthly date with
    SPX and SPXW, alt carrying SPXW only. SPX contracts keep -999, and
    `_greeks_filled < _greeks_window`.
  - `test_later_expiries_are_left_alone`.
  - `test_join_ignores_symbol_formatting`: an alt contract with no symbol at all still matches.
- [ ] **Step 2: Run** `cd options-scanner && python -m pytest tests/test_greeks_fill.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `fill_chain` and `is_filled`.** Deep-copy the chain
  (`copy.deepcopy`; it is called at most four times a minute) and write only
  `gamma`, `delta` and `volatility` on matched nearest-expiry contracts.
- [ ] **Step 4: Run it.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(gamma): fill_chain writes a second source's Greeks into an empty nearest expiry`.

### Task 4: Proxy side: parse, book and tokens (no I/O)

**Files:**
- Create: `schwab-proxy/ts_greeks.py`
- Modify: `repo_paths.py` (`TRADESTATION_TOKENS = REPO_ROOT / "schwab-proxy" / "tradestation_tokens.json"`), `.gitignore` (that file)
- Test: `schwab-proxy/tests/test_ts_greeks.py`, plus `schwab-proxy/tests/fixtures/ts_chain_stream.ndjson` (recorded-shape lines built from the spec's sample, including a `Heartbeat` line and an error line)

**Interfaces:**
- Produces: `parse_message(msg: dict) -> dict | None`. It returns
  `{"side","strike","root","expiry","gamma","delta","iv_pct","oi"}`, or None
  for a heartbeat, an error, or a message without a usable strike or side.
  Number strings become floats; `iv_pct = 100 * float(ImpliedVolatility)`; a
  non-finite or missing Greek gives None for that field. `side` comes from
  `Side` ("Call"/"Put" become "C"/"P"); `strike` from `Strikes[0]`; `root`
  and `expiry` from `Legs[0]`. **Verify the `Legs` field names against one
  live message during rollout (design: left to the builder). Until then the
  fixture defines them, and `root`/`expiry` are None-tolerant.**
- Produces: `class GreeksBook` with `put(symbol: str, expiry: str, row: dict, ts: float) -> None`,
  `snapshot(symbol: str, expiry: str) -> dict` (the `/alt/greeks` shape from
  Task 3, `as_of` = newest tick, and rows whose gamma is None are omitted), and
  `oldest_age(now: float) -> float | None`. It is thread-safe (one lock).
- Produces: `class TsTokens(path: Path, client_id: str, client_secret: str, session)`
  with `access_token(now: float) -> str`. It refreshes through
  `POST https://signin.tradestation.com/oauth/token`
  (`grant_type=refresh_token`) once fewer than 120 s of the 20-minute life are
  left, keeps the refresh token unless a new one is returned, and writes the
  file atomically with mode 0600.

- [ ] **Step 1: Write the failing tests:**
  - `test_parse_reads_string_numbers_and_converts_iv`: `"0.3118"` gives `31.18`.
  - `test_parse_skips_heartbeat_and_errors`.
  - `test_book_snapshot_shape_and_as_of`.
  - `test_book_drops_rows_without_gamma`.
  - `test_tokens_refresh_near_expiry_and_persist_mode_0600`: a fake session
    records the POST. Skip the mode assertion on Windows with a reason.
  - `test_tokens_keep_old_refresh_token_when_none_returned`.
- [ ] **Step 2: Run** `cd schwab-proxy && python -m pytest tests/test_ts_greeks.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement the module.** No network import at module scope beyond `requests`.
- [ ] **Step 4: Run it.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(proxy): TradeStation chain messages, the Greeks book and token refresh`.

### Task 5: Proxy side: stream workers, `/alt/greeks`, health

**Files:**
- Modify: `schwab-proxy/ts_greeks.py` (the stream loop and manager)
- Modify: `schwab-proxy/schwab_proxy.py` (start it in `startup()`, add the route and the health field)
- Test: `schwab-proxy/tests/test_ts_greeks_stream.py`, plus a case in `schwab-proxy/tests/test_security.py`

**Interfaces:**
- Consumes: Task 4, and `shared.greeks_fallback_config` (Task 2).
- Produces: `class StreamManager(book, tokens, session_factory, clock)` with
  `ensure(symbols: Iterable[str], today: str) -> None`. It keeps one thread per
  symbol on that symbol's nearest expiry. The expiry comes from
  `GET /v3/marketdata/options/expirations/{underlying}` (the first one on or
  after `today`, CT). A thread whose expiry or symbol is no longer wanted is
  stopped, and a new one is started. It also has `stop_all() -> None` and
  `status() -> dict` (`{"streams_up": int, "oldest_tick_age_sec": float|None}`).
- The stream:
  `GET https://api.tradestation.com/v3/marketdata/stream/options/chains/{ts_underlying(symbol)}`
  with params `expiration=<MM-DD-YYYY>`, `strikeProximity=strike_proximity()`,
  `enableGreeks=true`, `optionType=All`, `stream=True`. It reads `iter_lines`,
  `parse_message` → `book.put`, reconnects on end or error with backoff
  1, 2, 4 … 60 s, and calls `_degrade`-style logging (the proxy's own logger)
  on a swallowed error.
- Produces: the route `GET /alt/greeks?symbol=&expiry=`,
  `dependencies=[Depends(require_secret)]`. It returns `book.snapshot(...)`,
  or `404` when mode is `"off"` or the symbol is not configured. It never
  touches `market_store` or the Schwab rate gate.
- Produces: `/health` gains
  `"alt_greeks": {"mode": str, "streams_up": int, "oldest_tick_age_sec": float|None}`.
  It never changes `up`.
- `startup()`: when `mode() != "off"` and `TS_CLIENT_ID`, `TS_CLIENT_SECRET`
  and the token file all exist, start a daemon thread that calls
  `manager.ensure(symbols(), today_ct)` every 60 s. That catches the date
  change (the expiry rollover) and mode changes without a restart. Turning the mode to `"off"` calls `stop_all()`. Without
  credentials, log one WARNING and do nothing.

- [ ] **Step 1: Write the failing tests** (fake `session_factory` serving the fixture lines, fake clock):
  - `test_stream_fills_book_from_fixture`.
  - `test_stream_reconnects_after_end_of_stream`: two connections are recorded.
  - `test_manager_rolls_to_the_new_nearest_expiry_on_date_change`: the
    expirations fake returns today's and tomorrow's dates. Move the clock a
    day, and the old thread stops while a new one opens with tomorrow's
    `expiration`.
  - `test_manager_stops_streams_when_mode_turns_off`.
  - `test_alt_greeks_route_needs_the_secret` (in `test_security.py`).
  - `test_alt_greeks_404_when_off`.
  - `test_health_reports_alt_greeks_without_changing_up`.
- [ ] **Step 2: Run** `cd schwab-proxy && python -m pytest tests/test_ts_greeks_stream.py tests/test_security.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run the whole proxy suite** (`cd schwab-proxy && python -m pytest -q`). Expected: PASS, with the failing set unchanged from `docs/reference/testing.md`'s baseline (none).
- [ ] **Step 5: Commit** `feat(proxy): stream TradeStation chains and serve /alt/greeks (off by default)`.

### Task 6: One-time sign-in tool

**Files:**
- Create: `tools/tradestation_auth.py`
- Test: `tests/test_tradestation_auth.py` (repo root)

**Interfaces:**
- Produces: `authorize_url(client_id: str, redirect_uri: str) -> str`, built
  for `https://signin.tradestation.com/authorize` with
  `response_type=code`, `audience=https://api.tradestation.com`, and
  `scope=openid offline_access MarketData`.
- Produces: `exchange(code: str, client_id: str, client_secret: str, redirect_uri: str, session) -> dict`,
  which posts to `/oauth/token` with `grant_type=authorization_code`.
- Produces: `main()`. It reads `TS_CLIENT_ID` / `TS_CLIENT_SECRET` / `TS_REDIRECT_URI`
  (default `http://localhost:3000`) from the environment, prints the URL,
  accepts the pasted code **or** the whole redirect URL, writes
  `repo_paths.TRADESTATION_TOKENS` mode 0600, and prints nothing secret.

- [ ] **Step 1: Write the failing tests:** `test_authorize_url_has_offline_access_and_audience`;
  `test_exchange_posts_the_code` (fake session);
  `test_main_accepts_a_full_redirect_url` (monkeypatched input and session,
  `tmp_path` tokens file). Add the CI row if `tests/` at the root is not
  already covered (`tests/test_ci_covers_every_suite.py`).
- [ ] **Step 2: Run** `python -m pytest tests/test_tradestation_auth.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run it.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(tools): one-time TradeStation sign-in`.

### Task 7: The collector fills, and every row records its source

**Files:**
- Modify: `schwab-proxy/proxy_client.py` (a `get_alt_greeks` method on the client `fetch_minute` uses)
- Modify: `options-scanner/gex_collector.py` (`fetch_minute` fills; `derive_minute` writes the source)
- Modify: `options-scanner/gex_history_db.py` (a `greeks_source TEXT` column via `init_schema`'s ALTER list; `insert_snapshot` writes `summary.get("greeks_source")`; a new `filled_minutes(conn, symbol, view, session_date) -> set[int]`)
- Test: `options-scanner/tests/test_gex_collector_greeks_fill.py`, `options-scanner/tests/test_gex_history_db.py` (add cases)

**Interfaces:**
- Consumes: `fill_chain` / `is_filled` (Task 3), `greeks_missing` (Task 1), the config (Task 2).
- Produces: `ProxyClient.get_alt_greeks(symbol: str, expiry: str) -> FakeResponse`
  (`_get("/alt/greeks", ...)`, **never** `priority=True`).
- `fetch_minute`, after the carry and before building `RawMinute`: when
  `mode() == "on"`, for each `(symbol, chain)` with `symbol in symbols()` and
  `greeks_missing(chain, today)`, it fetches the alt payload for that chain's
  nearest expiry and replaces the chain with `fill_chain(...)[0]`. A failed
  request counts as `alt=None`. No request is made when the census says Greeks are present.
- `derive_minute`: `skew_fields["greeks_source"] = chain.get("_greeks_source")`
  when `is_filled(chain)`, else None (so NULL means Schwab).
- Produces: `gex_history_db.filled_minutes(...) -> set[int]`: the `ts` of
  rows with `greeks_source IS NOT NULL` for that symbol, view and session.

- [ ] **Step 1: Write the failing tests:**
  - `test_mode_off_never_asks_for_alt_greeks`: the fake client records calls;
    a -999 chain is left as it is.
  - `test_mode_on_fills_a_flagged_configured_symbol`: the `RawMinute`'s chain
    for SPY has real gammas and `_greeks_source == "tradestation"`.
  - `test_mode_on_leaves_a_healthy_chain_and_sends_no_request` (Review Focus 5).
  - `test_mode_on_skips_symbols_not_configured`.
  - `test_filled_rows_store_their_source_and_schwab_rows_store_null`.
  - `test_replay_of_a_filled_minute_is_identical` (Review Focus 4): derive
    once; JSON round-trip the `RawMinute` the way `raw_tee` stores it; derive
    again into a second in-memory DB; compare every snapshot row.
  - `test_filled_minutes_returns_only_filled_ts`.
  - `test_schema_adds_greeks_source_to_an_old_db`.
- [ ] **Step 2: Run** `cd options-scanner && python -m pytest tests/test_gex_collector_greeks_fill.py tests/test_gex_history_db.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it.** Keep the fill call in a small helper in
  `greeks_fill.py` (`fill_minute_chains(client, fetched, today, now_ts) -> list`)
  so `fetch_minute` grows by a few lines.
- [ ] **Step 4: Run the options-scanner suite and the options_svc suite** (each on its own). Expected: PASS.
- [ ] **Step 5: Commit** `feat(gamma): the collector fills an empty nearest expiry and records the source`.

### Task 8: Greek-reading hooks skip a filled chain

**Files:**
- Modify: `services/options_svc/collect_derive.py` (`_on_chain`)
- Test: `services/options_svc/tests/test_collect_derive.py` (add cases)

**Interfaces:**
- Consumes: `greeks_fill.is_filled` (Task 3). It is imported lazily inside
  `build_context`, like the existing imports there.
- In `_on_chain`: `stash_tick_chain`, `eth`, `flow_sides_tick` and
  `traded_premium` run as today. `detect_uoa`, `detect_big_delta` and
  `hiro_tick_row` are **skipped** when `is_filled(chain)`. (`eth` reads only
  a root flag. The skew readings in `options-scanner/flow_skew.py` that use
  `delta`/`volatility` run in `derive_minute` and are stored with the row's
  `greeks_source`, as the design says.)

- [ ] **Step 1: Write the failing tests:** `test_filled_chain_still_stashed_for_the_page`;
  `test_filled_chain_skips_uoa_big_delta_and_hiro` (hooks double records the calls);
  `test_schwab_chain_runs_every_hook_as_before`.
- [ ] **Step 2: Run** `python -m pytest services/options_svc/tests/test_collect_derive.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run it.** Expected: PASS.
- [ ] **Step 5: Commit** `fix(flow): Greek-reading detectors ignore a chain filled from a second source`.

### Task 9: Shadow mode

**Files:**
- Create: `services/options_svc/greeks_shadow.py`
- Modify: `services/options_svc/compute.py` (where `on_minute` is set, around line 4973: chain the tee and the shadow; at most 4 lines)
- Test: `services/options_svc/tests/test_greeks_shadow.py`

**Interfaces:**
- Consumes: `fill_chain` (Task 3), `ProxyClient.get_alt_greeks` (Task 7), the config (Task 2), `shared.bus`.
- Produces: `compare(schwab_chain: dict, filled_chain: dict, today: str, now: datetime) -> dict`.
  It is pure and returns `{"matched": int, "gamma_rel_p50": float|None,
  "gamma_rel_p90": float|None, "net_gex_schwab": float, "net_gex_alt": float,
  "flip_schwab": float|None, "flip_alt": float|None, "flip_gap": float|None,
  "walls_agree": bool}`. The relative difference is
  `abs(alt - schwab) / abs(schwab)` over matched contracts within 10 strikes of
  spot that have a non-zero Schwab gamma; net GEX and the flip come from the
  real `GammaEngine` on each chain.
- Produces: `on_minute(minute, client, bus, now) -> None`. It runs only when
  `mode() == "shadow"`. For each configured symbol whose chain is **healthy**,
  it fetches alt, fills a copy as if the census had said missing (give
  `fill_chain` a `force=True` keyword, default False, added in this task with
  its own test), calls `compare`, and writes
  `cache:options:greeks_shadow:<SYMBOL>` with `skip_unchanged=True` and no
  timestamp in the payload. It accumulates the session's minutes in memory and
  logs **one INFO line per symbol per session** at the first minute at or
  after 15:00 CT: medians of the above, and the walls-agree share. It never
  raises; a swallowed error calls `_degrade.degraded("greeks_shadow")`.
- Produces: `chain_on_minute(*fns) -> Callable` (calls each, isolated).

- [ ] **Step 1: Write the failing tests:** `test_compare_identical_chains_is_zero_gap`;
  `test_compare_scaled_gamma_reports_the_ratio` (alt = 1.1 × Schwab gives p50 ≈ 0.10);
  `test_shadow_never_changes_the_minute` (the `RawMinute` chains are
  byte-identical before and after); `test_shadow_writes_the_view_per_symbol`;
  `test_shadow_logs_one_line_per_symbol_after_the_close`;
  `test_shadow_off_and_on_modes_do_nothing`;
  `test_fill_chain_force_fills_a_healthy_chain` (in `options-scanner/tests/test_greeks_fill.py`).
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement it.** Wire it in `compute.py` with `chain_on_minute(raw_tee.maybe_write, partial(greeks_shadow.on_minute, client=..., bus=...))`,
  and keep the existing `hooks_ms` timing branch working.
- [ ] **Step 4: Run** the options_svc suite, including `test_compute_module_shape.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(gamma): shadow mode compares TradeStation's Greeks with Schwab's`.

### Task 10: The snapshot carries the source; the page labels it

**Files:**
- Modify: `services/options_svc/greeks_census.py` (add `apply_source`)
- Modify: `services/options_svc/compute.py` (`_gamma_fetch_chain` fills in mode `on`; `gamma_snapshot` calls `apply_source` in place of the two-line `if greeks_missing … mark_no_greeks` block, which nets zero lines)
- Modify: `webgui/pages/options/gamma.py` (`summary_text` only, **not** `render`)
- Modify: `webgui/page_help.py`
- Test: `services/options_svc/tests/test_greeks_census.py`, `services/options_svc/tests/test_compute.py`, `webgui/tests/test_options_gamma.py`

**Interfaces:**
- Consumes: Tasks 1, 3 and 7.
- Produces: `apply_source(snap: dict, chain: dict, today: str, wall_window_pct: float) -> None`, in place:
  - It sets `snap["greeks_source"]` to `"schwab"`, `"tradestation"` or `"none"`
    (`"none"` when the census says missing and the chain is not filled).
  - On `"none"` it calls `mark_no_greeks(snap["views"])`, which is today's behaviour.
  - When filled, it writes into every view's `summary` the keys
    `greeks_source`, `greeks_filled` and `greeks_window`. It clears a view's
    walls if the filled strikes do not span `spot × (1 ± wall_window_pct/100)`,
    with `wall_window_pct` from `greeks_fallback_config.wall_window_pct()`.
    The page's walls are searched unbounded: `compute.gamma_walls` passes no `max_pct`.
  - It sets `snap["filled_ts"]` to the sorted list from
    `gex_history_db.filled_minutes` for the GEX view (an empty list on any failure).
- `summary_text`: when `summary.get("greeks_source")` is not in `(None, "schwab")`,
  it appends `"Greeks: TradeStation (N of M filled)"`. The source name is
  title-cased from the key, and Massive later reads `"Massive, 15-min IV"`.
  The page's `render` does not change. `tests/test_render_size.py` must still pass.
- `page_help.py`: one sentence under Dealer Positioning's bars line saying what
  the label means and that Schwab sent no Greeks for that chain.

- [ ] **Step 1: Write the failing tests:**
  - `test_apply_source_schwab_changes_nothing`.
  - `test_apply_source_none_marks_no_greeks` (today's -999 case).
  - `test_apply_source_filled_labels_every_view_and_keeps_walls_in_window`.
  - `test_apply_source_clears_walls_when_window_short`.
  - `test_gamma_fetch_chain_fills_in_mode_on_only`.
  - `test_summary_text_names_the_fallback_source`.
  - `test_summary_text_unchanged_for_schwab`.
  - `test_page_help_mentions_the_greeks_label`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run** the options_svc suite and the webgui suite (no edits while the webgui suite runs). Expected: PASS.
- [ ] **Step 5: Commit** `feat(gamma): the snapshot names its Greeks source and the page labels it`.

### Task 11: Nothing fallback-derived reaches the public origin or a post

**Files:**
- Modify: `services/options_svc/handlers.py` (`_publish_gamma`; the gate itself lives in a new `services/options_svc/gamma_public_gate.py`)
- Test: `services/options_svc/tests/test_gamma_public_gate.py`

**Interfaces:**
- Consumes: `snap["greeks_source"]` and `snap["filled_ts"]` (Task 10), and `public_uses_fallback()` (Task 2).
- Produces: `public_copy(snap: dict, rows_by_view: dict) -> tuple[dict, dict]`.
  When `public_uses_fallback()` is False and `greeks_source` is not
  `"schwab"`, it returns a deep copy of the snapshot with
  `mark_no_greeks(views)` applied, `greeks_source = "none"`, and `filled_ts`
  removed, plus `rows_by_view` with every row whose `ts` is in `filled_ts`
  dropped. Otherwise it returns its inputs with only `filled_ts` removed.
- In `_publish_gamma`: a target whose `main_key` (or history key) starts with
  the `gamma_pub` prefix (`gamma_pub_key("")` / `gamma_pub_history_key`)
  publishes the `public_copy`. The private targets publish as they do now.
- **Inventory step:** run
  `grep -rn "gamma_snapshot\|CACHE_GAMMA\|gamma_pub\|x_post.post" services/options_svc --include=*.py`
  and list in the commit message every reader that renders positioning
  outside the private app: X posts, report images, the public pages. Each one
  must read a published (public-safe) key or check `greeks_source`. Add one
  test per such reader found; if there are none beyond `_publish_gamma`, the
  commit says so.

- [ ] **Step 1: Write the failing tests:** `test_public_copy_hides_fallback_views_and_rows`;
  `test_public_copy_passes_schwab_data_through`;
  `test_publish_gamma_sends_the_public_copy_to_pub_keys_only` (fake bus records
  `cache_set`); `test_public_uses_fallback_true_publishes_as_is`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement it, then do the inventory step.**
- [ ] **Step 4: Run** the options_svc suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(gamma): fallback Greeks stay off the public origin and out of posts`.

### Task 12: Manuals, CLAUDE.md, CHANGELOG

**Files:**
- Modify: `docs/manuals/` User Guide (what the label means, and that nothing
  changes until the mode is turned on); the Technical Reference (TradeStation's
  model is Bjerksund–Stensland priced off the ask; the `$IRX.X` rate; the fill
  rule; the shadow figures); the API / Developer Reference (`GET /alt/greeks`,
  `cache:options:greeks_shadow:<SYMBOL>`, the `greeks_source` column and snapshot key)
- Modify: `CLAUDE.md`: one line under "Options, paper books and trade
  selection → Other standing facts" (a filled chain carries `_greeks_source`;
  Greek-reading detectors skip it; replay stays exact because the fill happens
  in `fetch_minute`), and the `/alt/greeks` route under the proxy's
  Running/Secrets notes. Keep under the size test.
- Modify: `docs/CHANGELOG.md` (one entry), `docs/webgui-routes.md` (the
  Dealer Positioning label)
- Test: `tests/test_claude_md_size.py`, and the manuals build (`python docs/manuals/build_docs.py`)

- [ ] **Step 1: Write the docs.**
- [ ] **Step 2: Run** `python -m pytest tests/test_claude_md_size.py -q` and `python docs/manuals/build_docs.py`. Expected: PASS / builds with no errors.
- [ ] **Step 3: Commit** `docs: the Greeks fallback`.

### After the tasks: verify, merge, roll out (the user's steps are marked)

- [ ] Run every suite on its own, as CLAUDE.md "Tests" lists (proxy, options-scanner, options_svc, webgui, shared, the root `tests/`). Compare the failing set with the baseline (none).
- [ ] Render the page in `tools/ui_harness.py` with a seeded snapshot whose `greeks_source` is `"tradestation"`, and check the label in the summary line. Repeat with `"none"` for the sentence.
- [ ] Merge to `main` and push. **Promote only outside 06:30–15:20 CT.**
- [ ] **User:** request the TradeStation API key (`MarketData`, `offline_access`,
  redirect URI `http://localhost:3000`). Put `TS_CLIENT_ID` / `TS_CLIENT_SECRET`
  in the stack `.env` and run `tools/tradestation_auth.py` on the box.
- [ ] Set `mode = "shadow"` in `config/local/greeks_fallback.toml` (applies
  without a restart). After one full session, read
  `cache:options:greeks_shadow:<SYMBOL>` and the journal lines. While it runs,
  confirm the `Legs` field names on one live message (Task 4's note).
- [ ] **User:** decides on `mode = "on"`.
- [ ] Phase 2 (Massive) is a separate plan, written only if the user asks.
