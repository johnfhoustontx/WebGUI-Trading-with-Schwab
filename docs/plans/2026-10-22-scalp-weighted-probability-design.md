# Opening Scalp: a weighted chance of reaching the target — design

**Date:** 2026-10-22 · **Status:** draft for the operator's approval
**Builds on:** [the 08:36 call](2026-10-18-opening-scalp-design.md),
[the 15-minute calls](2026-10-20-scalp-intraday-calls-design.md), the page redesign
of 2026-10-21 (glance table + folded detail).

## 1. Intent

The operator reads the Opening Scalp page to decide a 0DTE scalp on `$SPX`, SPY or
QQQ. Today the glance table's **Confirmation** column says "Mixed · 3 of 4 sources":
a count, not something to size a trade on. The operator asked for that column to give
**the probability of reaching the profit target, weighted from every factor the app
has**, and for the **slope of the moving average and momentum** to count as well, on
the **5-minute chart (primary), the 15-minute (secondary) and the hourly (third)**.
They asked for the weights to be **approximated now and adjusted weekly**, for any
useful history in past data to be used, and for **assumptions where there are gaps**.
The **08:36 call gets the same weighted probability**, from its premarket inputs.

Why it has to be fitted: a number labelled a probability is read as one. If the
weights were set by hand, the column would look like a chance of winning without
being one. So every weight is learned from past days where history exists, starts
from an **assumed** value where it does not, and the page says which.

## 2. What the operator sees

- **Glance table.** The Confirmation column becomes **Chance of target**: one
  percentage (two decimals, the house rule) for the trade the row already names (the
  highlighted trade when there is one, else the trade with the move); a row with no
  direction (a flat tape) shows the dash. Under it, one small line: when the weights were fitted and
  whether they beat the usual rate on days they were not fitted on, e.g. "fitted Oct
  25 · beats the usual rate on unseen days: no". **Usually wins** (the base rate)
  stays beside it, so the two can be compared at a glance.
- **Each symbol's section.**
  - The **15-minute call** and **08:36 call** cards each gain a **Chance of target**
    column: one figure per trade, all four trades (a credit spread's target is
    keeping half the credit).
  - The confirmation block is replaced by **What moved the chance**: per factor, its
    reading, which way it pushed the chance for the trade with the call (in
    percentage points), and how many days of history its weight rests on. A
    factor whose weight is still the assumed one says **assumed**.
  - The confirmation WORD (Confirms / Mixed / Contradicts) and its "n of 4
    sources" leave the page. They are still computed and recorded (the scorecard's
    split by word and the readings table keep them), because the recorded history is
    what the weekly refit learns from.
- **Unchanged:** the four trades and their rules, the bucket study, its **Measured
  edge** and **Caution** marks, the usual win rates, the timeline, the context
  section, the scorecard's existing tables. The probability is a new column, not a
  replacement for the bucket study.

## 3. The factors

Every factor is a number measured **relative to the call's direction** `d` (+1 up,
-1 down): positive means it agrees with the call. Nothing at or after the call's
`entry_ts` is ever read (the existing rule of `scalp_intraday_inputs`).

| Group | Factor | 15-minute calls | 08:36 call | History |
|---|---|---|---|---|
| Price (existing inputs) | move since the open, side of VWAP, futures' last 15 min, volatility index's last 15 min (inverted), each sector fund, big-ten breadth | as `scalp_intraday_inputs.reading` | the premarket inputs `scalp_inputs` already reads | ~180 days of 5-minute bars |
| **Moving-average slope** (new) | 20-period EMA, % change over its last 3 bars, × `d` | on 5-minute, 15-minute and hourly bars | the same, at 08:35 | ~180 days |
| **Momentum** (new) | RSI(14), (RSI − 50) × `d` | on 5-minute, 15-minute and hourly bars | the same, at 08:35 | ~180 days |
| Dealer read | room to the wall in the call's direction ÷ the points to the trade's target (capped), big-ten share with room, spot above or below the gamma flip | from the gamma grids, as the live matrix computes them | the same, at 08:35 | ~36 days (§4) |
| Net premium | (call − put) / (call + put) premium LEVEL × `d` | as `scalp_confirm._netprem` | the same | ~36 days |
| Flow | directional alerts on the symbol and the big ten in the window, bull − bear, × `d` | as `scalp_confirm._flow` | the window ending 08:35 | 13 days (from Oct 5) |
| Sentiment | intraday trend state, +1 / −1 / 0 × `d` | as `scalp_confirm._sentiment` | the same | ~7 days (from about Oct 14) |
| Time of day | the block (morning / midday / afternoon) | yes | no (one time) | ~180 days |

**Bars on three timeframes.** The 15-minute and hourly bars are built from the stored
5-minute bars of the **regular session only**, aligned to the 08:30 open (15-minute:
08:30, 08:45 …; hourly: 08:30, 09:30 …, the last of the day 14:30–15:00). Only
**complete** bars count. The EMA and RSI run on across previous sessions, so at 08:51
the hourly reading is mostly yesterday's — what an hourly chart shows too. `$SPX`
uses its own bars (it prints no volume, which these two measures do not need).

**Gaps are neutral.** A factor that cannot be read at a slot (no wall because Schwab
sent no Greeks, no flow list, a missing bar) is **0 after scaling**: it neither helps
nor hurts the chance, and it teaches the fit nothing that day. It is never read as
disagreement. This is the operator's "make assumptions if there are gaps".

## 4. The history found (measured 2026-10-22)

- **Price, slope, momentum:** `scalp.db` `bars`, 5-minute, 2026-02-02 → today, the
  three symbols, the big ten, the futures, the volatility indexes and the sector
  funds. Outcome labels exist for every day: `outcomes` (08:36) and
  `intraday_outcomes` (every slot), rebuilt nightly.
- **Walls, flip, net premium:** prod's `gex_history.db` keeps 5 sessions. Stitched
  from it, the six VPS backup generations (`~/backups/prod_*`) and the four
  E:-drive copies, the per-minute grids exist for: Aug 6–7 (a dev copy — use only if
  its rows look like prod's), Aug 17–21, Aug 24–28, Sep 18, Sep 21–25, Sep 28 – Oct 2,
  Oct 5–9, Oct 12–21. About **36 sessions**. Missing: Aug 10–14, Aug 31 – Sep 17,
  Sep 19, Sep 26. **Oct 19 is suspect** (Schwab sent no Greeks: the walls are
  untrusted by the existing `walls_untrusted` rule, so they read as gaps).
- **Flow:** `flow_contract_days` (contract alerts with their bought / sold
  estimate) from Oct 5: 13 sessions. Crossover and HIRO alerts are not stored
  historically; before Oct 5 flow is a gap.
- **Sentiment:** `sentiment_intraday.db` `sentiment_intraday` (ts, sentiment, trend)
  from about Oct 14.
- **From now on** every call records its raw sources (the existing
  `intraday_readings` already does for the 15-minute calls; the 08:36 call gains the
  same record), so every source's history grows by a day each session and the weekly
  refit leans on it more each week.

The walls are recomputed from each stored grid exactly as the live matrix does
(`gamma_tool.get_directional_walls` over the GEX grid, split by spot, `net_total` as
net GEX, untrusted when net GEX is 0 or missing). The net premium is the stored
`call_prem` / `put_prem` at or before the entry.

## 5. The weights

- **One model per call kind and trade**: the 15-minute calls and the 08:36 call
  each get four models (one per trade: long with, long against, credit with, credit
  against), each pooled across the three symbols with a per-symbol intercept. The
  label is the existing outcome at the current `iv_scale`: win 1, loss or timeout 0,
  skip left out. For the 08:36 call it is the **5-minute** outcome
  (`outcomes.interval = 5`, the study's verdict interval): only that one covers the
  ~180 days.
- **Logistic regression with a prior on every weight.** Each scaled factor's weight
  has a Gaussian prior centred on an **assumed** value (config, §7). The fit is the
  most likely weights given the prior and the history. A factor with ~180 days of
  history moves far from its prior; one with 13 days barely moves. That is exactly
  "approximate now, adjust weekly": as a source's history grows, its weight follows
  its data instead of the assumption.
- **The day is the unit.** One day's slots share one tape, so a 15-minute row is
  weighted 1 / (that symbol's rows that day): each symbol-day counts once. Without it,
  21 correlated rows a day would make the fit far more confident than the evidence.
- **Scaling keeps 0 neutral.** A continuous factor is divided by its interquartile
  range over the fit's history and capped at ±3, but **never centred**: 0 means "no
  agreement either way" and must stay 0, so a gap (0) adds nothing. A factor whose
  range is 0, and the discrete ones (flow's net count is capped at ±3 and divided by
  3; sentiment and the flip side are −1 / 0 / +1), are not rescaled. The scaling is
  stored with the weights so a live call is scaled the same way.
- **Each factor has its own weight.** The dealer read is three weights (room ÷
  target points, big-ten share with room minus one half, flip side). Time of day is
  two indicators (midday, afternoon; morning is the baseline), prior 0.
- **Starting priors, written for the trades WITH the move (my approximations, all in
  config):** moving-average slope 5-min 0.15, 15-min 0.10, hourly 0.05; momentum
  5-min 0.10, 15-min 0.07, hourly 0.04; dealer room 0.15, big-ten room share 0.05,
  flip side 0; net premium 0.10; flow 0.10; sentiment 0.05; price inputs and time of
  day 0. Units: change in log-odds per scaled unit. **The trades against the move use
  the same priors negated**: a factor agreeing with the call should lower the chance
  that a bet on its reversal pays. Long and credit share the sign of their side.
- **Two prior spreads.** Price inputs, slope, momentum and time of day, which all
  have ~180 days, get a **wide** spread (1.0): their history decides them. The
  sources with little history (dealer, premium, flow, sentiment) get a **tight**
  spread (0.10): they stay near the assumption until their own days accumulate.
  "Days of history" on the page is the count of distinct days on which that
  factor had a reading (not a gap) in the fit.
- **Honesty check, every refit.** Walk-forward by week: for each past week, fit on
  the weeks before it and score that week; the first week scored is the one after
  `[prob] min_train_weeks` (8) weeks of history. The comparison is the **same usual
  rate the page shows**: the trade's base rate for that symbol (and, for the
  15-minute calls, that block) over the days before the scored week, as the
  existing walk-forward computes it. Each of the eight models gets its own result;
  the glance line reports the model of the trade the row names. **Beats the usual
  rate on unseen days** is **yes** only when that model's Brier is at least
  `[prob] min_brier_margin` below the usual rate's over the scored weeks. The
  number is shown either way; the line says which. Calibration by tenths is stored
  with the result for the record.

## 6. When it is fitted, and how a call uses it

- **Weekly refit:** Saturday at 08:00 CT, a new `[slots.scalp_prob]` in
  `config/sessions.toml`, after Friday evening's study rebuild has graded the week.
  It recomputes every factor from the stored bars and source history, fits the
  eight models, runs the walk-forward check and stores the result as a new version.
  It never deletes an older version.
- **A command** `scalp_prob_refit` on `cmd:options` runs the same refit on demand
  (slow lane, replay-guarded). The first fit runs once the history is loaded, so the
  page has a probability from the next trading day.
- **A call freezes its probability.** Each published call (08:36 and every slot)
  computes its probabilities with the newest stored weights and saves them, with the
  weights' version and each factor's push, in the frozen call. A refit never changes
  a published call, and the grader reads the frozen figure.
- **No weights yet** (before the first fit, or a fit that failed): the column shows
  the dash and "not fitted yet"; nothing falls back to a hand-made number.

## 7. Storage and configuration

- **New `scalp.db` tables only** (the existing tables are never altered):
  `prob_sources` (call kind, day, slot, symbol, raw sources json: walls, flip, net
  GEX, spot, premium, flow counts, sentiment) and `prob_models` (version, fitted
  at, call kind, trade, weights, scaling, per-factor days of history, walk-forward
  results). Both kept forever (`scalp.db` is already in the nightly backup and never
  in `DB_EXCLUDE`).
- **A one-time history loader**, `tools/scalp_prob_backfill.py`: reads gamma-history
  copies given as paths (read-only, `immutable=1`) plus prod's own flow and
  sentiment stores, and writes `prob_sources` rows for every stored session × call
  time × symbol. The E:-drive copies are read on the Windows box and only the small
  extracted rows are copied to prod (the 1.5 GB files are not moved). Re-running it
  replaces rows for the sessions it read; it never touches a live call's record.
- **`[prob]` in `config/scalp.toml`**: EMA period (20), slope bars (3), RSI period
  (14), the three timeframes (5, 15, 60), the factor caps, every prior and both prior
  spreads, the room cap, `min_train_weeks`, `min_brier_margin`, and whether the
  column is shown. Every
  key has an entry in `webgui/config_schema.py` (the standing rule).

## 8. Goals, non-goals, anti-goals

**Goals:** a probability per trade for every call, weighted from all factors, the
moving-average slope and momentum on three timeframes included; weights learned from
all usable history, assumed where history is missing, refitted weekly; the page
says how much each weight rests on and whether the probability beats the usual rate.

**Non-goals:** changing the trade rules, the bucket study or its edge / caution
marks; any trade control; pushing the probability to the phone, X or the reports;
backfilling flow alerts before Oct 5 by re-running the detectors.

**Anti-goals (a failure even if it runs):**
- a number that reads as fitted while it is mostly assumption, with nothing on the
  page to say so;
- any factor read at or after the entry (leakage), in the fit or the live call;
- a missing reading treated as disagreement;
- a refit that changes a call already published;
- the walk-forward check scoring a week the weights were fitted on.

## 9. Left to the builder

The module split (keep it out of `compute.py`; `gamma_tool` is importable directly),
the solver (numpy Newton steps are enough at this size), the exact scaling code,
the tests' fixtures, the order of the work, and the page's exact wording beyond §2,
within the house rules (page kit, Tailwind, `fmt`, two decimals). The probability is
computed in the service and published in the view; the page only shows it.

## 10. Calls I made that the operator has not ruled on

- The column shows the trade with the call; the details show all four trades.
- The confirmation word leaves the page but is still recorded.
- 20-period EMA slope over 3 bars and RSI(14), on regular-session bars.
- The starting priors in §5, negated for the trades against the move.
- Two prior spreads: wide (1.0) where there are ~180 days, tight (0.10) where there
  are few, so thin sources stay near the assumption.
- The honesty check starts after 8 weeks of training and compares with the same
  usual rate the page shows.
- Saturday 08:00 CT for the refit.
- One model per call kind and trade, pooled across the three symbols.
- Each symbol-day counts once in the fit.
- The 08:36 model learns from the 5-minute outcomes.
