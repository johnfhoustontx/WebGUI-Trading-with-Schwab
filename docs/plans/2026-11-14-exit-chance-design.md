# Exit chance (shadow) - design

ML roadmap Phase 3b ([roadmap](2026-11-14-machine-learning-roadmap.md)). Approved
approach: **B first**, on Model Health only, as in Phase 2.

## Intent

Earlier measurements point at exits, not entries. The 2026-08 calibration found the
edge is in cutting losers. The 2026-11 exit replay found that closing at 50% of the credit
beats riding to break-even, and that 27% of losers had been up +50% before losing.
The question an exit model answers, at any mark of an open credit position:
**what is the chance it still reaches 50% of its credit before the app's own exits
end it?** If a model reading the path so far answers that better than the distance
already travelled does, an advice chip on open positions is worth building
(roadmap section 4). If not, that is recorded and nothing is built.

## Goals, non-goals, anti-goals

- **Goal.** A nightly, walk-forward score of that chance for each captured-signal
  population (SWING, 0DTE, INCOME, never pooled), shown as a third table on Model
  Health and a section of its report, against a named baseline.
- **Non-goals.** No chip on the Paper Account or Ledger. No prediction for open
  positions. No entry model (3a comes later, on the same labels).
- **Anti-goals.** Nothing reads it. No exit rule, paper book or order changes.

## Approaches

- **A. Entry model (3a):** the chance of the target per capture. Its test needs one row
  per trade, so it is thinner, and Swing's verdict waits longest.
- **B. Exit model (3b): chosen.** One row per mark, aimed where the measured edge is,
  against a hard opponent (distance travelled).
- **C. Both at once:** about twice the size, with A mostly reading "too few days" at first.

## What the store holds (2026-11-13 backup, measured)

Closed credit captures (PCS, CCS, IC; tracked rows left out), manual closes left out,
at least two marks:

| Type | Trades | Entry days | Marks before the first +50% | Share that later reach +50% |
|---|---|---|---|---|
| SWING | 255 | 33 | 15,802 | 60% |
| 0DTE | 76 | 21 | 3,314 | 19% |
| INCOME | 35 | 21 | 12,174 | 48% |

Share that later reaches +50%, by where the mark sits (P&L as a fraction of the credit):

| | under -1 | -1 to -0.5 | -0.5 to -0.25 | -0.25 to 0 | 0 to 0.25 | 0.25 to 0.5 |
|---|---|---|---|---|---|---|
| SWING | 45% | 51% | 52% | 54% | 63% | 72% |
| 0DTE | 0% | 1% | 6% | 13% | 37% | 41% |
| INCOME | 29% | 12% | 33% | 51% | 46% | 82% |

For 0-DTE, distance alone explains most of it. For Swing it is nearly flat, so the
path and the position carry whatever there is to find.

## The label

- **Rows.** Every mark of a closed capture BEFORE the first mark at or above
  `tp_frac` (from `[stops]`, 0.5) of the credit. Marks after it are left out: the question is
  answered by then.
- **y = 1** when a later mark, or the close itself (`signal_outcomes.realized_pnl`,
  which holds expiry settlement), reaches `tp_frac` of the credit; **y = 0** when the
  series ends first. "The app's own exits" are whatever closed it (money, delta,
  break-even or time stop, or expiry short of the target). Those rules changed over
  September to November. The label is read as "under the rules then running", and the
  report says so.
- **Left out.** Manual closes, tracked rows, non-credit strategies, trades with fewer
  than two marks, and `TIME_STOP` closes within `[stops] time_stop_min_hold_min` of
  capture (the first-mark stop fixed on 2026-11-14: those trades never had a path).
- **Thinning.** One mark per `thin_min` (30) minutes of each trade, the first
  kept. Neighbouring 5-minute marks repeat one another, and this cuts the work about
  six times.

## Inputs (as of the mark, nothing later)

`pnl_frac` (P&L / credit), `peak_frac` and `worst_frac` (best and worst so far),
`life_used` (time held / time from capture to expiry), `log_mins_left` (to the 16:00 ET
settlement, through `options_calculator.expiry_time_to_years`), `short_delta`
(absolute, now), `delta_change` (now minus entry, absolute values), `cushion` (spot's
distance from the short strike in percent, toward safety; an IC takes its nearer
side), `entry_iv_rank`, and the structure (IC and CCS indicators; PCS is the base).
A missing input is filled with its training mean and gets a missing-indicator column.
It never becomes 0.

## Models and baseline

- **Baseline, named before the fit: distance.** The training days' weighted rate in
  `pnl_frac` buckets (edges `[-1, -0.5, -0.25, 0, 0.25]`, configurable). It is the
  table above, learned walk-forward.
- **Model.** The weighted MAP logistic in `shared/ml/logistic.map_fit` (zero prior
  mean, precision `penalty`), inputs standardised on the training rows.

## Scoring (Model Health's rules)

- **Walk-forward by entry day.** A day's trades are predicted by a fit on trades that
  CLOSED before that day began. A trade still open on the test day must not leak its
  ending into training. Scoring starts once `min_train_days` (15) entry days are
  available.
- **Weights.** Each entry day weighs one, split equally over its trades, and each
  trade over its kept marks. A long trade counts no more than a short one.
- **Loss and verdict.** Brier per row. The model minus the baseline goes through
  `metrics.day_bootstrap_diff`, clustered by entry day, and then Model Health's verdict
  rule with `min_days` (40 scored days). Also published: both Brier scores, the
  reliability bands, and drift on `short_delta`, `cushion` and `entry_iv_rank`, with
  the session-shuffle check.
- **When the verdict comes.** Scored days = entry days − 15. Swing has 18 today and
  reaches 40 after about 22 more entry days, roughly a month. 0-DTE and Income take
  longer. Until then each row reads "too few days" and still shows the running figures.

## Where it lives

- `services/options_svc/exit_chance.py`: reader (signals.db, read-only, with
  `structures.not_tracked_sql`), rows, walk-forward, score. It imports nothing from
  `compute`.
- `model_health.build` adds `payload["exit"]` (one entry per type, `None` when off or
  failed; a failure is the degrade `options.exit_chance`). It runs in the same nightly
  step and Recompute.
- `[exit]` in `config/model_health.toml`: `enabled`, `types`, `thin_min`,
  `min_train_days`, `min_days`, `penalty`, `buckets`. Each has a Settings entry.
- The page: a third table, **Exit chance (shadow)**: type, verdict against distance,
  difference and range, both Brier scores, rows, days. Drift is listed beneath it in
  words. The report gets a section. Help, the three manuals, routes and the CHANGELOG
  are updated in the same change.

## Left to the builder

The exact column words; the report's layout; test fixtures (synthetic paths in a
temporary signals.db).
