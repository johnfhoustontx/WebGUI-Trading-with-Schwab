# Traded premium, booked as it trades (design)

**Date:** 2026-10-09
**Scope:** `services/options_svc` (one new sibling module, the per-chain hook) and
`options-scanner/gex_history_db.py` (one more view string). No schema change, no
new Schwab call, no new command. **Phase A stores the figure and nothing reads it.**
Readers are Phase B and each is its own decision.
**Status:** proposed. Nothing here is built. Plan:
[2026-10-09-traded-premium-increment-plan.md](2026-10-09-traded-premium-increment-plan.md),
which builds Phase A with the three proposals under "Open before the plan" taken
as written.
**Comes from:** the gate that stopped Phase 5 of
[the heatmap design](2026-10-09-gamma-heatmap-value-scale-frame-design.md)
(section 7).

## The problem

Every premium figure the collector stores is

```
Σ mark now × totalVolume × 100
```

per symbol (`flow_skew.index_call_put_premium`, the `call_prem` / `put_prem`
columns) and per strike (`flow_skew.premium_by_strike`, the `prem` view).
`totalVolume` is the day's running volume and `mark now` is this minute's price. So
each minute, everything that traded earlier in the day is priced again at the
current mark. It is "today's volume valued now", and the app treats it as "what
traded today".

Measured on stored prod sessions (the 2026-09-25 backup,
`tools/measure_prem_remark.py`):

| | |
|---|---|
| Per strike and side, minute to minute: falls as a share of rises | 61% to 89% |
| Over 30 minutes: comparisons that came out negative | about one in six |
| Minutes in which a symbol's day TOTAL fell | 33% to 41% |
| `$SPX`, 2026-09-24 | peaked at $2.82B, closed at $1.98B |

A traded total cannot fall. Every one of those falls is the mark moving.

## What to store instead

For each contract, take only the volume that is new since the last poll and price
it once, at the mark of the minute it arrived in:

```
booked this minute = (totalVolume now − totalVolume at the last poll) × mark now × 100
```

Sum that per strike and side and add it to a running total for the session. Volume
already booked is never priced again.

One 0-DTE call trades 1,000 contracts at 09:00 at $5.00, then nothing, and decays:

| Time | Volume | Mark | Stored today | Booked as it trades |
|---|---|---|---|---|
| 09:00 | 1,000 | $5.00 | $500,000 | $500,000 |
| 11:00 | 1,000 | $2.00 | $200,000 | $500,000 |
| 14:00 | 1,000 | $0.20 | $20,000 | $500,000 |

The running total can only rise, and the difference between any two readings is the
premium that traded between them.

**The stored row is the running total, not the minute's increment.** One row per
symbol per minute, a grid of `{call, put, net}` per strike, exactly the shape of the
`prem` view. A reader that wants "the last 30 minutes" subtracts two rows, which is
what the heatmap's `delta` already does. Storing the total has two properties
storing the increment lacks: a missed row loses nothing, and a row can be read on
its own. (Storing only the strikes that traded each minute would be smaller, but a
single lost row would silently lose that premium from every later sum.)

## Facts that shape the design

All measured 2026-10-09, on the backup unless said.

| Fact | Measured | Consequence |
|---|---|---|
| Every fetched chain already passes through a per-chain hook on the collector's thread, and one detector there already tracks every contract's volume in memory. | `compute` `on_chain` → `flow_sides_tick.on_chain` | The new pass is one more call in that hook. It follows that module's rules for a first sighting, a gap and a new session. |
| A carried chain (a watchlist-only symbol between real fetches) is not handed to the hook: it has no new volume. | `gex_collector.poll_once` | No row is written on a carried minute. A tail symbol has a row every `tail_interval_min` minutes, and its new volume is priced at the mark of the minute it was fetched in. |
| Little of the day's volume trades before the regular open. | 0.0% at the first row (06:30 or 08:00), 0.1% to 1.4% at 08:30 | The watch begins at the regular open, the rule the bought/sold tally and the hedging-flow model already take (before it a chain's marks are frozen and may still carry yesterday's volume). What traded earlier is left out: at most 1.4% of the day here. |
| Polls are missed a few times a session. | 3 to 10 gaps a session, usually one or two missed minutes; 2% to 9% of the day's volume crosses a gap; one 34-minute step on `$SPX`, 2026-09-24 | Volume that crosses a gap is still booked, at the later mark. It is not lost, only priced a minute or two late. The size of that share is logged each day. |
| The per-strike premium pass over an index-sized chain is quick. | 13.8 ms for 6,400 synthetic contracts; 0.5 ms for a stock's 240 | About a tenth of a second a minute across the universe, by arithmetic. Real chain sizes are not measured; Phase A times the pass in place. |
| The `prem` view is an eighth of the stored grid bytes. | 21.8 MB of 170.2 MB for one session of 90 symbols | A sixth view of the same shape adds about 13% to the database. Grids are kept five sessions, so it is bounded: roughly 110 MB at that universe. |
| The collector's one-minute branch already overruns its minute a few times a day. | stated in `config/gamma_public.toml`; not re-measured here | The pass runs in memory and the write is one batch after the poll, the way the hedging-flow rows are written. Phase A measures both before any reader depends on them. |

## The rules

One small state per symbol: each contract's highest volume seen this session, the
running total per strike and side, and the time of the last good reading.

1. **A symbol's first reading sets each contract's baseline and books nothing,**
   and that reading is never before the regular open. After a restart the whole
   day's volume must not land in one minute. On a later reading, a contract with
   no baseline stood at zero volume before, so all of its volume is new.
2. **New volume is booked at this reading's mark,** the same mark the stored figure
   uses today (`flow_skew`'s contract mark). A contract with no usable mark books
   nothing this minute and keeps its baseline, so its volume is booked when a mark
   returns.
3. **The baseline is a high-water mark.** Volume never falls within a session, so a
   glitch read of zero books nothing and cannot re-book the day later.
4. **A gap does not drop volume.** Whatever traded while a poll was missed is booked
   at the next reading. The day's check line (below) says how much.
5. **A new session date clears everything.**
6. **A restart continues the session's total.** The first time a symbol is written
   after a start, its last stored row for today is read and added back. Switching
   the collection off and on again is treated the same way. Contract baselines
   are rebuilt from that poll, so the volume that traded between the last poll
   before the restart and the first one after it is not booked. Promotes are made
   after the close, so in practice this is a crash, and it costs a minute or two.
7. **A start with nothing stored today starts from zero at that minute.** A reader
   can see it: the first row's time is when the watch began.
8. **It never opens the database inside the hook and never raises.** The hook
   returns a grid; rows are written in one batch after the poll, under a guard that
   counts a failure as a degrade. A premium failure must cost this view only.

## Where the code goes

```
services/options_svc/traded_premium.py     new; imports nothing from compute
    advance(state, chain, now_ts)          pure: books one chain's new volume
    grid(state)                            the running totals as store cells
    on_chain(symbol, chain, now)           the session state around advance
    write_rows(gh, conn, ts_min)           rule 6, then this poll's rows, one commit
    day_check()                            the figures for the daily log lines

services/options_svc/compute.py            two lines
    on_chain(...)                          one more call beside flow_sides_tick
    after poll_once(...)                   one batch write, beside the HIRO rows

options-scanner/gex_history_db.py          nothing new: insert_snapshot with a
                                           sixth view string, "tprem"
```

`compute.py` is one line under its ceiling, so the two lines it gains are paid for
by moving the hedging-flow row writer out to a module of its own.

A row also carries the symbol's call and put totals in the `call_prem` and
`put_prem` columns, so a later reader of the totals need not decode a grid. Every
reader of those columns filters on its own view, so nothing existing sees them.

Strikes are keyed at three decimals. The store keeps strikes as 32-bit floats, so
an odd strike such as 17.63 reads back as 17.6299991607666; without the rounding, a
total resumed after a restart and the same strike's new volume would sit in two
cells that pack to one stored strike, and one would be lost.

**Config.** `config/marketdata.toml [collection]`, which is read per request, so a
change needs no restart, each with its entry in Settings → Configuration:
`traded_premium` (a boolean, shipped `false`) and `traded_premium_late_sec` (90),
the step after which new volume is counted as priced late in the day's check.

## What it is, and is not

- **It is an estimate of dollars traded,** priced at the mid of the minute the
  volume was first seen. A minute's volume did not all trade at that minute's
  closing mid.
- **It is unsigned.** It still says nothing about who bought and who sold. That is
  the bought/sold tally's job, for flagged contracts only.
- **It runs a little under the true day figure** by whatever traded before the
  regular open, before the first reading, and across a restart.
- **It begins the day it ships.** Nothing can rebuild it for past sessions, because
  per-contract volumes were never stored.
- **It will not match the figure stored today,** and should not. On a day when marks
  decay it will end above it.

## How Phase A proves itself

Phase A ships the collection with nothing reading it. It is judged on three checks,
each computable from what it stores or logs, before any reader is built:

1. **No cell ever falls.** For every symbol and session, each strike's call and put
   totals are non-decreasing. `tools/check_traded_premium.py` reads every symbol's
   stored rows for a day and must report zero falls. One fall is a bug.
2. **The volume reconciles.** Once a session, at the first poll after the regular
   close, one log line per symbol: the volume booked, the chain's own volume now
   less at the first reading (summed straight off the chain, apart from the
   booking), and the share of the booked volume first seen more than
   `traded_premium_late_sec` after the symbol's previous reading. The first two
   should agree to within volume that had no usable mark.
3. **The cost is in budget.** The pass's time per poll and the batch write's time
   (median and worst, logged with the lines above), and what the view adds to the
   day's stored bytes against the 13% estimate, which the same tool prints.

Alongside, with no extra storage, the same line reports the day's total priced two
ways: at the mark, and at each contract's `last` price. `last` is a real trade in the
window whenever volume rose, so it may be the better price, but it is noisier on
multi-leg prints. One week of that comparison settles which to use before a reader
depends on it.

## Phase B: readers, one decision each

None of these is part of Phase A, and each changes what a page or an alert shows.

| Reader | Today | What changes | Decide on |
|---|---|---|---|
| Heatmap **Premium** value | not built (failed its gate) | Level and Change both become sound; this is Phase 5 of the heatmap design, on the new view | whether the write for a fifth history key fits the minute |
| Flow view's **strike ladder** | the `prem` view | the same ladder, on premium that does not fade | how different it looks on a decay day |
| Flow **ribbon** and **Net Prem** lines | `call_prem` / `put_prem` | lines that only rise; a crossover that cannot come from re-marking | measured first: the open task on those two views |
| The **crossover** flow alert | fires on the sign of `call_prem − put_prem` | would fire on traded premium | how many past alerts re-marking alone caused or hid |

Once the ladder has moved, nothing reads the `prem` view and its write can stop,
which returns the storage this design adds.

## Open before the plan

1. **Which price:** the mark (proposed, for continuity and steadiness) or `last`.
   Phase A measures both; this only fixes the default.
2. **The restart minute.** Persisting contract baselines would close rule 6's gap at
   the cost of a second store. Proposed: do not, and let check 2 show how often it
   matters.
3. **Carried minutes.** Proposed: no row. The alternative, repeating the last total
   so every Greek row has a premium row beside it, costs storage for no information.

## Not in this design

Signed premium (buying versus selling), per-contract storage, a backfill of past
sessions, the public page, and any change to what the Flow, Net Prem or heatmap
pages draw.
