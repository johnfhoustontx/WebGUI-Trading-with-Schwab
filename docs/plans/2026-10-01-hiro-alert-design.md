# HIRO-style hedging-flow alert — design

**Date:** 2026-10-01
**Status:** built 2026-10-01 and running **quiet**: on the Flow Alerts screen only — no
phone push, no Desk speech, not on the public screens, not counted in any ranking —
until the daily report (section 3) validates it.
**Scope decided:** Surge alert first, then Flip; index group only ($SPX, SPY, QQQ, IWM).
Plan: `docs/plans/2026-10-01-hiro-alert-plan.md`.

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
the symbols in `[hiro].symbols` (`compute.hiro_tick_row` over the pure
`services/options_svc/hiro.measure_chain`).

Per contract, per minute:

1. **New volume** `dv = totalVolume − previous totalVolume`. The service keeps the
   previous reading per (symbol, contract symbol) in memory, cleared when the CT
   session date changes. The stored reading is a **high-water mark**: volume never
   falls within a session, so a glitch read of 0 books nothing and cannot re-book the
   day later. A `dv` of zero or less books nothing.
2. **Side**, only when `dv > 0` — new volume means new trades printed, and `last`
   is the latest of them, so no trade-time field is needed (`tradeTimeInLong` was
   considered and dropped: no code here reads it, and `dv > 0` says the same thing).
   `last ≥ ask` → customer bought (+1); `last ≤ bid` → customer sold (−1); otherwise
   the side of the midpoint `last` sits on. Exactly at the midpoint (compared with a
   tiny tolerance, so a binary rounding of the midpoint cannot pick a side), or an
   unusable quote — missing, non-finite, a bid below zero, a `last` or ask of zero
   or less, or a LOCKED or crossed quote (ask ≤ bid) — → **unclassified**: its volume
   is counted separately and contributes nothing, never a guessed side. ⚠ The
   bid/ask are this minute's, and the trade may be up to a minute older than them —
   one more reason the label is coarse.
3. **Hedge impact ($)** `= side × delta × dv × 100 × spot`, with the contract's own
   SIGNED delta (call positive, put negative), which gives the dealer direction with
   no call/put branch.
4. **Unclassified, not dropped:** volume whose delta is non-finite, `|delta| > 1`
   (Schwab's `−999` sentinel), or of the wrong sign for its right (a call with a
   negative delta) is counted in `unclassified_vol`, so a window never looks better
   measured than it was. A non-finite or non-positive **spot** makes the whole minute
   unusable: nothing is written and the volume baseline is left where it was, so the
   next good minute books that volume. (The NaN-pins-the-bound class, CLAUDE.md.)

Per symbol per minute the table records: `ts`, `spot`, `impact` (net $),
`classified_vol`, `unclassified_vol`. The running total since the open is not stored;
it is summed from the rows (`hiro.running_total`, which skips a non-finite minute).

**Seed minutes write nothing.** A contract's first reading only seeds its baseline, so
a restart can never book the day's volume into one minute. A symbol whose last good
minute is more than **150 s** old (`compute.HIRO_MAX_GAP_SEC`, about 2.5 polls — one
dropped slot still books) is **re-seeded** rather than measured, because several
minutes of volume booked into one would carry one minute's bid/ask label. A minute in
which the symbol had no baseline at all (the first poll of the day, or the first after
a gap) is a pure seed and writes **no row**: written as a zero it would read as a quiet
minute to the baseline. A minute where only some contracts are new is a real row.

**Same-minute rows accumulate.** Two ticks can land in one minute (one at X:59.9, the
next at X+1:30). Their rows share a `ts`, and the insert ADDS impact and both volume
columns rather than replacing them (spot takes the newer value): the volume memo has
already moved past the first tick, so replacing would lose that volume for good. A
missing or non-finite impact or volume refuses the whole insert, because SQLite stores
an infinity silently and one would poison every baseline it touched.

**Session:** regular hours, 08:30–15:00 CT only (`shared.market_calendar` window, not
a literal). Off-hours the chain's `underlyingPrice` can be stale and index open
interest reads zero.

**Storage:** one row per symbol per minute in a `hiro_minutes` table inside
`gex_history.db` (reusing its `connect()` and the collector's write connection), so a
restart does not reset the running total. It has its OWN retention,
`[hiro].keep_sessions` (default 20), because the GEX snapshots keep only 5 sessions
and the Surge baseline needs 5 sessions BEFORE today. The purge never keeps fewer than
`baseline_sessions + 1`, so a small `keep_sessions` cannot starve the baseline.

**The summary view.** `cache:options:hiro` (`skip_unchanged`) holds
`{date, symbols: {SYM: {ts, spot, impact, cum, window_impact, sigma, mult,
unclassified_share}}}`, where `ts` is that symbol's newest stored minute, not a publish
time. It is published only once a symbol has a stored minute. A reader must gate on
`date` being today **and** on each symbol's `ts` against the clock, because a stalled
collector also leaves a today-dated view. No page reads it yet.

**Accepted limits, stated on screen and in the manuals:** one side per contract per
minute, not per trade; expiries beyond 7 days are not counted; volume during a restart
gap is lost; a model of HIRO, not SpotGamma's.

**The opening minute only seeds.** Collection runs from 08:00 CT, but measurement
starts with the first poll inside regular hours, and that poll seeds every contract's
baseline from its cumulative volume. So the trades printed between 08:30:00 and that
first poll are never booked: the day's measured flow starts up to a minute after the
bell. Booking them would need a volume reading taken at 08:30:00 exactly, which the
poll does not give.

## 2. Alert rules

All values in `[hiro]` of `config/flow_alerts.toml`, with built-in defaults in
`flow_alerts._DEFAULTS`, and an entry each in `webgui/config_schema.py`. The defaults
are guesses until the daily report measures them.

Two alert TYPES, `hiro_surge` and `hiro_flip`, so the Flow screen's Type filter,
tone map and spoken phrases need no second key. Reader-facing names: **Hedging
surge** and **Hedging reversal** — never "flip", which the screen already uses for
the gamma flip.

### Surge (`hiro_surge`)

- Window: rolling 15-minute sum of `impact`.
- Normal size σ: the **root-mean-square** of the symbol's own full 15-minute sums over
  its last 5 stored sessions before today. RMS rather than a standard deviation
  because hedging flow's natural centre is zero, and a day-long drift is the very
  thing being measured, not noise to subtract. A "full" window is one that ends at
  least a window's length after the session's first stored row; the partial windows
  of the first minutes would shrink σ. Until 5 prior sessions exist (or when they
  give no usable full window), today's full windows, and only after ≥ 30 stored
  minutes (`min_minutes`); otherwise the rule does not run. The prior-session σ cannot
  change within a day, so the handler memoizes it per symbol per day.
- Fires when the 15-minute sum is ≥ `k × σ` (default 3) **and** clears
  `min_notional` (an absolute floor against a dead tape) **and** the window's
  unclassified share ≤ `max_unclassified` (default 0.5) **and** the newest stored row
  is at most **120 s** old against the clock (`hiro.FRESH_ROW_SEC`, two polls). Rows
  stop at the 15:00 close while detection keeps running, and a stalled collector
  freezes the newest row; without the clock check the same old window would re-fire
  every time its cooldown lapsed. A non-finite window or σ never fires.
- Direction: `dealers_buying` (upward pressure) or `dealers_selling`.
- Cooldown: 30 minutes per symbol and direction.
- Payload: `symbol`, `side`, `ts`, `spot`, `impact` (the window's dollars), `mult`
  (multiple of σ), `window_min`, `unclassified_share`.

### Flip (`hiro_flip`)

- State per symbol: dealers `buying` or `selling` for the day.
- Switches only when `cum` clears zero by a dead zone of `flip_band × σ` — the
  `gamma_flip` hysteresis pattern, scaled per symbol.
- Held until `flip_not_before` (default 09:00 CT): early in the session the total is
  tiny and crosses zero constantly. The first state after that is the baseline and
  never alerts.
- The running total counts from the session's first stored row; only the evaluation
  waits for `flip_not_before`. A non-finite minute is skipped, never added (one NaN
  would make the total NaN for the rest of the day and freeze the state).
- **Stateless:** every tick replays today's stored minutes through the hysteresis and
  alerts only on a transition that is (a) newer than the last one seen, recorded in
  the shared cooldown map as `hiro_flip_seen:<SYM>`, and (b) at most 2 minutes old
  against the clock (`hiro.FLIP_MAX_AGE_SEC`). So a restart cannot fire a false flip,
  and a σ that moves intraday (today-fallback) cannot surface an old one. The seen
  marker deliberately has no `|`, so it can never be read as an alert event by the
  per-symbol counts (below).
- Cooldown: 60 minutes per symbol. A transition inside the cooldown is marked seen
  and dropped, not deferred.
- Payload: `symbol`, `side` (`to_buying` / `to_selling`), `ts`, `spot`, `cum` (the
  running total at the transition).

### Delivery

- Both fire to the Flow Alerts screen as two types, `hiro_surge` and `hiro_flip`,
  each with its own entry in the Type filter and its own colour by side (green for
  dealers buying, red for selling).
- `push = false` at ship. When enabled, Surge has its own `push_k` (default 4), the
  `big_delta_should_push` pattern: the screen gets every fire, the phone only the
  strongest; a reversal is pushed whenever `push` is on (`flow_alerts.hiro_should_push`,
  which fails closed — only a real `true` opens it).
- **Push category `flow_hiro`.** Both types route to one category in
  `config/notify.toml` (`[channels.flow_hiro]`, Discord and Telegram), shown in
  Settings → General → Push notifications as **Hedging flow**. It sits below the
  `[hiro].push` gate: the category switch can only silence a channel, never open a
  push the gate refused. Dealers buying is the green embed, selling the red.
- **Never a chime or toast.** Both types are in `webgui/alerts._QUIET_FLOW_TYPES`
  beside `big_delta`, decided by TYPE, so turning `push` on does not make the browser
  chime.

### Quiet, private, and out of every ranking

**Two flags the service stamps on every HIRO alert**, so Tier 1 decides from the
alert and never reads the service's config:

- `quiet` = `[hiro].push` is not `true`. The Desk glows a quiet row but does not
  **speak** it, and it is neither named nor counted in "plus N more"
  (`desk.fold_flow_arrivals`, decided by the flag, not the type, so any future quiet
  alert is silent too). Owner decision: an unvalidated model must not talk.
- `public` = `[hiro].public` is `true` (default off). An alert stamped
  `public: False` is hidden wherever `shell.hides_non_public()` is true: on the public
  live screens (`/flow`, and the Desk's flow panel) **and** in any render carrying the
  gallery-capture cookie (`ns_capture=1`), because a capture is published to the
  neuralstrike.co gallery. The one filter is `pages/options/flow._shown`, which every
  flow-row reader goes through via `flow.alert_rows`; it reads the cookie, so it must
  run in the page context, never inside `run.io_bound`. While hiding, the public
  Type picker leaves out a HIRO kind with no visible row. An alert with no `public`
  key stays visible (every older alert). Owner decision: private until validated.
  Turning `public` off later leaves alerts already published visible there until the
  list resets overnight.

Both flags fail closed: only a real `true` in the config opens either.

**Out of every ranking while unvalidated.** `compute._count_flow_alerts` (the per-symbol
count behind the Opportunity Board's **Flow alerts** column, `n_alerts`, and Hotness)
skips every cooldown key whose second segment starts with `hiro_`, and
`compute._notable_movers` (the EOD briefing's mover counts) skips every `hiro_*`
alert. A model that has not been shown to lead price must not move a ranking.

## 3. Display, documentation, testing, validation

**Display:** Flow Alerts rows only — which also reach the Desk's flow panel and the
Symbol page's flow band, since both draw `flow.alert_rows`. Every HIRO row says it is
a model: its dollar figure carries `≈` and its detail ends in "model". A
hedging-flow line on the Gamma page's Flow subtab is deferred until the alert has
proved itself.

**Documentation, same change:** `webgui/page_help.py`, the User Guide, the Technical
Reference (the formula and limits), and an Options Glossary entry for HIRO.

**Testing (TDD on pure functions):** new volume, side, impact, Surge, Flip. Fixtures
use Schwab's real chain shape. Named cases: first reading after restart books
nothing; NaN / `|delta| > 1` / `−999` counted as unclassified; unclassified-heavy window does not
fire; restart does not fire a false Flip; Flip held before `flip_not_before`;
cooldowns. Discriminating config test: monkeypatch the loader and re-run, not
`settings.X == config.X`.

**Verification:** no dev stack exists on vps2, so this is verified on prod after it
lands — acceptable because the change is additive and screen-only. Redis-driven:
`cache:options:hiro` fills during regular hours; `hiro_surge` / `hiro_flip` alerts
reach `cache:options:flow_alerts`.

**Validation before any push: the daily report.** `tools/hiro_report.py`, run by the
generated `trading-<env>-hiro-report.timer` at `[slots.hiro_report]` (16:10 CT,
Monday to Friday, `Persistent=true`), writes
`options-scanner/data/hiro_report/<date>/report.md`. It reads `gex_history.db`
read-only and nothing else (no proxy, no Redis), so a missed day can be re-run with
`--date` while `[hiro].keep_sessions` still holds its minutes. With no `--date` it
reports the newest CLOSED session (today after the 15:00 close, else the previous
trading day), so a catch-up run at boot reports the day it missed. Other flags:
`--force` (a weekend or holiday given with `--date`), `--out`, `--k`, `--horizons`.

It **replays** the live rules minute by minute — `hiro.detect_surge` /
`hiro.detect_flip` with the clock set to that minute, the handler's σ rule (prior
sessions, else today's minutes as live had them at that minute) and its cooldowns —
and re-implements none of them, so it cannot drift from what the Flow screen showed.
Per symbol: minutes measured, the share of volume with no buy/sell label, σ and where
it came from, how many Surges would fire at each `k` in a grid (2, 2.5, 3, 3.5, 4, 5),
and the reversals.

**Base-rate method.** A *hit* is a fire after which spot moved the way the modelled
hedging pushed it (up after dealers buying, down after selling) over 5 and 15
minutes, read from the first stored row at or after the horizon (no more than 120 s
past it, else the horizon is unmeasured). A return of exactly zero is neither hit nor
miss and is counted as flat. A hit rate is compared with that day's **base rate for
the same direction** — the share of every measured minute after which spot simply
rose (or fell) over the same horizon, ties left out the same way — never with a coin
flip, because on a day that trended up most buying signals "hit" by doing nothing.
Hits print as "k of n", with a percentage only from 10 decided fires. Because one
day's handful of fires decides nothing, a **Last N sessions** block pools every stored
session (up to `keep_sessions`), each replayed with the prior sessions live had on
that day, with Surges and reversals pooled SEPARATELY (they are correlated) and the
base-rate counts summed. The report exits 1 when nothing was measured (every symbol
failed, or a trading day has no stored minute for any symbol — a dead collector).

If hedging flow does not lead price well above its base rate over several sessions,
the alert stays screen-only or is removed. Turning on `push` or `public` is the
owner's call on that evidence.
