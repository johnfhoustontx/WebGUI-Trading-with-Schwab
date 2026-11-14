# Model Health (roadmap Phase 1) — design

Date: 2026-11-14. Status: proposed. Roadmap:
[2026-11-14-machine-learning-roadmap.md](2026-11-14-machine-learning-roadmap.md).
Builds on [the ml foundation](2026-11-14-ml-foundation-design.md) (`shared/ml`).

## Intent

Several numbers in the app state a chance. Nothing checks, in one place, whether
those chances come true as often as they say, or whether the inputs a model
reads still look like the ones it learned from. Model Health is that place: a
private page and a nightly report that **only report**. The operator's words:
"Just flag it on the page, don't change anything."

The first reading it will show is already known: on prod's data the 08:36 scalp
models do worse than the usual rate out of sample (Brier 0.26 to 0.29 against
0.23 to 0.26), and the 15-minute models only match it.

## Goals

1. **A nightly computation** in `options_svc` that, for each model, works out:
   - how its stated chances compared with what happened, by tenths
     (reliability) and as a Brier score;
   - the same for the usual rate over the same rows, and the difference with a
     day-clustered 95% interval (`shared/ml.metrics.day_bootstrap_diff`);
   - a verdict: **Beats**, **Matches**, **Does not beat** the usual rate, or
     **Too few days** (the rule is under "The verdict");
   - drift per input: the last few sessions' readings against the earlier ones.
2. **One cache view**, `cache:options:model_health`, published from it.
3. **One dated report file** per run, written from the same payload, so the
   history can be read outside the app and compared across weeks.
4. **A private page**, More → **Model Health** (`/model-health`), that reads the
   view, with a **Recompute** button that enqueues the command.

## Non-goals

- **No model, chance, rule or other page changes.** No recalibrated figure is
  shown anywhere, including on Model Health. The Scalp page's own baseline flags
  stay as they are.
- No prediction log and no Finder probability: the Finder records nothing to
  score yet; that arrives with the log in Phase 2.
- No alert, push, or chime. A failed verdict is shown, never sent.
- No public screen.

## Anti-goals

- A verdict printed from too few days. Below the minimum it says so and shows
  the counts, never "beats" or "does not".
- A comparison that lets a row see its own day: the usual rate for a row uses
  earlier days only (`shared/ml.baselines.usual_rates`).
- A drift flag raised by readings that are simply absent on a day: a missing
  reading is its own bin, and a jump in the missing share is reported as such.

## The models in Phase 1

| Model | Stated chance | Outcome | Live rows (prod, 2026-11-13) |
|---|---|---|---|
| **08:36 scalp chance** | `calls.json` → `entry.chance.by_role[role]` | `outcomes` at the current `iv_scale`, `interval = 5`, labelled by `scalp_study._label` (skip left out) | 19 days |
| **15-minute scalp chance** | `intraday_calls.json` → `chance.by_role[role]` | `intraday_outcomes` at the current `iv_scale`, same label | 18 days, 1,134 calls |
| **Captured signals' delta-implied win rate** | `shared.calibration.priced_win_rate(entry_short_delta, strategy)`; a row where it is None (an iron condor, or no delta) is left out | win when `shared.calibration.r_multiple(realized_pnl, entry_max_loss)` is above 0 (a None R is left out) | 1,691 closed, 99 days |

The two scalp models read the chance each call froze: `calls.json["chance"]`
and `intraday_calls.json["chance"]`, each with `by_role` and `pushes`. A live
row is a call's chance for one role joined to the study's outcome row with the
same (day, slot, symbol, role) at the current `iv_scale`
(`scalp_study._scale_key`); the 08:36 call's slot is `"08:36"` and its outcomes
are `interval = scalp_study.VERDICT_INTERVAL`. `intraday_outcomes` has no
interval column.

Signals are read by `tools.signal_calibration.load_rows`, the reader the
service's existing `cache:options:calibration` already uses. It carries every
field needed, leaves tracked rows out, and drops captures made outside the
regular session (mispriced against the prior close). No new reader.

For each scalp model the page also shows the latest weekly refit's own
out-of-sample result, already stored in `prob_models` (`walk_forward`: `weeks`,
`brier`, `brier_usual`, `beats_usual`, `calibration`), labelled as the refit's
history, beside the live record.

Details that settle what is counted:

- **Weighting.** A row weighs `1 / rows of its (day, symbol, role)` for the scalp
  models and `1 / signals of its first_seen_date` for signals, so each day
  counts once.
- **Usual rate.** Scalp: the group is (symbol, role) for 08:36 and (symbol, role,
  block) for 15-minute. `baselines.usual_rates` is fed the study's FULL outcome
  history (every stored row at the current scale, weighted as above), and the
  live rows take the value of their own row. Signals: the group is the
  `scanner_type`, over every closed signal. A row whose usual rate is None (its
  group's first day) is left out of both Brier sums.
- **Signals left out:** tracked rows, rows with no stated chance, rows with no
  usable R. **One model per `scanner_type`** (0DTE, SWING, INCOME, as stored);
  pooled numbers are not shown, because a pooled table has misled this project before.
- **Known caveat, stated on the page:** many captured signals from September to
  November were time-stopped on their first mark, so their recorded outcomes are
  not hold-to-target results. Model Health scores what was recorded and says so.

## Drift

Per model, per input, the **population stability index** (PSI) of the last
`recent_sessions` sessions against the earlier live sessions, over ten bins cut
at the reference's deciles plus one bin for "no reading". A session is a day
with a live call (scalp) or a `first_seen_date` (signals). Every reading is a
float or None (checked on prod: 17 input kinds, none categorical); duplicate
decile edges are merged, so a 0/1 input such as a block flag gets two bins.

- **Scalp models:** each input of the call's `chance.pushes[].reading`.
- **Signals:** `entry_iv_rank`, `entry_short_delta`, `entry_score`, `dte_at_entry`, per family.
- Bands, from config: under `moved` (0.10) is **steady**, up to `shifted` (0.25)
  is **moved**, above is **shifted**. With fewer than `min_reference_sessions` of
  reference it reads **too little history**.
- The page lists only moved and shifted inputs, worst first, with the share of
  "no reading" before and after, so "the sentiment reading went missing" reads as
  that and not as a change in sentiment.

PSI goes in `shared/ml/metrics.py` (`psi(reference, recent, bins=10)`), with its
own tests against a hand-computed case.

## Where it runs

- **Module:** `services/options_svc/model_health.py` (imports nothing from
  `compute`); one adapter function per model returning rows of
  `(day, group, p, y, weight)` plus its drift inputs, and one `build(now)` that
  runs the adapters, scores them with `shared/ml` and returns the payload.
- **Schedule:** the LAST step of the existing 16:20 scalp branch in
  `scheduler.py` (`_scalp_bars_branch`), after the intraday study rebuild and
  guarded on its own like each step there, skipped when `enabled` is false. A
  clock slot was rejected: the study rebuild takes minutes, and nothing would
  make a later slot wait for it. The 15:05 settle has already run, so the
  signals are current too. No new slot.
- **Command:** `model_health_rebuild` in `handlers._COMMANDS`, on the slow lane
  (`SLOW_LANE`: it reads stores for seconds and changes no paper book). It runs
  whatever `enabled` says.
- **Output:** `cache:options:model_health` (with `skip_unchanged`: the payload
  carries its data date, not a wall-clock stamp, so an unchanged rerun bumps no
  version), then the report file at
  `services/options_svc/data/model_health/<date>/report.md`. A failure is a
  `_degrade.degraded("options.model_health")` and publishes nothing.

## The page

```
Model Health                                 Data to 2026-11-13 · [Recompute]

Model                    Verdict                 Brier vs usual (95%)        Days  Drift
08:36 scalp chance       Too few days (19 of 20) 0.271 vs 0.244              19    2 moved
  Weekly refit history   Does not beat           0.290 vs 0.259, 33 weeks    191
15-minute scalp chance   Too few days (18 of 20) 0.241 vs 0.240              18    1 shifted
  Weekly refit history   Matches                 0.241 vs 0.241, 33 weeks    199
Delta win rate · 0DTE    Does not beat           …                           54    steady
Delta win rate · SWING   …
(figures illustrative)

▸ 15-minute scalp chance
  Says vs happened   0-10%: — · 30-40%: said 36%, happened 31% (212) · …
  Inputs that moved  sentiment: no reading 4% → 100% (shifted)
                     price:vol_change PSI 0.14 (moved)
```

- Built from the page kit (`kit.page`, `kit.header`, `kit.table`, `kit.button`),
  Tailwind classes only; verdicts map to fixed classes (beats / does not / too
  few / matches).
- Numbers through `pages/fmt.py` (Brier three decimals is a deliberate exception
  to the two-decimal rule for ratios, because two decimals hide the differences
  that decide the verdict; to be confirmed against `fmt` conventions).
- A version-gated poll (`view_watch.watch_view`) repaints when the view moves.
- `page_help.py` gets the page's guide.

### The verdict

Checked in this order, per model:

1. Fewer than `min_days` distinct days → **Too few days (n of min)**, with the
   Brier pair still shown.
2. The interval of `day_bootstrap_diff(model_loss, usual_loss, …)`, each row's
   loss its Brier term `(p − y)²`, so negative means the model's chance was closer:
   - `hi < 0` → **Beats**;
   - `lo > 0` → **Does not beat**;
   - otherwise (including an end exactly at 0) → **Matches**.

The refit-history rows have no per-row data, only the stored `walk_forward`:
**Beats** when its `beats_usual` is true (the refit's own margin rule), **Does not
beat** when `brier > brier_usual`, otherwise **Matches**; no interval; "Days" is
the stored model's `n_days`, with its `weeks` beside it. They are labelled as the
refit's history.

## Configuration

`config/model_health.toml`, each key in `webgui/config_schema.py`:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | true | false skips the nightly step (Recompute still works) |
| `min_days` | 20 | live days below which the verdict is "too few days" |
| `recent_sessions` | 5 | drift: the recent window |
| `min_reference_sessions` | 8 | drift: the least reference history to judge |
| `psi_moved` / `psi_shifted` | 0.10 / 0.25 | drift bands |
| `bootstrap_draws` | 2000 | resamples for the interval |

## Docs touched in the same change

`docs/webgui-routes.md` (the route), `page_help.py`, the User Guide and Reference
Guide (the page), the Technical Reference (Brier, reliability, PSI, the verdict
rule), the CHANGELOG. No architecture diagram changes (no new process or port).

## Approaches

| | | Trade-off |
|---|---|---|
| **A. Service computes a view, page reads it** (chosen) | Fits the tiers; Recompute on demand | A new module, slot and command |
| **C. A nightly report file** (chosen, with A) | Dated history readable outside the app | Written from A's payload, so no second computation |
| B. Prediction log first | General; the only route to the Finder | Duplicates records the stores already hold; the Finder would show nothing for weeks. Left to Phase 2 |

The operator chose A and C. Combining them as one computation with two outputs
is the builder's (Claude's) call.

## Left to the builder

- The payload's exact field names (one dict per model, a `models` list in page
  order, `data_date`).
- Report file layout, provided it carries every number the page shows.
- Whether the report folder needs a backup entry (it lives under an already
  gitignored data folder; the backup test decides).
