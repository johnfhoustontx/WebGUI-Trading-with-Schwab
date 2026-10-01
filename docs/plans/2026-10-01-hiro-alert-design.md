# HIRO-style hedging-flow alert — design

**Date:** 2026-10-01
**Status:** approved design, not built
**Scope decided:** Surge alert first, then Flip; index group only ($SPX, SPY, QQQ, IWM).

## What HIRO is, and what this app can build

SpotGamma's HIRO sums, over every option trade, the stock dealers must trade to
hedge it: `side × delta × size × 100 × spot`, where `side` is whether the customer
bought or sold. A customer buying a call makes the dealer buy stock (positive); a
customer buying a put makes the dealer sell stock (negative).

**Schwab gives no time-and-sales tape and no aggressor side.** Every existing flow
measure in this app (Net Prem, crossover, `big_delta`) is unsigned `mark × volume`
(`options-scanner/flow_skew.py`). So this feature is a **model of HIRO**, built from
the 1-minute chain poll, and the screen says so. It is not SpotGamma's number.

## Approach

**Chain-poll based (approach B), on the index group.** Chosen over the level-one
option stream (approach A: closer per-trade classification, but SPY/QQQ only, front
expiry near the money, and no certainty that $SPX options stream cleanly) and over a
hybrid (two pipelines to tune). The poll already fetches the full strike ladder,
today through +7 days, for these four symbols every minute
(`gex_collector.poll_once`, `to_date = today + 7`), so the feature adds **no Schwab
calls**. Widening to the watchlist later is mostly config, but needs a
symbol-relative Surge threshold, as `big_delta` has.

## 1. Measurement

Runs in the `on_chain` hook of the 1-minute GEX poll, beside `detect_big_delta`, for
the symbols in `[hiro].symbols`.

Per contract, per minute:

1. **New volume** `dv = totalVolume − previous totalVolume`. The service keeps the
   previous reading per (symbol, contract symbol), reset each session. A contract's
   first reading after a restart only seeds the baseline — never books the day's
   volume into one minute. A negative `dv` is dropped.
2. **Side**, only when `tradeTimeInLong` advanced since the last poll:
   `last ≥ ask` → customer bought (+1); `last ≤ bid` → customer sold (−1); otherwise
   the side of the midpoint `last` sits on. Exactly at the midpoint, no new trade, or
   an unusable quote → **unclassified**: its volume is counted separately and
   contributes nothing, never a guessed side.
3. **Hedge impact ($)** `= side × delta × dv × 100 × spot`, with the contract's own
   SIGNED delta (call positive, put negative), which gives the dealer direction with
   no call/put branch.
4. **Dropped:** non-finite delta, `|delta| > 1`, Schwab's `−999` sentinel, a
   non-finite or non-positive spot. (The NaN-pins-the-bound class, CLAUDE.md.)

Per symbol per minute the series records: `ts`, `spot`, `impact` (net $),
`cum` (since the open), `classified_vol`, `unclassified_vol`.

**Session:** regular hours, 08:30–15:00 CT only (`shared.market_calendar` window, not
a literal). Off-hours the chain's `underlyingPrice` can be stale and index open
interest reads zero.

**Storage:** one row per symbol per minute in a small SQLite table (alongside the GEX
history), so a restart does not reset the running total. Published as
`cache:options:hiro` (`skip_unchanged`, no timestamp inside the payload).

**Accepted limits, stated on screen and in the manuals:** one side per contract per
minute, not per trade; expiries beyond 7 days are not counted; volume during a restart
gap is lost; a model of HIRO, not SpotGamma's.

## 2. Alert rules

All values in `[hiro]` of `config/flow_alerts.toml`, with built-in defaults in
`flow_alerts._DEFAULTS`, and an entry each in `webgui/config_schema.py`. The defaults
are guesses until the daily report measures them.

### Surge (`kind = "surge"`)

- Window: rolling 15-minute sum of `impact`.
- Normal size σ: the spread of the symbol's own 15-minute sums over its last 5 stored
  sessions. Until 5 sessions exist, today's data, and only after ≥ 30 minutes of it;
  otherwise the rule does not run.
- Fires when the 15-minute sum is ≥ `k × σ` (default 3) **and** clears
  `min_notional` (an absolute floor against a dead tape) **and** the window's
  unclassified share ≤ `max_unclassified` (default 0.5).
- Direction: `dealers_buying` (upward pressure) or `dealers_selling`.
- Cooldown: 30 minutes per symbol and direction.
- Payload: symbol, direction, dollars, multiple of σ, spot, unclassified share.

### Flip (`kind = "flip"`)

- State per symbol: dealers `net_buying` or `net_selling` for the day.
- Switches only when `cum` clears zero by a dead zone of `flip_band × σ` — the
  `gamma_flip` hysteresis pattern, scaled per symbol.
- Held until `flip_not_before` (default 09:00 CT): early in the session the total is
  tiny and crosses zero constantly.
- Cooldown: 60 minutes per symbol.
- After a restart the state is rebuilt from the stored minutes, so no false flip.

### Delivery

- Both fire to the Flow Alerts screen as type `hiro`, with their own Type filter and
  colour.
- `push = false` at ship. When enabled, Surge has its own `push_k` (e.g. 4), the
  `big_delta_should_push` pattern: the screen gets every fire, the phone only the
  strongest.

## 3. Display, documentation, testing, validation

**Display:** Flow Alerts rows only. A hedging-flow line on the Gamma page's Flow
subtab is deferred until the alert has proved itself.

**Documentation, same change:** `webgui/page_help.py`, the User Guide, the Technical
Reference (the formula and limits), and an Options Glossary entry for HIRO.

**Testing (TDD on pure functions):** new volume, side, impact, Surge, Flip. Fixtures
use Schwab's real chain shape. Named cases: first reading after restart books
nothing; NaN / `|delta| > 1` / `−999` dropped; unclassified-heavy window does not
fire; restart does not fire a false Flip; Flip held before `flip_not_before`;
cooldowns. Discriminating config test: monkeypatch the loader and re-run, not
`settings.X == config.X`.

**Verification:** no dev stack exists on vps2, so this is verified on prod after it
lands — acceptable because the change is additive and screen-only. Redis-driven:
`cache:options:hiro` fills during regular hours; `hiro` alerts reach
`cache:options:flow_alerts`.

**Validation before any push:** a daily report (the `big_delta` instrumentation
pattern) giving fires per symbol at several `k`, the unclassified share, and the
forward 5- and 15-minute spot return after each Surge in its own direction. If
hedging flow does not lead price on this data, the alert stays screen-only or is
removed.
