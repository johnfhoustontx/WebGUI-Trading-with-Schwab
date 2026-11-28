# Market cycle: where the market is in its rise and fall — design

**Date:** 2026-11-27 · **Status:** DRAFT for the operator's review. Nothing is built.
The direction (four phases, one engine, three readers) was agreed in conversation
on 2026-11-27; every threshold, name and rule below marked *(my call)* or listed
under "Open questions" is not yet agreed.
**Source:** the operator's doc "Rules to Identify a Market Bottom"
(https://claude.ai/artifact/RtasdQFgPMQotARURCMtAV, private to the operator).

## Intent

The app says what kind of **day** the market is having (the Market read, the
weather card) and what **character** the session has (the regime). Nothing says
where the market is in its **weeks-to-months rise and fall**. The operator's words:
*are we on the way to the top, at the top, on the way to the bottom or at the
bottom.*

The operator's doc gives rules for one of those four (a bottom: washout, divergence
on the retest, confirmation, invalidation). This design generalises it to all four,
as one state that the market reports, the market weather card and the Desk all read.

## Goals

- One answer, decided by rule from stored daily data: the **phase**, how far into
  it, what evidence holds it there, what would end it, and how many signs of the
  next phase are present.
- The same answer on every surface: one engine publishes one view; the reports,
  the weather card and the Desk only read it.
- Measured before it is trusted. The rules are tested against twenty years of
  history, against simple price-only baselines, before any public surface shows them.
- Honest about absence: a missing or stale input is "No reading", never a phase.

## Non-goals

- Not a forecast and not a trade signal. It names the phase the evidence supports
  today; it does not say what happens next.
- It decides nothing in the scanner, the paper books, the exits or the trade ideas.
  Context only (the five-state classifier's precedent, see "Why measure first").
- No Claude call. Code states every fact; a report may quote them.
- The five regime keys are a contract and are not touched. The cycle is a new axis.
- No TradingView dependency at run time (see "Data").
- v1 reads the S&P 500 only. No per-index or per-sector cycle.
- v1 decides once a day, on the close. No intraday phase.

## Anti-goals (a failure even if it "works")

- **A label that is late and reads as early.** The five-state classifier's Bullish
  state underperformed neutral because the move was mature by the time it fired.
  A "Topping" that appears after the break, or a "Bottoming" 10% off the low, is
  this failure. The lag is measured and shown, not assumed away.
- **A label that is always on.** Topping signs appear often inside long advances.
  A phase with no measured false-alarm rate must not be shown as a warning.
- **Flicker.** A phase that changes back and forth across consecutive sessions.
- **A stale phase shown as current.** The view carries the session it was decided
  on; a reader that finds an older one shows "No reading".
- **Two surfaces that disagree**, the open Sector & Industry / Sector Rotation bug
  in a new place. No surface computes anything about the cycle.

## The four phases

| Key | Shown as | In plain words |
|---|---|---|
| `advancing` | Advancing | On the way up |
| `topping` | Topping | At the top |
| `declining` | Declining | On the way down |
| `bottoming` | Bottoming | At the bottom |

Two more answers exist and are not phases: `unclear` ("Between phases": the
inputs are present and disagree) and no reading at all (inputs missing or stale).

The four keys are a contract once shipped, like the regime keys; the display words
are data.

Each phase has a **level**, `early` or `established` *(my call: the words)*:

| Phase | `early` means | `established` means |
|---|---|---|
| Advancing | A bottom was confirmed within the last `early_sessions` | Trend and participation both hold |
| Topping | Some topping signs, below the quorum | The quorum of signs is met |
| Declining | The break has happened; trend not yet down on every measure | Lower highs, lower lows, net new lows |
| Bottoming | A washout has happened (the doc's Stage 1) | The retest held with better internals (Stage 2) |

"Confirmed" is deliberately not a level word: the doc's Stage 3 **confirmation** is
what ends Bottoming and starts Advancing, so "Bottoming, confirmed" would be read
as the opposite of what it means.

### A state machine, not a score

```
              failed top                         failed bottom
          ┌───────────────┐                   ┌─────────────────┐
          ▼               │                   ▼                 │
     ADVANCING ───► TOPPING ───► DECLINING ───► BOTTOMING ───► ADVANCING
          │                          ▲    │                        ▲
          └── direct break ──────────┘    └── recovery, no washout ┘
```

Legal moves, each recorded on the view as the `path` that led here:

- `normal`: the four in order.
- `failed_top`: Topping back to Advancing (new highs expand again).
- `failed_bottom`: Bottoming back to Declining. This is the doc's invalidation rule:
  the index undercuts the low **and** new lows expand past the washout extreme. The
  count restarts.
- `v_bottom`: Bottoming `early` straight to Advancing on a confirmation signal, with
  no retest. The doc treats the retest as the normal sequence; history does not
  always supply one (see the 2026-11-20 reading in the appendix), so the retest
  raises the level and is never required.
- `direct_break`: Advancing straight to Declining with no topping quorum (a shock).
- `recovery`: Declining straight to Advancing with no washout (a grind back above
  a rising average with positive net new highs).

**Hysteresis.** The phase is decided once per session, on the completed daily row.
A move that rests on a *condition* (trend, participation) needs it on
`confirm_sessions` consecutive closes. A move that rests on an *event* (a thrust, a
follow-through day, an invalidation) happens on the event's own close: an event is
already a multi-day fact.

## The evidence

Every sign is a pure function over the daily series: it returns met, not met or no
reading, plus the facts it used. The lists below are the starting set. **Which
signs stay is decided by the study**, not by this document. The thresholds are
common conventions (the operator's doc says the same of its own); all of them live
in `config/market_cycle.toml`, and where a fixed number would not travel between
universes the rule is a **percentile of the series' own trailing history**.

**Bottoming (the operator's doc).**

| Sign | Rule (starting values) |
|---|---|
| New lows extreme | 13-week new lows at 40% or more of the universe |
| Oversold breadth | Fewer than 15 to 20% of stocks above their 50-day average |
| Panic volume | One or more 90% downside days (down volume at least 90% of the total) |
| Volatility spike | VIX above 30 to 40, with VIX above 3-month VIX |
| Put demand | Equity put/call ratio at a multi-month extreme |

- **Washout** (enters Bottoming, `early`): at least `washout_min_signs` (2) of the
  five inside `washout_window` sessions (5). The anchor is the lowest index low of
  the washout, and it moves down while the washout continues.
- **Retest held** (raises to `established`): at least `retest_min_gap` sessions
  later the index comes back to within `retest_pct` of the anchor, or under it, with
  at least `retest_min_improved` of four better: fewer new lows, a lower VIX peak, a
  higher RSI on the index, lighter down volume.
- **Confirmation** (ends Bottoming, starts Advancing `early`), any one of: a
  follow-through day (day 4 or later of a rally attempt, the index up about 1.5% or
  more on higher volume than the day before); a Zweig breadth thrust (the 10-day
  average of advancers ÷ (advancers + decliners) from below 0.40 to above 0.615
  within 10 sessions); a 90% upside day or two consecutive 80% upside days; net new
  highs turning positive on the 4-week count and then the 13-week; a higher low and
  a close back above the 50-day average.

**Topping (the mirror; none of this is in the operator's doc).**

| Sign | Rule (starting values) |
|---|---|
| Price near its high | The index within 3% of its 52-week closing high. Required. |
| Fewer new highs | 52-week new highs at this index high below the count at the previous one |
| Participation fading | Share of stocks above their 50-day lower than at the previous index high |
| Advance/decline line lags | The cumulative line has not made a new high with the index |
| Distribution cluster | Five or more distribution days in 25 sessions (index down 0.2% or more on higher volume) |
| Narrow leadership | Equal-weight ÷ cap-weight (RSP ÷ SPY) at a 60-session low |
| Complacency | VIX ÷ 3-month VIX and the put/call ratio in the bottom tenth of their year |

- Topping `early`: price near its high plus at least `top_early_signs` (2) others.
  `established`: at least `top_quorum` (4).
- **The break** (ends Topping, starts Declining): a close below the 50-day average,
  then a rally that fails under the prior high, with net new highs negative.
- **Failed top**: a new index high with more 52-week new highs than at the peak that
  started the count.

**Advancing / Declining (the trend states).** Advancing: the index above a rising
50-day average, net new highs positive, more than half of stocks above their 50-day,
and no topping quorum. Declining: the mirror.

## Data

Measured on 2026-11-27 through the prod proxy and the TradingView connector. The
full probe results are in the appendix.

| Input | Run time (every day) | History for the study |
|---|---|---|
| Advancers, decliners (`$ADVN`, `$DECN`) | Schwab | Schwab, 20 years |
| Up / down volume share | Schwab: (ADVN ÷ DECN) ÷ `$TRIN` at the close | Schwab `$UVOL` / `$DVOL`, 20 years |
| Index price, 50-day, RSI, swings | Schwab `$SPX` daily bars | Schwab |
| Volume for follow-through and distribution days | Schwab SPY daily bars *(my call: SPY stands in for the index)* | Schwab |
| VIX, 3-month VIX | Schwab | Schwab |
| Equal-weight ÷ cap-weight | Schwab RSP, SPY | Schwab |
| New highs / lows, 4 / 13 / 52 weeks | Our own count, `cache:market:new_highs` (all US stocks at $5 and up) | **TradingView only** (NYSE: 52-week from 2001, 3- and 1-month from 2005) |
| Share of stocks above their 20 / 50 / 200-day | **No source yet** | **TradingView only** (all US from 2002; S&P 500 from 1990 / 2007) |
| Equity put/call | Our own sector-weighted ratio (no stored history) | **TradingView only** (CBOE, from 2007) |

Three facts shape the design:

1. **Schwab alone covers advance/decline, volume, price and volatility, both live
   and for twenty years, on one definition.** These inputs can be backtested and run
   on identical data.
2. **The participation inputs (new highs/lows, share above an average, put/call)
   have history only at TradingView, on a different universe from our live counts.**
   A threshold fitted on NYSE history does not transfer to an all-US $5-and-up count.
   Those series are stored under **different names and never spliced**.
3. **The TradingView connector exists only inside a Claude session.** `market_svc`
   cannot call it, it has a daily call quota, and whether the headless report runs on
   vps2 can reach it is untested. It is a research and backfill tool, never a
   run-time dependency.

**How the participation inputs get a run-time source.** Three ways, in the order I
would try them; the study decides whether any is needed at all:

- **A. Our own S&P 500 reconstruction.** Pull the members' daily bars from Schwab
  (about 500 requests, once, then a nightly top-up after the close, well clear of
  the autoscan windows) and compute the share above each average and the new highs
  and lows ourselves. One definition for history and for today. Its history is
  survivorship-biased (today's members only), and TradingView's point-in-time
  `S5FI` / `S5TH` measure exactly how much.
- **B. Our live all-US counts, thresholds as percentiles.** Costs nothing new for
  highs and lows; needs one more screener count for the share above the 50-day
  (untested: the field-to-field filter `new_highs.py` already uses should express
  it). Has no history until it accumulates, so it cannot be decision-grade for at
  least a year.
- **C. Decide on the Schwab-only inputs** and show the participation counts as facts
  beside the phase, deciding nothing. This is where v1 lands if the study finds the
  Schwab set sufficient.

### The breadth store

`shared/breadth_history.py`, one SQLite file at `repo_paths.BREADTH_DB`
(`shared/data/breadth.db`: that folder is already gitignored, in the backup's
sweep and refused by the test suite's store guard). It is in `shared/` for the
reason `shared/iv_history.py` is: a service and two tools write it, and a store
must have one write path.

```
daily(series TEXT, day TEXT, value REAL, source TEXT, PRIMARY KEY (series, day))
```

- `series` is a fixed vocabulary (`nyse_adv`, `nyse_dec`, `nyse_up_share`,
  `spx_close`, `us_52w_highs`, `tv_nyse_3m_lows`, …). A TradingView-sourced series
  carries the `tv_` prefix and is read by the study only.
- `source` says where a row came from: `schwab_history`, `schwab_close_quote`,
  `own_count`, `tradingview`.
- A row older than two sessions is immutable outside the backfill tool's `--force`.
- **Kept forever and in the nightly backup**; never added to `DB_EXCLUDE`. The
  folder map in `CLAUDE.md` gains its row when the store ships.
- The up-volume **share** is stored as its own series. Schwab's raw `$UVOL` /
  `$DVOL` scale wanders from day to day (64.3M on one day, 0.75M on the next), but
  the share matched TradingView on all three days checked.

**The daily row.** Schwab's daily history ends at the previous session: at 22:00 CT
on 2026-11-27 the last bar was still 2026-11-25. So the day's row is written twice:

1. At `close + settle_min` (the early close on a half day, through
   `session_close_on`): one `/quotes` call for the closing `$ADVN`, `$DECN`,
   `$TRIN`, VIX and 3-month VIX, plus the settled new highs / lows from
   `cache:market:new_highs`. Source `schwab_close_quote` / `own_count`. The engine
   decides on this row.
2. The next morning: Schwab's history bar replaces the quote-sourced values
   (`schwab_history`). A difference above a tolerance is a counted degrade, and a
   phase decision that would have differed is logged.

The live `$UVOL` quote is never read: on 2026-11-27 it returned 92233720368.54776,
the 64-bit ceiling, on a strong up day. That is exactly the kind of day a 90% rule
exists for.

**Backfill**, `tools/breadth_backfill.py`:

- `--source schwab`: run on prod; about a dozen `/pricehistory` calls for twenty
  years. Verified by row counts and by comparing the up-volume share with
  TradingView on a sample of days.
- `--source csv --series <name> <file>`: loads a series a Claude session pulled
  through the TradingView connector (one `get-ohlcv` call per series). The file is
  copied to prod by hand; it never enters git.

## The engine and its view

`services/market_svc/market_cycle.py`: pure, like `market_read.py`. Daily rows in,
a plain dict out; no I/O, no clock read (the session is an argument, so a replay
over history runs the same code the service runs). It imports nothing from
`compute`. A thin `publish` validates the payload with a small model in
`shared/contracts/market.py` before `cache_set`; an invalid payload is not published
and is a counted degrade.

`cache:market:cycle` (v1, a sketch):

```json
{
  "v": 1, "as_of": "2026-11-27",
  "phase": "bottoming", "level": "early", "path": "normal",
  "since": "2026-11-20", "sessions": 4, "prior_phase": "declining",
  "anchor": {"kind": "low", "level": 6910.39, "day": "2026-11-20"},
  "ends_if": [{"key": "failed_bottom", "text": "a close below 6,910.39 with new lows past the washout's"}],
  "holding": [{"key": "vix_inverted", "met": true, "facts": {"ratio": 1.05}}],
  "next": {"phase": "advancing", "met": 1, "of": 5, "signs": []},
  "events": [{"day": "2026-11-27", "key": "net_new_highs_positive"}],
  "rally_day": 4,
  "missing": [],
  "measured": {"status": "unmeasured"}
}
```

- `measured` carries the study's verdict for this phase once there is one (its
  typical lag and false-alarm rate), so a reader can say how far to trust the label
  without knowing the study exists. Until then it says `unmeasured` and every
  surface says so.
- It is published once per session and on a morning correction, so it carries no
  timestamp of its own beyond `as_of`.
- `cache:*` is readable by the public process, so writing the view publishes it. It
  holds only facts derived from Schwab and our own counts. No TradingView-sourced
  value is ever written to a cache view or a site file.
- Scheduling follows the pattern in `scheduler.loop`: a `refresh` beside the poll
  that returns at once when nothing is due and never raises.

## The three surfaces

**The Desk.** A cycle panel of its own: the four phases with the current one and
its level marked, the evidence holding it, the signs of the next phase, the anchor
and what would end it.

It is **not** an eighth Market read row, which is what I first suggested. The
Market read's rows are verdicts for stocks *today* on a 15-minute clock, and the
weather card's sky is a count of those verdicts: a cycle row would change the sky
and the tally for a reason that has nothing to do with today. `desk.render` is
under a size ceiling that can only be lowered, so the panel is built in a sibling
module and reached from a builder `render` already calls.

**Market weather.** A season beside the sky: weather is today, the season is the
climate. Advancing is summer, Topping autumn, Declining winter, Bottoming spring
*(my call: the mapping)*. `site_weather` reads the view at each slot's write, as it
reads the regime, and adds one `season` field; `weather.js` draws one chip. The
season never changes the sky. No reading, `unclear` or `unmeasured`: no chip.

**The market reports.** These run outside this repo (`vps2:~/neuralstrike-reports`).
`remote_collect.py` adds the view to its snapshot; `REPORT_GUIDE.md` gains a cycle
line for every slot ("Advancing, day 34; 2 of 6 topping signs"), the evidence table
for the close and the Sunday report, and calls the scorecard can grade ("still
Advancing by Friday, 70%"; "Bottoming ends if the index closes below 6,910"). The
plain-English rule applies: a phase is named in words, never by key.

Later, not in the first slices: a spoken Desk alert on a phase change, and the
phase's measured record on Model Health.

## Why measure first, and how

The five-state classifier is the precedent. Validated over five years, its edge was
thin and sat in the two middle states; Bullish underperformed neutral, and Bearish
never fired once, 2022 included. A cycle label can fail the same way, and the sample
here is smaller: about seven or eight full cycles since 2007.

`tools/market_cycle_study.py` writes `docs/reference/market-cycle-study.md`:

- **Rules first.** The rules and thresholds are committed in config before the
  first run. The record states what was changed after seeing results, if anything.
- **Mechanical reference points.** Tops and bottoms are not hand-picked: a decline
  of at least `min_drawdown_pct` (10) from a closing high defines the peak and the
  trough, by code.
- **What is measured.** Per phase: the forward 20- and 60-session index return and
  worst drawdown. Per turn: how many sessions and how many percent after the true
  extreme the phase was called, and how many calls had no extreme behind them.
- **Baselines the cycle must beat.** Always-Advancing; the index above or below its
  200-day average; the same with the 50-day. Rules that only restate "price is
  above its average" add nothing and are dropped.
- **Out of sample.** Thresholds chosen on 2007 to 2018 are scored on 2019 onward.
  Percentile rules use trailing windows only. Any fitted parameter goes through
  `shared/ml`'s walk-forward, per the standing rule.
- **Which inputs earn a place.** Each sign is scored alone and by what it adds to
  the Schwab-only set. This is what settles options A, B and C under "Data".
- **The bar** *(to agree before the run)*: a phase is shown on a public surface only
  if its turns come earlier than the 200-day baseline's on most reference points
  without more false calls. A phase that misses the bar is shown on the private Desk
  as unmeasured, or not at all.

## Configuration

`config/market_cycle.toml`, read through `shared/config_toml.toml_loader`, with
every key in `webgui/config_schema.py`: the switches (`enabled`, `public`), the
settle time, the trend windows, every sign's threshold and window, the quorums,
`confirm_sessions`, and the stale limit. The windows that define a label (13-week,
52-week) stay in code beside the words that explain them, as in `new_highs.py`.

## Build order

Each slice is usable alone, and each later one depends on the study's answer.

1. **The breadth store and both backfills.** The store, the Schwab backfill, the
   daily row (close quote, then the morning correction), the TradingView CSV load.
   Usable alone: it starts the history nothing else can recreate.
2. **The study.** The engine as a pure module, replayed over the store; the
   reference record. Ends with a decision the operator makes: which phases and
   signs ship, and where.
3. **The view and the Desk panel** (private).
4. **The weather season and the reports**, for the phases that passed the bar.

There is no dev environment. Slice 1 is verified by running the backfill on prod
(new store, read-only against Schwab) and checking the row counts and the
TradingView cross-check; the pages by the local harness.

## Documentation

When a surface ships: the Desk's page help, the User and Reference guides, the
Technical Reference (rules, thresholds, the daily clock), the Glossary (the four
phases, washout, follow-through day, breadth thrust, distribution day), and the
weather card's symbol sheet. The measured verdict goes to `docs/reference/`.

## Open questions for the operator

1. **Level words.** `early` / `established`, shown per phase as in the table? Or
   the doc's own stage names for Bottoming (washout, retest)?
2. **Public before measured?** I recommend no: the private Desk may show an
   unmeasured phase, the site and the reports wait for the study.
3. **Option A's cost.** About 500 Schwab requests each night after the close for
   the S&P 500 reconstruction. Acceptable, if the study says participation matters?
4. **TradingView's terms.** The backfill stores its history privately for our own
   research. I have not read the terms; someone should before the first load.
5. **The bar.** What result would make you ship a phase, and what would make you
   drop it?
6. **The season mapping** and whether the weather card should show a season at all.

## Appendix: what was measured on 2026-11-27

**TradingView connector.** Breadth symbols answer only through the chart tools
(`get-ohlcv`, `run-pine-indicator`); symbol search, quotes and the screener return
nothing for them. First daily bar: `USI:ADV` / `USI:DECL` 1991, `USI:UVOL` /
`USI:DVOL` 1992, `INDEX:S5TH` 1990, `INDEX:MMFI` / `MMTH` / `MMTW` 2002,
`INDEX:HIGN` / `LOWN` 2001, `INDEX:MAHN` / `MALN` / `M3HN` / `M3LN` / `M1HN` /
`M1LN` 2005, `USI:PCC` 2006, `USI:PCCE` and `INDEX:S5FI` 2007,
`FRED:BAMLH0A0HYM2` 1996. One Pine call over 13 symbols returned the whole
checklist. Traps: `USI:TRIN`'s early history is junk; a zero count is stored as
0.01; a screener filter on a derived field (`price_vs_ema50_pct`) was silently
ignored and returned all 503 S&P 500 members; the "more than half of the daily
quota used" notice appeared after about 30 calls.

**Schwab, through the prod proxy.** Twenty years of daily bars for `$ADVN`,
`$DECN`, `$TRIN`, `$UVOL`, `$DVOL`. A bar is stamped midnight Central of its
session. ⚠ The breadth series carry weekend-dated junk bars (18 of 5,021 in
`$ADVN`: Friday's value repeated on Saturday and Sunday) and `$TRIN` has 128
zero closes; `$SPX` has neither, so a session is a day `$SPX` has a bar. Invalid
symbols: `$NYHGH`, `$NYLOW`,
`$NYHGH.X`, `$NYLOW.X`, `$NEWH`, `$NEWL`, `$MMFI`, `$SPXA50R`, `$NYA50R`,
`$PCALL`. Zero or empty: `$VOLD`, `$ADD`, `$UVOL/Q`, `$DVOL/Q`. The first nine are
the candidates `sentiment-dashboard/live_composite._BREADTH` asks for, so the
sentiment breadth score has been running on advancers and decliners alone; that is
a separate task, not part of this design.

**The operator's rules against the 2026-11-20 low** (one read of TradingView's
feed, dates converted by hand, recorded as a worked example and not as a finding):

| Rule | Reading | Met |
|---|---|---|
| Under 15 to 20% above the 50-day | 26.2% (all US), 42.7% (S&P 500) | No |
| A 90% downside day | 87% down on 11-19, 80% on 11-20 | No |
| VIX above 3-month VIX | Ratio 1.05 on 11-20, 1.16 on 11-23 | Yes (the VIX level was not read) |
| Equity put/call extreme | 1.02 | Not judged: no percentile computed |
| New lows extreme | Net 3-month lows −546 on the NYSE, about a fifth of issues | No |

From the low at 6,910.39 the index closed at 7,297.49 four sessions later (+5.6%)
with no retest. Net new highs turned positive on 2026-11-27 on both the 3-month
(+76) and 52-week (+28) counts; the breadth-thrust average went from 0.32 to 0.54
against the 0.615 it needs. By the doc's own thresholds this was a partial washout
followed by a recovery with no Stage 2, which is why `v_bottom` is a legal path.
