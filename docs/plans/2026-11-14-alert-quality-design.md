# Alert record and alert quality (ML roadmap Phase 4, flow half) - design

**Date:** 2026-11-14 · **Status:** draft, awaiting approval
**Roadmap:** [machine learning roadmap](2026-11-14-machine-learning-roadmap.md), Phase 4

## Why

The flow alerts (unusual activity, big delta, the two HIRO-model alerts, the
premium crossover, the gamma flip) fire every session and reach the screen and
sometimes the phone. Nothing says whether any of them is followed by anything.
HIRO is held quiet and out of the rankings "until the daily report beats the base
rate"; big delta has its own push threshold for the same reason. The operator
needs a number per alert type before deciding to trust one, mute one, or rank on
one.

Today that number cannot be produced past 20 sessions: the alert list is a
day-scoped Redis view, `flow_contract_days` (unusual activity and big delta
only) is purged after 20 sessions, crossover fires are stored nowhere, and
Model Health asks for 40 days before it gives a verdict.

## Goals

1. **A. The alert record.** Every alert that fires, of every type, is written
   once to a permanent store the moment it fires. Collection only.
2. **B. Alert quality on Model Health (shadow).** Per alert type: how often the
   next 30 minutes ran wider than that symbol's usual 30-minute range at that
   time of day, against how often that symbol's other windows that same day did.
   A difference with a day-resampled interval, and the Model Health verdict.

## Non-goals

- Changing any alert, threshold, cooldown, push rule or ranking. Model Health
  only (the operator's rule since Phase 1: "just flag it, don't change anything").
- Direction (did price go the way the alert leaned). Range first; direction is a
  later question on the same record.
- Gamma features in the range forecast (roadmap approach C): deferred until the
  declared gamma regime study reads (about 2026-12-17) and the Gamma Archive
  reaches about 80 sessions (about February 2027).
- Keeping the HIRO minute series or the bought/sold tally past their purge. The
  record keeps what B needs; those are their own decision.
- Any paper book, Redis view the public process reads, or Tier-1 import change.

## Anti-goals

- A score that looks good because alerts fire on busy days. The comparison is
  against the SAME symbol's other windows on the SAME day, so a busy day lifts
  both sides.
- A score built on sessions the gamma regime study has spoken for. The gamma
  flip alert is a spot-versus-flip event, close kin to the study's question, so
  it is recorded from day one but left out of the scored types until the study
  has read (the operator adds it to the list then).
- A day of many alerts counting as many days. Each day counts once.

## Approaches considered

- **A only (collect now, score later).** Cheapest, but nothing to look at for
  months and no check that the record holds what scoring needs.
- **B only (score from the 20 sessions held).** Never reaches 40 days; crossover
  and HIRO have no history at all.
- **A and B together (chosen by the operator, 2026-11-14).** The record starts
  filling today, and the scorer proves the record is fit for purpose from the
  first night, reading "too few days" until it is not.

## A. The alert record

**Store:** `services/options_svc/data/alert_log.db`, gitignored, **kept forever
and in the nightly backup** (never in `DB_EXCLUDE`), like `scalp.db` and
`gex_archive.db`. A new store module `services/options_svc/alert_log.py` with
`db_path=None` resolved at call time.

**Table `alerts`:**

| column | meaning |
|---|---|
| `alert_id` TEXT PRIMARY KEY | the alert's own `id` (already unique per fire) |
| `session_date` TEXT | Central date of the fire |
| `ts` INTEGER | epoch seconds of the detecting tick |
| `symbol`, `type`, `side` | as on the alert |
| `detail_json` TEXT | the rest of the alert dict, minus `text` |
| `source` TEXT | `live`, or `seed` for rows copied from `flow_contract_days` |

`INSERT OR IGNORE`, so a re-sent id is a no-op.

**Where it hooks:** `handlers.run_flow_alerts`, which already gathers every
type into one `fresh` list after the cooldowns. The write goes after the
phone pushes and the cached list (a slow store must not hold back either), in its
own guard counted as `_degrade.degraded("options.alert_log")`, and before
`flow_sides_tick.after_alerts`. It runs only when `fresh` is non-empty.

**Seed:** a one-off tool, `tools/seed_alert_log.py`, copies the unusual activity
and big delta fires still held in `gex_history.db`'s `flow_contract_days` (about
20 sessions) with `source = 'seed'`. The other types have no history to seed.

**Switch:** `config/flow_alerts.toml [log] enabled = true`, with its Settings
entry.

## B. Alert quality

**Module:** `services/options_svc/alert_quality.py`, beside `exit_chance.py`,
returning entries in the shape `model_health.build` already folds in; a new
`"alerts"` key in the Model Health payload, a fourth table on the page, and a
section in the nightly report.

**Price path:** the Gamma Archive's `gex` view, fetched rows only
(`gex_history_db.fetched_only_clause`), one spot per 5-minute grid point for
each of the 147 collected symbols. A session counts once it is archived. Model
Health runs in the 16:20 nightly step and the archive at 17:00, so a session's
alerts are first scored the next trading night (my call: no reordering of the
nightly steps for a one-day lag).

**The window's range:** from the grid point at or before the alert to the last
grid point within `horizon_min` (30) after it: (highest spot - lowest spot) /
starting spot. An alert with less than a full horizon left before collection
stops is dropped (counted, not scored).

**Usual range:** for the same symbol and the same 5-minute slot of the day, the
median of that window's range over the `history_sessions` (20) archived sessions
**before** this one. Fewer than `min_history` (10) earlier sessions: not scored.
This is what makes it walk-forward: nothing on or after the scored day goes into
its yardstick.

**Label:** 1 when the window ran wider than its usual range, else 0.

**Comparison:** for each scored alert, the base is every other window of that
symbol on that day whose start is not inside any alert's horizon, labelled the
same way. Per day, an alert type's hit rate and its base rate, the days weighted
equally (each day's alerts split its weight). The difference comes from
`shared.ml.metrics.day_bootstrap_diff` with the loss taken as `-label`, so a
negative "loss difference" is a positive edge; the page shows it the reader's way
round.

**Verdict:** the Model Health rule already used by the range forecast and the
exit chance: no data, then too few days (under `min_days`, 40), then beats /
does not beat / matches on the interval.

**Columns on the page:** alert type (whole words), alerts scored, days, wider
after the alert, wider at other times, difference (with its interval), verdict.
Plus a note when alerts were dropped for lack of a full horizon or history.

**Config:** `config/model_health.toml [alerts]`: `enabled`, `types`
(default unusual activity, big delta, HIRO surge, HIRO flip, crossover; gamma flip
left out, see Anti-goals), `horizon_min = 30`, `history_sessions = 20`,
`min_history = 10`, `min_days = 40`, each with a `webgui/config_schema.py` entry.

**First reading, expected:** the archive holds 19 sessions, so after 10 spent as
history about 9 days can be scored for unusual activity and big delta (from the
seed); every type reads "too few days". The record has to run about 50 more
sessions (late January 2027) before unusual activity and big delta can earn a
verdict, longer for the types with no seed.

## Constraints

- Tier 2 and `shared/ml` only; the page reads the existing Model Health view.
- No Schwab or proxy call; reads `gex_archive.db` read-only.
- Every tunable in config with a Settings entry; whole words on the page;
  Tailwind and the page kit; the manuals and `page_help.py` in the same change.
- No backfill guesses: an alert type or day without data is absent, never zero.

## Left to the builder

- The exact `detail_json` keys kept (all but `text` is the rule).
- How the scorer indexes the archive (one read per symbol per session is enough).
- Where the seed tool lives in the nightly flow: it is run once by hand.
