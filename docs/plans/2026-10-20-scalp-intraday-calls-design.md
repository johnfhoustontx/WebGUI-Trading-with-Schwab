# Opening Scalp — intraday calls every 15 minutes — design

**Date:** 2026-10-20 · **Status:** written from the brainstorm, awaiting the
operator's review. **Builds on:** [the Opening Scalp design](2026-10-18-opening-scalp-design.md)
and [the study's first verdict](../reference/opening-scalp-study.md).

**Asked for by the operator (2026-10-20):** "update the scalp setting to take into
account (1) flow alerts on $SPX, SPY, QQQ and the big ten symbols, (2) market trend
and sentiment (minor role), (3) net premium of $SPX, SPY and QQQ, (4) call / put walls
of $SPX, SPY, QQQ and the big ten. I also need you to refresh every 15 minutes."
Clarified in conversation: each refresh is a **brand-new call** for a trade entered
at that moment; "take the best of breed of whatever information we have collected";
of three options offered the operator chose **Option A** (a measured price core plus
a live confirmation reading) and asked that **Option C** (dealer positioning — walls
and net premium leading) "is also taken into consideration".

Calls marked **(my call)** were made by Claude while writing this and were not
discussed.

## 1. Intent

The 08:36 call answers one question about one entry. The operator scalps all
morning and into the afternoon, and wants the same kind of answer every 15 minutes:
which way, how often that has worked, and what the flow, the dealers and the tape
are saying right now.

The honesty rule of the first design stands and matters more here: **a number
called a probability is one that was tested walk-forward on mornings it was not
fitted to.** Everything else is shown as a reading, clearly labelled, and recorded
so it can be tested later.

## 2. Goals, non-goals, anti-goals

**Goals**
- A new, frozen call every 15 minutes from **08:51 to 13:51 CT** (21 calls a day),
  for $SPX, SPY and QQQ, each for a 0DTE scalp entered at that moment with the
  existing trade rules (+50% / −50% on premium, 60-minute time stop, four roles).
- Its direction and its probability from **price structure with seven months of
  history** (§4), tested walk-forward exactly like the 08:36 call.
- A **confirmation reading** beside it from flow alerts, net premium, the walls
  (Option C's dealer read) and sentiment (§5), with sentiment weighted small.
- The walls placed against each trade's target and stop.
- **Start recording now** everything the confirmation uses, every 15 minutes, so
  it can be tested; the walls and net premium in `gex_history.db` are kept only five
  sessions and are being lost daily.
- Every intraday call graded nightly and shown in the scorecard.

**Non-goals**
- The 08:36 call does not change (its premarket method, study and freeze stay).
- No trade entry, no alerts or pushes for a call, no public screen, no Claude call.
- The confirmation reading does not move the probability until a later study says it
  should (§7).

**Anti-goals — it fails even if it works if:**
- a confirmation score is presented as a win probability;
- a later call is built from data that already contains its own outcome;
- 21 calls a day push the one-minute GEX poll off its slots or crowd the autoscan;
- the page makes it hard to tell the latest call from an older one of the same day.

## 3. Schedule

- **Slots (my call):** every 15 minutes from **08:51 to 13:51 CT**, from config
  (`[intraday] start`, `end`, `interval_min`). :06 / :21 / :36 / :51 sit after the
  autoscan's :02–:05 fetch burst and outside the first minute after the hour and half
  hour (CLAUDE.md, "Every service shares the proxy's 5 requests a second"). The last
  entry, 13:51, exits by 14:51 — before the 15:00 close and the settle-only slot.
- **Entry time:** the call's own slot minute floored to the last 5-minute
  BOUNDARY, i.e. the close of the last complete bar (an 08:51 call enters at
  08:50, the close of the bar that STARTS at 08:45 — the 08:35 convention). Called
  the call's `entry_ts`. The live chain mid is read about a minute later; accepted.
- **Refresh (my call):** the page's Refresh never makes an off-slot call. Within a
  slot's grace it fills that slot if its call has not been published yet; otherwise
  it republishes what exists. No `entry_ts` ever falls between slots.
- **One scheduler branch, latched per slot**, under the existing `scalp_call` lock
  (the 08:36 call and an intraday slot can never run at once). A slot missed by more
  than its grace is skipped, never backfilled (a late call would not be the call
  that moment asked for).
- **Schwab budget per call (my call):** today's 5-minute bars for the 23 bar
  symbols (§4) and the three 0DTE chains — about 26 requests every 15 minutes,
  normal lane. The confirmation reads only Redis views (no Schwab call).

## 4. The measured core (Option A)

**Bars.** The bar store gains the BIG10 basket's members
(`shared.symbols.big10()` — see §9) and the backfill is run again to fetch their
seven months of 5-minute history.

**Direction.** The sign of the symbol's own move over the **last 15 minutes**
(three 5-minute bars ending at `entry_ts`); a move inside `[intraday]
dead_zone_pct` means no direction and no call for that symbol at that slot (as the
08:36 call's flat open).

**Inputs that agree or disagree** (each measured over seven months; absent stays
absent):

| Input | Agrees with up when |
|---|---|
| Move since the open (08:30 open to entry) | above +dead zone |
| Side of VWAP (session VWAP from 08:30 on 5-minute bars; **$SPX uses SPY's VWAP side** — the index prints no volume) (my call) | price above VWAP + dead zone |
| Big-ten breadth: share of the BIG10 members whose last-15-minute move agrees | above `breadth_agree` (default 0.6); below `1 − breadth_agree` disagrees |
| Futures, last 15 minutes (/ES for $SPX and SPY, /NQ for QQQ) | above +dead zone |
| $VIX, last 15 minutes | below −dead zone (inverted) |
| The symbol's sector funds, last 15 minutes (the 08:36 mapping) | above +dead zone, each |

**Alignment and buckets** are the 08:36 machinery unchanged: fraction agreeing,
cut at `[direction] buckets`, roles relative to the direction.

**Time-of-day blocks (my call).** 21 slots × 4 roles × 3 buckets × 3 symbols would
be 756 tested cells on about 140 days each — far too many for the sample, and a
recipe for chance "edges". Slots are pooled into **three blocks**: morning
(08:51–09:51), midday (10:06–12:21), afternoon (12:36–13:51), in config. Each
(symbol, block, role, bucket) is one cell: 108 cells. The study report says how
many cells passed and how many chance alone would pass.

**The study.** For every stored day and every slot, the four trades are simulated
from that slot's entry along the bars with the existing simulator (sigma = $VIX or
$VXN at the entry bar × multiplier); walk-forward Brier against the base rate;
edge only if it holds at every multiplier in `iv_scales`; one row per
(day, symbol, slot, role) so a day contributes up to 21 mornings-worth of rows to
a block. ⚠ Rows of one day are not independent (the same tape, overlapping hours):
**the statistical unit is the DAY (my call).** Walk-forward steps by day, never by
slot; a cell's `n` is the number of distinct days with a row in it; `min_samples`
gates on those days; the Wilson interval is computed with `n` = days (the rate
itself is the mean over that cell's rows); the page's sample label says "days".
Brier is summed over rows but scored only on days strictly after the cell's
history. Same 5-minute both-levels-in-one-bar rule; same skip rules (short session,
data hole, no vol).

**Late-day 0DTE.** At 13:51 a 0DTE premium is small and fast; the existing
`min_premium` skip and the time-to-settlement in the pricer already apply. The study
reports results per block so the afternoon's behaviour is visible on its own.

## 5. The confirmation reading (shown, recorded, not in the probability)

Four components, each read at the call's moment from Redis views only, each giving
**agree / disagree / no reading** relative to the call's direction, combined as a
weighted score in [−1, 1] (agree +w, disagree −w, over present components):

| Component | Source | Agrees with up when | Weight (default) |
|---|---|---|---|
| **Flow** | `cache:options:flow_alerts`, alerts since `entry_ts − [confirm] flow_window_min` (default 30) on the symbol AND the BIG10 members | the net count of bullish over bearish alerts is at least `flow_margin` (default 2); see the mapping below | 1.0 |
| **Net premium** | `cache:options:net_premium` series: the symbol's latest call − put premium **level** (never a difference — the stored premium is volume at the current mark and falls when marks fall; CLAUDE.md) | net call premium above `netprem_margin` of the total | 1.0 |
| **Dealer read (Option C)** | `cache:options:matrix` rows: `call_wall`, `put_wall`, `flip`, `net_gex`, `spot` for the symbol and the BIG10 | see below | 1.0 |
| **Sentiment** | `cache:sentiment:composite` `derived.trend` (state, score) and the intraday regime | a bullish state; a bearish state disagrees; neutral is no reading | **0.5** (minor, the operator's word) |

**Flow alerts, mapped to a direction (my call).** The view holds six alert types
whose `side` means different things, and only `uoa` carries premium; so the
component COUNTS directional alerts rather than summing premium:

| Alert type | Bullish | Bearish | Not directional |
|---|---|---|---|
| `uoa` | a call alert estimated BOUGHT; a put alert estimated SOLD | a put bought; a call sold | no bought/sold estimate (unlabelled) |
| `big_delta` | positive delta notional | negative | — |
| `crossover` | calls over puts | puts over calls | — |
| `hiro_surge` / `hiro_flip` | dealers buying | dealers selling | — |
| `gamma_flip` | — | — | always (recorded, not scored) |

The bought/sold figure is an estimate (CLAUDE.md); an alert without one is not
directional. The view is a capped list: when the window reaches past its oldest
alert the reading is marked `truncated` and recorded so. Raw per-type counts are
recorded, so any later mapping can be recomputed from history. The builder confirms
each type's actual `side` values against `services/options_svc/flow_alerts.py` and
the HIRO detectors before coding the table.

**The dealer read (Option C), defined (my call — the operator described the idea,
not a rule):**
- *Room:* the distance from spot to the wall in the trade's direction (the call wall
  for up, the put wall for down) against the **"long, with the move" role's** live
  `win_points` — the move the directional trade needs. Room ≥ win points agrees; a
  wall inside that target disagrees ("the target is beyond the call wall"). The
  credit roles get the wall-context text only, not a score.
- *Breadth of room:* the share of BIG10 members with room in that direction by the
  same rule in percentage terms; above `breadth_agree` agrees.
- *Flip side:* spot above the gamma flip favours the move continuing up less
  (positive gamma dampens moves); recorded and shown, **not scored** until tested.
- The dealer read is shown on its own line as well as inside the score, so the
  operator can read Option C directly: "Dealer read: room up to the call wall at
  5,850 (0.4%); 7 of 10 big names have room."

**Wall context by the target.** Each role's row shows any wall between entry and its
target or stop ("target beyond the call wall at 5,850").

**Shown as words, not a percentage:** "Confirms", "Mixed", "Contradicts" from the
score (cuts in config), with each component's verdict beside it. The page labels the
whole panel "Not in the probability — being recorded to be tested".

## 6. Recording (from day one)

At every intraday slot AND at 08:36, one row per (day, slot, symbol) for $SPX, SPY
and QQQ — written whether or not the symbol has a direction — saves: the
confirmation components and their RAW values (so any later definition can be
recomputed), the walls, flip, net GEX and spot for the symbol and every BIG10
member, the net premium levels (symbol and the BIG10 basket), the sentiment state
and score, and the flow per-type counts. A source older than `[confirm]
max_age_min` (default 5) at read time is "no reading" and recorded as stale. In
`scalp.db`, a new table (`intraday_readings`), kept forever, in the backup. This is
the history that does not otherwise exist beyond five sessions.

**Storage shape (my call).** Intraday calls, their chain slices and their grades
live in NEW tables keyed by `(day, slot, symbol)` — never by altering the existing
`(day, symbol)` tables, which prod already holds. Intraday calls are graded on both
the model and the slot's own saved chain slice (about 63 small slices a day,
compressed, kept forever).

## 7. Promotion (later, by a declared study)

After **40 recorded sessions** (config), the study runs a second, declared test:
does adding the confirmation score — and separately the dealer read alone — as an
input beat the price-only model walk-forward, at every multiplier? The result is
written to a dated `docs/reference/` note and **reported to the operator**; the
operator decides whether it goes into the probability. Nothing is promoted
automatically (my call). **This promotion study is NOT part of this build** (my
call): only the recording it needs is. It is a later, separate piece of work once
40 sessions exist (about mid-December 2026); the threshold is config.

## 8. Grading and the scorecard

- Every intraday call is frozen at publish (the 08:36 rules: saved only after it
  validates and publishes; a frozen call is reused whole; no new call after its own
  entry + time stop; grades write-once from what was published).
- Nightly after the rebuild, every published intraday call is graded (model and
  chain results, as the 08:36 grading), and its confirmation verdict is stored beside
  the grade so the scorecard can show "calls that confirmed" vs "calls that did not".
- The scorecard gains an intraday section per block: highlighted-call hit rate vs
  base, cautions, and win rates split by confirmation (Confirms / Mixed /
  Contradicts) — the first, informal look at whether the reading helps, until §7.

## 9. Decisions to flag for the operator

- **The big ten (my call):** the app's existing BIG10 basket from
  `config/symbols.toml` `[baskets] big10` — NVDA, MSFT, GOOGL, AMZN, META, AAPL,
  TSLA, AVGO, **PLTR, AMD** — rather than the list proposed in conversation (which
  had JPM and LLY in place of PLTR and AMD). One list in one place; the Net Premium
  page's BIG10 basket already uses it, and editing it there changes both.
- Slot times, blocks, weights, margins and the 40-session promotion threshold are
  config, catalogued in Settings → Configuration.

## 10. The page

- The 08:36 card stays at the top, unchanged.
- A **"Latest call"** section per symbol: the slot time, direction ("moving up over
  the last 15 minutes"), the four roles with measured rates or "no measured edge",
  the confirmation word with its components, the dealer read line, and the wall
  context per role.
- A **today's timeline**: one row per published slot (time, direction per symbol,
  highlight or none, confirmation word), graded results filled in after the close.
- Clock-honest labels as the 08:36 card: a call is never shown as current once its
  next slot has passed; the next slot's time is shown.

## 11. Build order

1. **Recording + big-ten bars** — the `intraday_readings` writer on its own 15-minute
   slot (no call yet), the BIG10 added to the bar store and backfilled. First,
   because the walls and net premium are being lost daily.
2. **The intraday study** — outcomes per slot, blocks, walk-forward by day; its
   verdict written up and reported to the operator, then continue unless told to stop.
3. **The intraday call** — build, freeze, publish, refresh, grading.
4. **The page and scorecard.**
5. **Docs** — CHANGELOG, routes doc, User/Reference/Technical/API manuals, page_help,
   config catalogue, CLAUDE.md only if an invariant changes.

## 12. Left to the builder

Module and table names (siblings of `scalp_*`, none importing `compute`); the view
shape for intraday calls (a second key or a section of `cache:options:scalp`); how
the page lays out the timeline within §10.
