# Live Paper Ledger marks and exits (design, 2026-11-24)

The operator asked for the Paper Ledger to update the way the Portfolio does,
every few seconds from the option stream, and chose **display and exits**: the
P&L moves live, and the operator's own stops and targets fire on those live
marks instead of waiting for the hourly pass.

## What existed

| Rows | Marked by | Cadence | Exits |
|---|---|---|---|
| The operator's own (`source` not self-managed) | `compute._reprice_open_pnl` on each Ledger publish; `compute.manage_ledger_trades` | the hourly paper cycle (09:00-14:00 CT) and any Refresh | DEBIT: `signal_recommender.recommend`. CREDIT: the profit trail and the +50% target (`[protect] ledger`, `ledger_take_profit`) |
| Self-managed (ideas, daily condor, shadow ideas) | `idea_ledger.mark_cycle` | every 5 minutes in the session | stop / target / trail, `apply_mark` |

## A bug found on the way

`manage_ledger_trades` priced EVERY row with `signal_repricer.reprice_legs`.
A CREDIT Ledger row has no `legs` (its strikes are `short_strike` /
`long_strike` / `call_short` / `call_long`), so `reprice_legs` summed nothing and
returned **value 0.00, P&L 0.00** for it. Since the trail and the +50% target were
switched on for the operator's credit trades (2026-11-15) neither could ever
fire, and every hourly pass wrote a best of 0 into `mfe_pnl`. The tests stubbed
`reprice_legs` with a ready mark and so never ran the real one. Fixed here: a
credit row is priced by `reprice_swing`, as the page's own reprice always did.

## The shape

One new sibling module, `services/options_svc/ledger_live.py` (it imports
nothing from `compute`), and two pure halves split out of the repricers so the
live mark is the SAME arithmetic as the hourly one, not a copy of it:

* `signal_repricer.value_legs(trade, chain)` and `value_swing(trade, chain)`:
  everything `reprice_legs` / `reprice_swing` did after the chain fetch.
* `structure_marks.value_from_chains(row, chains)`: the same for a
  self-managed row.

The live module builds a small chain-shaped dict from streamed quotes and hands
it to those functions. The quote rule (`_market`: a zero bid is a market only
under a small offer) and the fill model (`realistic_vertical_fill` for a credit
spread) therefore apply unchanged.

**Contract symbols** are read off a real chain, never built: one chain request
per (symbol, expiration) when a row first appears, through the proxy with the
mark age limit (`[marks] chain_max_age_sec`). The same answer seeds the quotes,
so a quiet contract that the stream has not ticked yet still has a price. The
symbol is the contract's own (`SPXW  261218P05800000`); the root depends on the
series and only the chain knows it.

**Two threads**, both daemons started by the scheduler next to `flow_stream`:

1. the stream: one `/stream/options` connection for every open row's legs,
   merging each partial tick (a tick carries only the fields that changed) into
   a per-contract bid / ask book; it reconnects when the wanted set changes;
2. the evaluator, every `[live_marks] interval_sec` (3 s): marks every open row
   it can price, publishes them, and checks the operator's exits.

**The published view** is `cache:options:paper_live`,
`{"marks": {trade_id: {"unrealized_pnl": total $, "current_value": per share}}}`,
written with `skip_unchanged` and so with no timestamp of its own. Outside the
regular session, with the switch off, or while the stream is down, it is
`{"marks": {}}` and the page shows the Ledger view's own figures, as before.
The Ledger view is not republished every few seconds: it carries every trade
ever booked, and the page only needs the open rows' P&L.

## What fires, when, under which lock

* **Only the operator's own rows.** The self-managed rows are marked live for
  display, but their stops stay with `mark_cycle`: their results are what the
  weekly review, `/eod` and the public results measure, on a five-minute
  basis, and a live stop would change that measurement without anyone deciding
  to. (Root rule: every new Ledger pass skips them unless it is meant for them.)
* **The same rules as the hourly pass.** The ctx and the decision move out of
  `compute` into `services/options_svc/ledger_exits.py`
  (`exit_ctx`, `decide`), which both passes call.
* **Regular session only** (`market_calendar.is_regular_hours`). A quote outside
  it is a stale one.
* **Confirmed, not on one tick.** A rule must say "exit" on consecutive
  evaluations spanning at least `[live_marks] confirm_sec` (10 s) before
  anything closes. A single wide quote on one leg can put a spread's mid
  anywhere for a second; the hourly pass never saw those, and the live pass
  should not act on one.
* **Under `paper_lock.BOOK_LOCK`.** The close re-reads the row, requires it to
  be OPEN and still the operator's, decides AGAIN on the re-read row with the
  same live mark, and only then closes at that mark's value through
  `paper_trader.close_paper_trade` + `update_trade`, the hourly pass's own
  primitives, with the rule's own exit code. Then the Ledger view is
  republished (`refresh_paper_trades(reprice=False, keep_live_pnl=True)`).
* **The best mark** (`mfe_pnl`, the trail's peak) is held in memory between
  evaluations and written with `trades_db.raise_peak` at most once every
  `[live_marks] peak_write_sec` (60 s), and always at a close: a mark every
  three seconds would otherwise be a write every three seconds per row.
* Expiry stays with `expire_ledger_trades` and the 15:05 settle-only slot.

## Settings (`config/paper.toml [live_marks]`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | stream and publish live marks at all |
| `exits` | `true` | let the operator's stops and targets fire on them |
| `interval_sec` | `3` | how often the marks are worked out (1-60) |
| `confirm_sec` | `10` | how long an exit must hold before it fires (0-300) |
| `peak_write_sec` | `60` | the longest a new best mark waits to be stored (5-3600) |

## Caveat for the numbers

The exit rules' settings were measured on five-minute and hourly marks (the
2026-11 exit-rule replay). Live marks see moves those cycles missed, so a
trail or a target will fire sooner and more often than the replay assumed.
`exits = false` keeps the live display and returns the exits to the hourly pass.
