# Bought or sold on flow alerts, and next-day open interest — design

**Date:** 2026-10-04
**Status:** approved 2026-10-04, not built.
**Scope decided:** contract-level flow alert rows only (unusual volume and outsized
bet). A per-symbol bought/sold split on the Macro Board and the Opportunity Board was
considered and deferred. Public from the start, behind a switch.
Plan: `docs/plans/2026-10-04-flow-alert-sides-plan.md`.

## The question

Every flow alert says **call or put**, never **bought or sold**, because Schwab
publishes no time-and-sales tape. The reader's question is the second one. This
build answers it as an estimate, from two sources the app already has, and adds the
one free fact that separates opening from closing: the next day's open interest.

## What exists already

- `services/options_svc/hiro.classify_side(last, bid, ask)` labels a contract
  bought, sold or unlabelled from where its last trade sits in the spread. The
  hedging-flow model (2026-10-01) uses it once per contract per minute on the polled
  chain, for four index symbols, and keeps only a per-symbol total.
- `sentiment-dashboard/scoring/order_flow.py` classifies streamed option ticks for
  SPY and QQQ near the money and feeds the trend score. It is not per contract and
  not on any flow screen.
- The proxy's `/stream/options` fans level-one option ticks to subscribers on the
  one shared Schwab stream, and already guarantees a flow subscription cannot drop a
  tracked paper-trade leg.
- The one-minute chain poll hands every FETCHED chain (never a carried one) to
  `detect_uoa` and `detect_big_delta` through `on_chain`. A chain covers today
  through +7 days.
- Alerts live in `cache:options:flow_alerts`, a rolling list for the day. No store
  keeps a contract's open interest from one day to the next.

## Two facts that shaped the design

1. **A stream subscribed at the alert sees only what trades afterwards.** The alert
   fires because the volume has printed. The poll has the whole ladder every minute,
   so only the poll can label the volume that caused the alert. The stream refines
   what follows. Hence both, shown as two figures and never blended.
2. **Most alerts can never get an open-interest reading.** Measured on prod for
   2026-10-02: 175 contract alerts (96 unusual volume, 79 outsized bet), of which
   152 (87%) were 0-DTE and had expired by the next morning. A Friday is the worst
   case, since every stock has a Friday expiry, but the verdict will cover a
   minority of rows on any day. It is built anyway: the 1–7 day alerts are where
   opened-versus-closed matters most.

## 1. Poll tally

New pure module `services/options_svc/flow_sides.py` (stdlib and `shared.numeric`
only; it imports `hiro.classify_side` for the rule and nothing from `compute`).

Per contract, per fetched minute:

- `dv = totalVolume − previous totalVolume`. The stored reading is a high-water
  mark, as in the hedging-flow model.
- `dv > 0` is labelled by `classify_side` on that minute's `last`, `bid`, `ask`, and
  added to the contract's `bought`, `sold` or `unlabelled` total.
- **Volume nobody watched is unlabelled, not skipped** (see section 8): the volume
  a symbol already carries at its first poll of the session, and the volume across
  a poll gap. The gap limit is 1.5 × the collector's slowest tier (450 s), not the
  hedging-flow model's 150 s: a watchlist-only symbol is fetched as rarely as every
  five minutes by design, and that normal step must still be labelled.
- The tally starts at the regular open (08:30 CT) and runs to the end of
  collection, past the 15:00 close, since ETF options trade to 15:15. It does not
  start earlier: before the bell a chain may still carry yesterday's volume for a
  contract that has not traded yet (unmeasured; this is the conservative reading,
  and the hedging-flow model's). It also keeps the contract's latest `totalVolume`
  and its `openInterest`, the latter read in the regular session only (index open
  interest reads zero outside it), and a zero never replaces a positive figure
  already read that session.
- The poll hook works in memory only. It never opens the database, so it cannot
  slow or break a poll; the store work happens once a minute, after the detectors.

State is in memory, keyed by session date, and cleared when the date changes. It is
kept for every contract in every fetched chain, because an alert can fire on any of
them and must then report the volume from before it fired.

`detect_uoa` and `detect_big_delta` each gain an `osi` field (the contract's Schwab
symbol), so an alert is matched to its tally by contract and not by a rebuilt key.

⚠ `services/options_svc/compute.py` sits two lines under its ceiling. The hook is
one call into a sibling module; the plan frees lines if it needs more.

## 2. Stream after the alert

New module `services/options_svc/flow_stream.py`: a daemon thread started with the
options service's scheduler (so the scheduler suppression covers it).

- It holds one SSE connection to the proxy's `/stream/options` for the day's flagged
  contracts and reconnects when the set grows. The set is capped by
  `[sides].stream_max_contracts`; contracts past the cap keep their poll tally only.
- The proxy's option normalizer gains `total_volume` (level-one field 8). Nothing
  else in the proxy changes.
- Level-one ticks after the first are deltas, so the worker merges them per
  contract, then applies the same arithmetic as the poll: volume change, high-water
  mark, first reading seeds, `classify_side` for the label.
- Reconnect with capped backoff, never raises out, as the sentiment consumer does.

Level-one conflates rapid ticks, so this is a finer sample, not a tape.

## 3. Next-day open interest

New table `flow_contract_days` in `gex_history.db` (the precedent `hiro_minutes`
set), with its own retention.

One row per flagged contract per session: session date, alert id, symbol, contract
symbol, call or put, strike, expiry, alert type, fired time, open interest that day,
final volume, the poll tally, the stream tally, and — once resolved — the next open
interest, the date it was read, and the verdict.

- Rows are written when the alert fires and refreshed while the tally moves.
- **Resolution rides the poll.** On a later session date, in regular hours only
  (index open interest reads zero overnight), the first fetched chain for a symbol
  carries the new open interest for its unresolved contracts. No new Schwab call, no
  new scheduler slot.
- ⚠ **Not yet measured: when Schwab's chain starts showing the new open interest.**
  If it lags the open, a first read would repeat yesterday's figure and read as
  "mixed". So the row is re-read on every fetch that day, and the verdict is
  re-derived whenever the figure moves, with a log line. The first live session
  therefore also answers the question.
- **Verdict** = (next open interest − that day's open interest) ÷ that day's volume.
  At or above `opened_ratio` (+0.5): mostly opened. At or below `closed_ratio`
  (−0.5): mostly closed. Between: mixed or traded within the day. A contract whose
  expiry is the alert date: expired, no reading. A contract absent from the later
  chain, or with a non-positive volume: no reading. Never a guessed verdict.
- **Only the previous trading day's rows are read.** A row left unread for a
  session (the service was down, the follow-up was off) gets no reading: set
  against a later day it would be several days of open-interest change divided by
  one day's volume.
- **A restart on the follow-up day keeps re-reading.** Rows whose figure was read
  earlier that day are loaded again with the unread ones. A resolution is kept in
  memory until its write has succeeded, so a locked store costs a minute, not the
  reading.

## 4. Views

- `cache:options:flow_sides` — `{date, contracts: {alert id: {poll: {bought, sold,
  unlabelled}, stream: {bought, sold, unlabelled}, volume}}}`. Published with
  `skip_unchanged`, so it carries no timestamp of its own.
- `cache:options:flow_followup` — the previous session's resolved and unresolved
  rows, published when a row resolves.

The alert list itself is not rewritten each minute; the page joins by alert id.

## 5. Screens

- **Flow Alerts page and the Desk's flow rows.** The detail gains the session share
  from the poll — "bought 62% · sold 30% · unlabelled 8%" — and, once the stream has
  volume, "since the alert: bought 71% of 12,400". The row text is built by one
  function in `pages/options/flow.py`; the Desk composes it and restates nothing.
- **Flow Alerts page only.** A "Previous session" panel: contract, volume,
  bought/sold share, open interest before and after, verdict.
- Both are worded as estimates, as the hedging rows are. The unlabelled share is
  always shown: a reading must not look better measured than it was.

## 6. Public, pushes, speech

- **Public from the start.** `[sides].public = true` ships on; the operator can turn
  it off without a deploy. When off, the service stamps nothing public and the page
  hides the figures on the public origin through the same filter hedging flow uses.
  The public process reads Redis, so the switch is enforced at the service, not only
  on the page.
- **A switch turned off takes effect within a minute.** Changing `public`
  republishes the follow-up view even when no row moved. Turning `[sides].enabled`
  or `[followup].enabled` off publishes an EMPTY, non-public view once, so no
  screen keeps figures from before the switch. Switches turned on mid-session are
  picked up the same way (contracts already flagged join the stream; waiting rows
  are loaded).
- **Phone pushes and Desk speech are unchanged.** The push fires at the alert, when
  the tally is thinnest.

## 7. Configuration

`config/flow_alerts.toml`, each with a `webgui/config_schema.py` entry:

```toml
[sides]
enabled = true
public = true
stream = true                 # stream flagged contracts after they fire
stream_max_contracts = 200    # Friday 2026-10-02 had 175

[followup]
enabled = true
opened_ratio = 0.5
closed_ratio = -0.5
keep_sessions = 20
```

## 8. Failure behaviour

- A measurement failure never breaks collection: the hook is best-effort and counted
  through `_degrade.degraded`.
- A dead stream leaves the poll tally intact and the stream figure frozen at its
  last reading. The stream figure is NOT covered by the "never dropped" rule
  across a service restart: what it had labelled is kept, but the volume that
  printed while the service was down is not added to "since the alert". Across a
  reconnect within one run it is added, unlabelled.
- A second alert on a contract already streamed counts "since the alert" from its
  own alert, not from the first one.
- One stream worker per process: a scheduler loop restarted by its supervisor
  reuses the live one.
- **Known and not handled:** if the first usable chain for a symbol is missing an
  expiry, the next full chain books those contracts' whole volume with one
  minute's label.
- Volume the service did not watch print is **unlabelled**, never dropped: a
  contract's volume from before a restart, and the volume across a poll gap. So
  bought + sold + unlabelled always equals the contract's volume, and a tally that
  missed part of the day says so through its unlabelled share. (Hedging flow
  re-seeds instead, because it sums dollars and has no "unlabelled dollars".)
- The flagged contracts and their tallies are restored from the store after a
  restart.
- A missing or non-finite input is unlabelled volume or no reading, never a zero.

## 8a. The tally at the alert (added 2026-10-05)

The first live session (2026-10-05, 73 contract alerts by 11:36 CT) passed every
mechanical check and showed the estimate to be weak on busy contracts: pooled
bought 47.7% against sold 46.0%, a median lean of 7.7 points, and the two sources
leaning the same way on 34 of 66 contracts. That last figure was not a fair test.
The poll figure covered the contract's whole day; the stream figure covered only
what came after the alert; and at the median half of a contract's volume had
already printed when it was flagged.

So each row now also keeps **the poll's tally at the moment the alert was
registered** (`at_bought`, `at_sold`, `at_unlabelled`):

- taken in `_register`, the same minute the alert fired, from the book as the
  alert's own chain left it;
- written ONCE with the row's first write and never updated, read back after a
  restart with the row's identity;
- `NULL`, not three zeros, when the contract had not been booked yet (an alert
  before the open) or the row predates the columns;
- published in `flow_sides` as `at_alert`. **No screen shows it.**

Poll-since-the-alert is the running tally minus it, over the window the stream
covers (the stream starts a few seconds later; measured coverage was 99%).
`tools/flow_sides_report.py` makes the comparison for a stored session: how many
contracts both sources measured, whether they lean the same way, the median gap
between the two leans, and their correlation. It is read-only and prints; it is
not scheduled. What it shows decides whether the estimate on a busy contract
deserves the screen space it has.

## 9. Testing and rollout

- Test-first for the pure parts: the tally, the tick merge, the verdict, the store,
  the row text, the public filter. The blocking stream worker is verified live.
- Config tests monkeypatch the accessor and reload the consumer.
- No dev environment exists: the two screens are verified on the local page harness,
  and the service by a Redis read on prod during a session after promote.
- The User Guide, Technical Reference, API Reference, `page_help.py` and the
  changelog move in the same commits.

## Not in this build

- A per-symbol bought/sold split on the Macro Board or the Opportunity Board.
- A standing streamed window subscribed before anything fires.
- Changing what the hedging-flow model measures; it keeps its own state.
- Any paid tape.
