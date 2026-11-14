# Range forecast, scored on Model Health (ML roadmap Phase 2, approach A)

Date: 2026-11-14. Status: approved approach, design for review.

## Intent

Find out, on the history the app already holds, whether the range a session
will still travel can be forecast better than the two simple predictions a
trader already has: recent realised volatility, and the move the options imply.
The answer appears as rows on Model Health. Nothing else changes.

The operator's choices: Model Health only (no trading screen); approach A, score
every night from stored bars; a live intraday forecast (approach B) is built only
if this one passes.

## Goals, non-goals

- **Goal.** For SPY, QQQ and `$SPX`, at each 15-minute mark of the session, a
  forecast of the high-to-low range still to come over the next 30 minutes, the
  next 60 minutes and the rest of the session, as a 10th, 50th and 90th
  percentile. Scored out of sample against two baselines, each day counted once.
- **Not now.** No live forecast, no forecast on any trading screen, no condor
  tag, no day-type chip, no IWM (no stored bars), no economic-release or earnings
  inputs (stores start late September), no net gamma (Phase 4), no LightGBM.
- **Anti-goal.** A number that looks predictive because the future leaked into
  it. Every input is taken from bars that closed at or before the forecast time.

## The data

`scalp.db` `bars`, 5-minute interval, regular session 08:30 to 15:00 CT.
Measured on the 2026-11-13 backup: 159 complete sessions for SPY and QQQ, 199 for
`$SPX`, 198 for `$VIX`, 199 for `$VXN` (complete = all 78 bars). A session missing
any regular bar of the symbol or of its volatility index is left out whole, which
also leaves out early-close days.

## Rows

One row per (symbol, session, forecast time, horizon).

- **Forecast times.** Every 15 minutes from 08:45 to 14:30 CT (config). A time
  whose horizon runs past 15:00 is left out for that horizon; "rest of session"
  runs to 15:00.
- **Target.** `log(range / price_t)`, where range is the highest high minus the
  lowest low of the bars that START at or after t and end by t + horizon, and
  price_t is the close of the last bar ending at t.

## Inputs (all known at t)

| Input | Built from |
|---|---|
| Realised volatility, last 30 minutes | sum of squared 5-minute log returns, logged |
| Realised volatility, session so far | same, from 08:30 |
| Realised volatility, previous session | whole previous session |
| Realised volatility, previous 5 sessions | mean of the five, logged |
| Overnight gap | `log(1 + abs(open / previous close - 1))` |
| Implied volatility | `$VIX` (SPY, `$SPX`) or `$VXN` (QQQ) close at t, logged; and its change since the open |
| Volume so far | SPY's (or QQQ's) volume since the open against its mean at the same time over the previous 20 sessions, logged; `$SPX` uses SPY's |
| Time | minutes left to 15:00 and the horizon's minutes, logged |

A log of a zero realised volatility takes a small floor (config). An input that
cannot be built (no previous session yet) leaves the row out.

## Models and baselines

All three are fitted the same way, so the comparison is about information, not
method: a ridge regression on the log target, then the 10th, 50th and 90th
percentiles of its training residuals added to the fitted mean (a
location-and-spread model). Fitted separately per symbol and horizon.

| Name | Inputs |
|---|---|
| **Realised-volatility yardstick** (the HAR baseline) | the four realised-volatility inputs and the time inputs |
| **Implied move** | implied volatility scaled to the horizon (`log(iv) + 0.5 log(minutes)`) and the minutes left, so it too knows ranges are wider near the open and close (added during the build: without it the implied move lost partly on time of day alone) |
| **Forecast** | every input above |

Using fitted residual percentiles for the implied move makes it a fair opponent:
it is the options' own number, calibrated to how ranges actually came out.

## Scoring

- **Walk-forward by session.** Each day is forecast by a fit on every earlier
  day only (at least `min_train_days`, 40). Fits are ridge closed forms, so
  refitting per day is seconds in all.
- **Loss.** Pinball loss of the three percentiles, averaged, on the log target.
  Each day weighs one (rows weigh `1 / rows of that day`).
- **Verdict per baseline.** `metrics.day_bootstrap_diff` of forecast loss minus
  baseline loss, then Model Health's existing rule: under `min_days` (40) scored
  days *Too few days*; interval below zero *Beats*; above zero *Does not beat*;
  else *Matches*.
- **Coverage.** The share of outcomes inside the 10th to 90th band (aim 80%).
- **Drift.** Each input through the existing session-shuffle drift check.

The roadmap's pass mark: beats both baselines over 40+ scored days. With the
history on hand that is about 119 days for SPY and QQQ and 159 for `$SPX` on the
first night.

## Where it runs

- `services/options_svc/range_forecast.py`: rows, inputs, fits, scores. Reads
  bars through `scalp_store.bars`; imports nothing from `compute`. The ridge and
  pinball loss go in `shared/ml` (`linear.ridge`, `metrics.pinball`), with tests.
- `model_health.build` calls it and adds a `range` list to the payload: one entry
  per (symbol, horizon) with the losses, both verdicts, coverage, days and drift.
  It runs in the same nightly step and the same Recompute; it publishes with the
  rest. A failure in it degrades `options.range_forecast` and leaves the other
  models' rows published.
- Config: a `[range]` table in `config/model_health.toml` (enabled, symbols, the
  volatility index per symbol, horizons, first and last forecast time, step,
  min_train_days, min_days, ridge strength, the zero floor), each in Settings.

## On the page

A second table under the first, **Range forecast (shadow)**:

```
Range forecast (shadow)
Symbol  Horizon   vs realised volatility   vs implied move        Inside 10-90   Days  Inputs
SPY     30 min    Beats  -0.021 (…)        Matches  -0.004 (…)    78%            119   Steady
SPY     60 min    …
SPY     Rest      …
```

The report file gets the same rows. The page help, manuals and CHANGELOG say what
it is and that nothing uses it.

## Approaches considered

| Approach | Trade-off | Chosen |
|---|---|---|
| **A. Score nightly from stored bars** | Verdict on the first night from ~160 days; no live process | Yes |
| B. Also forecast live every 15 minutes | Needed only once a forecast is shown while trading | Later, if A passes |
| C. One-off study tool | No nightly row, no drift | No |

## Left to the builder

- Exact column names in the payload, provided the page and report carry every
  number above.
- The ridge strength default (start at 1.0 on standardised inputs).
