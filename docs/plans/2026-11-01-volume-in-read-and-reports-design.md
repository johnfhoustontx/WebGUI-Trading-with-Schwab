# Traded volume in the Market read and the market reports — design

**Asked for (2026-11-01):** "I need the Market reports and market reads to take into
consideration the Volumes traded and the impact it would have on the markets."

## What each product did before

- **The Market read** (`services/market_svc/market_read.py`): six rule-decided rows
  from published views. The only volume in it was the Flow row's bought/sold tally
  over flagged option contracts. Share volume appeared nowhere.
- **The market reports** live outside this repo, in `vps2:~/neuralstrike-reports` (no
  git). Claude (Sonnet) writes a JSON spec from a snapshot of Redis views, and
  `render.py` turns it into the report. The snapshot's only volume was the
  `order_flow` aggressor ratio, which is empty when the streamer is not running.

## What "the impact on the markets" means here (decision)

The phrase could mean three things:

1. **Price impact**: how far a given order moves a price. Needs order-book depth. The
   stack has none.
2. **Conviction**: heavy volume behind a move means the move is real and will carry on.
   This is the usual market saying and what the request most naturally evokes.
3. **What volume has actually been followed by.**

The decision is **3, using the report's existing idea of a checkable claim**, because the
measurement below refuses 2 as a forecast. The Market read and the reports state what
volume did, which way the heavier trading went, and what volume has been followed by:
the size of the move, not its direction.

## What was measured (2026-11-01, read-only on prod)

**Source.** `scalp.db` holds stored five-minute bars. 149 full sessions from 2026-03-31
to 2026-10-30, SPY and QQQ, with "usual" the median of the prior nine sessions by the
same time of day. Five readings a session (09:30 to 13:30 CT). In-sample, and the
intervals are bootstrapped by session.

| SPY volume so far | Readings | Sessions | Range for the rest of the session (mean) | 95% interval |
|---|---|---|---|---|
| Heavy (at least 1.2 times usual) | 142 | 48 | **0.64%** | 0.52 to 0.77 |
| Usual | 419 | 116 | 0.52% | 0.46 to 0.60 |
| Light (at most 0.8 times usual) | 139 | 43 | **0.41%** | 0.33 to 0.52 |

| Move so far of at least 0.25% | Readings | Sessions | Continued to the close | 95% interval |
|---|---|---|---|---|
| Heavy | 98 | 36 | 45% | 30 to 60 |
| Usual | 220 | 86 | 50% | 40 to 60 |
| Light | 43 | 23 | 49% | 28 to 66 |

| The row's chip (both symbols agree) | Readings | Sessions | SPY to the close (mean) | 95% interval |
|---|---|---|---|---|
| Tailwind | 9 | 7 | −0.34% | −0.76 to −0.11 |
| Headwind | 23 | 15 | +0.15% | −0.18 to +0.52 |
| Neutral | 668 | 140 | −0.03% | −0.07 to +0.02 |

So heavy volume went with a **wider** range and light with a narrower one, and the two
ends separate. Heavy volume did **not** make a move more likely to continue. The chips
did not point the right way, on samples too small to read either way: 32 of 700
readings left Neutral.

**So a chip reads what has happened, not what comes next**, like Direction, and no
sentence says heavy volume confirms anything. The time-of-day mix differs slightly by
bucket, so the range figures are a guide and are not printed by the code.

**Other measurements that shaped this:**

- `/quotes` through the proxy carries no `fundamental` block, so there is no
  `avg10DaysVolume` to read. The baseline has to come from bars.
- `$UVOL`, `$DVOL`, `$VOLD` and `$TVOL` answered with all zeros on a Sunday. Whether
  they fill in during a session is unmeasured, so nothing depends on them.
- **A five-minute bar and `totalVolume` are on different bases.** SPY's regular-session
  bars on 2026-10-30 summed to 49.3M, its daily bar said 77.9M, and its quote said
  77.7M: 0.60 to 0.72 across ten sessions. So pace compares bars with bars.
- Stored and refetched bars agreed exactly on the older sessions, and within 1% on the
  newest. 2026-10-30's doubled volume is real (the daily bar agrees), not a change of basis.
- The proxy's `/pricehistory` defaults to `needExtendedHoursData=false`: the bars are
  the regular session only (78 a day, 08:30 to 14:55 CT).

## Where each figure comes from, and how often

| Figure | Source | Available |
|---|---|---|
| **Pace**: volume so far against the median of the last 9 sessions, same time of day | `/pricehistory`, five-minute bars, SPY and QQQ | Every 5 minutes in the session, on finished bars. After the close, once, for the whole session |
| **Up share**: volume in rising bars over volume in rising and falling bars | The same bars | The same. Flat bars count on neither side |
| **Sector share**: dollars traded in rising sectors | `/quotes` on the 11 sector funds, `totalVolume` × last | The same, in the session and at the close only. A quote before the open carries thin pre-market prints, so it is not read |

Cost: 3 Schwab calls per refresh, about 240 a session and a handful otherwise, beside
the roughly 12,000 quote calls the dashboard makes a day.

**Outside regular hours.** Before the open and on a day with no session, one fetch a
day (never in the minute after the hour or half hour, where Schwab refuses calls). The
view reports the last session and says that pre-market trading is thin and not read as
a signal. The Volume row reads only a view of this session, so it is **No reading**
then, which is true anyway: the Market read has no slots outside the session.

**Checked on real bars (dumped from prod, nothing published).** 2026-10-30 at 12:10:
SPY 1.69 and QQQ 1.93 times usual, 42% and 44% in rising bars, so a Headwind. Sector funds
were almost all rising on the day. That is why no sentence says "the move down": the
volume leaned to falling bars inside a session that was not down. 2026-10-28 at 10:20: 0.63
and 0.59 times usual, Neutral.

## The shape

```
market_svc/scheduler.refresh_volume  → compute.fetch_bars / fetch_raw_quotes (every 5 min)
                                     → volume_read.build (pure)
                                     → cache:market:volume  (contract MarketVolume)
                                          ├─ market_read.volume → the Volume row
                                          └─ remote_collect.py → snapshot → render.py's volume block
```

- **It runs beside the poll, never inside it.** The fetch is handed to the executor
  without an await, one at a time: a slow bar series must not hold up the three-second
  dashboard.
- **A failed fetch publishes nothing.** The last view stays and ages out, and the
  row becomes No reading, so a failure cannot pass for a session with no volume.

### The Volume row

| | Rule |
|---|---|
| **Tailwind** | Every symbol heavy (`pace ≥ heavy_ratio`, 1.2) with `up_share ≥ up_share_strong` (0.55) |
| **Headwind** | Every symbol heavy with `up_share ≤ up_share_weak` (0.45) |
| **Neutral** | Anything else with figures, including a rally on light volume |
| **No reading** | A symbol with no pace or up share, the view missing, older than `stale_after_sec` (600), from another day, or not of this session |

The sector share is printed and decides nothing: on one day it would overrule the
other two with a figure no chip was tested on.

### The reports: code states the facts

The app's standing rule (since 2026-09-10) is that the model only joins statements the
code wrote, because prompt instructions alone did not hold. The reports take it one step further than a word-for-word check:
**the model never handles the sentences.** A new `volume` block has no text fields.
`render.py` reads `volume.payload.statements` out of the slot's snapshot itself and prints
them, so nothing in a spec can reword them, and a spec that tries to is ignored.

- Absent reads as "Volume figures are not available for this report." An out-of-date
  view (another day's, or a session view over 15 minutes old) reads as "not current".
- `REPORT_GUIDE.md` gains a reading rule: heavy volume is never one of the two confirming
  reads a 70% call needs, and a direction call never rests on it. A call on the size of
  the range may.
- `plain_english.py` lists a report with no volume block, and a volume figure written
  outside the block. A listing only, like the rest of that check.

**The report tooling is not in this repo**, so its change is committed here as a patch,
[`deploy/report_tooling/`](../../deploy/report_tooling/README.md), for the operator to
apply. It was tried against a copy of the live files.

The order of the two steps does not matter: before the service publishes the view the
block says the figures are not available, and the view is harmless to a tooling that
does not read it.

## What was left out

- **Volume on the Big 10 or every tile**: more calls for a row that would not change.
  `symbols` is a setting.
- **Options volume**: the reports already carry the put-to-call ratio and the flow
  alerts. The stored premium falls when marks fall, and is not differenced here.
- **/ES and /NQ**: their chain feed is not promoted, and its counts are scaled and never
  printed. Nothing here reads it.
- **`$UVOL` and `$DVOL`**: see above.
- **A Claude call**: none is added. The Market read has none, and the reports' existing
  run writes around the block.

Every threshold is a starting point. The row's two chips have not been shown to mean
anything about what follows.
