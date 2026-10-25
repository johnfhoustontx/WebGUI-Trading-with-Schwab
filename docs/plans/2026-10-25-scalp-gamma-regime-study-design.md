# Opening Scalp: a declared net-gamma regime study — design

**Date:** 2026-10-25 · **Status:** draft for the operator's approval
**Builds on:** [the 15-minute calls](2026-10-20-scalp-intraday-calls-design.md) and
their [first verdict](../reference/scalp-intraday-study.md),
[the weighted chance](2026-10-22-scalp-weighted-probability-design.md), and the gravity
well ([contours and the well](2026-10-10-gamma-contours-and-well-design.md)).

## 1. Intent

Both scalp studies found that the call's DIRECTION is a coin flip, and the fitted
chance does not beat the usual rate. But a scalp's target (+50% before −50% within
the hour) is mostly a bet that price MOVES, and that is what the gravity well draws:
positive net gamma is a valley, where dealer hedging damps moves and holds price;
negative net gamma is a hill, where hedging amplifies them.

An exploratory look on 2026-10-25 (§2) found that the 15-minute calls made while the
symbol's net GEX was **negative** reached the long-option target far more often in
the **afternoon**, and timed out half as often. That look was not declared: about
five splits were tried before this one stood out, and the studies' own rule is that a
split found after reading the data is a new study, declared and then run as one.

The operator asked (2026-10-25) for exactly that: **a declared study of the net-GEX
sign, by block, on the 15-minute calls, tested on sessions after 2026-10-20**, and,
**if it passes, an unsigned regime factor in the chance model.**

The chance model cannot see this effect today. Every factor in it is multiplied by
the call's direction, and its one gamma factor, `flip_side`
(`scalp_prob_features._dealer`), asks only whether spot sits on the call's side of
the flip. A factor that says "this tape is pinned" or "this tape moves" has no
direction, so it needs a slot the model does not have.

## 2. What was seen in-sample (the reason for the study, not evidence for it)

Read on the E:-drive backup `prod_2026-10-23_2001` (`scalp.db`, opened immutable):
`prob_sources` (the backfilled dealer readings at each call) joined to
`intraday_outcomes`, sessions **2026-08-17 to 2026-10-20** (33 sessions, Oct 19
untrusted readings dropped). Each symbol-day counts once. Long options, with and
against the move pooled.

| Block | Net GEX negative | Net GEX positive | Difference (x1) | Whole-day permutation p |
|---|---|---|---|---|
| Afternoon | 41.5% win, 9.0% timeout* | 29.6% win, 18.1% timeout* | **+13.3 pp** | **0.009** |
| Midday | | | +4.2 pp | 0.11 |
| Morning | | | +1.9 pp | 0.36 |

The difference column is the §3.3 statistic (each symbol-day once). *The win and
timeout shares beside it pool the three symbols by day, where the difference is
+11.9 pp.

- **Afternoon: present at every multiplier** (+9.8, +11.9, +9.1, +8.4 pp at x0.8, x1,
  x1.25, x1.5) **and on every symbol** ($SPX +14.9, SPY +10.5, QQQ +14.7 at x1).
- **The permutation** moves each day's whole sequence of signs onto another day, all
  three symbols together, because the three share one tape. (A first, per-symbol
  shuffle gave p ≈ 0.001; that understated the dependence between symbols and is not
  the number to quote.)
- **The sign rarely changes inside a block**: 11 of 96 afternoon symbol-days. In
  practice the sign is a fact about the afternoon, not about each slot.
- **Weaker or empty splits, also tried:** spot above or below the flip (+2.5 pp, all
  blocks), the distance to the nearest wall, the distance to the flip. Credit spreads
  were a little higher on negative days too (+5.8 pp afternoon), mostly from fewer
  timeouts.

**Why it would be real:** a positive-gamma afternoon is a pinned tape. 0DTE gamma is
largest late in the session, so the effect should be largest in the afternoon and
small in the morning, and that is the order found. **Why it might not be:** it was
found by looking, the effect a search finds is inflated, it is one regime
(August–October 2026), and the premiums are modelled (Black-Scholes at $VIX / $VXN),
not filled.

## 3. The declared study

Everything in this section is fixed before any outcome from the test window is read.
Changing any of it after reading the result is a new study, declared and run as one.

### 3.1 The test window and its data

- **Sessions from 2026-10-21 onward.** No outcome from 2026-10-21 or later has been
  read for this question. (The exploratory scripts joined outcomes to `prob_sources`,
  which ends 2026-10-20. Two live records, 10-22 08:36 $SPX and 10-22 12:36 QQQ,
  were opened to learn the record's shape; no outcome of either was read.)
- **The regime reading is the live record only**, from `intraday_readings`:
  `snapshot.matrix[<symbol>]` of the call. It is a **gap** when the reading is
  `walls_untrusted`, `net_gex` is 0, missing or not finite, or
  `snapshot.sources.matrix.state` is not `"ok"` or its `age_min` is above
  `[confirm] max_age_min`. Negative net GEX is the **negative** regime, positive the
  **positive** one. `prob_sources` is never read for a test-window session, even if
  a backfill later writes rows for one.
- **A usable session** is a regular full session (not an `early_close_days` day)
  where at least one of the three symbols has at least one non-gap afternoon reading
  and a graded afternoon long outcome. A day with no usable reading (another
  no-Greeks day like 2026-10-19) is not counted and does not count towards §3.4's
  minimum. Midday and morning, when their turn comes, are tested over the same
  sessions as the afternoon.
- **The outcomes** are `intraday_outcomes` (which exist only for calls that had a
  direction), roles `long_with` and `long_against`,
  win 1, loss or timeout 0, skip left out: the label the study and the fit already
  use.

### 3.2 The precondition: live and backfilled readings must agree

The in-sample effect was measured on BACKFILLED readings (each stored grid
recomputed the way the live matrix computes it, `tools/scalp_prob_backfill.py`),
and the test reads LIVE ones. The two never overlap: backfill stops 2026-10-20 and
live recording began 2026-10-21. Before the test runs, the backfill extract is run
over the gamma history covering at least five test-window sessions (the E:-drive
`prod_2026-10-23_2001` copy holds 10-21 to 10-23; a later backup adds more), and its
net-GEX sign is compared with the live record's, slot by slot, where both are
non-gap. **At least 95% of slots must agree in sign.** This reads gamma only, never
an outcome, so it can run at any time. If it fails, the study stops: the
difference is found and fixed first, and the study is declared again.

### 3.3 The statistic

Per block, per usable symbol-day: the share of that block's long-option outcomes won
at negative-regime slots, and the share won at positive-regime slots. The
**effect** is the mean of the negative shares over the symbol-days that have one,
minus the mean of the positive shares over the symbol-days that have one, in
percentage points. This is the in-sample statistic exactly. If either side has no symbol-day at all
(every reading in the window was one sign), the effect is undefined and the block
fails.

The **p-value** is one-sided (negative regime wins MORE), by permutation: each
permutation moves every day's whole sequence of readings onto another test-window
day, the three symbols together, keeping only the slots both days have a reading
at. 10,000 permutations, a seed fixed in config. The report prints the date the outcomes were
read, since the nightly rebuild could restate an outcome if bars are refetched. The p-value is the share of permutations
whose effect is at least the observed one.

### 3.4 The pass rule

Read **once**, when the 40th usable session has its afternoon graded (2026-12-17
if every session is usable, about when the confirmation word's own 40-session
test falls due). **No interim look:** before then, the report prints the count
of usable sessions and nothing else (§5).

The blocks are tested **in a fixed order, each at 0.05: afternoon, then midday, then
morning. A block is tested only if the one before it passed.** This keeps the chance
of any false pass at 5% while the afternoon, the one hypothesis the in-sample look
supports, keeps its full power.

A block **passes** when all four hold, at the current `iv_scale` (x1) unless stated:

1. p ≤ 0.05;
2. effect ≥ **+5 pp** (a significant 2-point difference is real and useless);
3. effect **above 0 at every multiplier** (x0.8, x1, x1.25, x1.5), the studies'
   usual guard against a result that is only an artefact of the volatility
   assumption;
4. the §3.2 precondition passed.

**Power, measured by resampling the in-sample sessions** (300 runs each): at 40
sessions the afternoon test passes p ≤ 0.05 **93%** of the time if the in-sample
effect is real at full size, and **28%** of the time if it is half that (about
+6.5 pp). So a pass is good evidence; a fail does **not** show there is no effect,
only that it is not as large as it first looked. The report says so in those words.

### 3.5 Reported, never decided

Per symbol; the credit roles; the effect with each of the long roles alone; the
timeout share by regime. These are printed beside the result and decide nothing.

## 4. If a block passes: the unsigned regime factor

Only the blocks that passed get a factor. If none passes, nothing in the chance
model changes, and the result is written up (§6).

- **One factor per passing block**, named `regime:<block>` (e.g.
  `regime:afternoon`): **+1** when the call's own reading is the negative regime,
  **−1** when positive, **0** for a gap or a slot in another block. The same gap
  rule as §3.1, read by `source_factors` from the call's snapshot, so the fit and the
  live call read it the same way.
- **Unsigned, twice over.** It is NOT multiplied by the call's direction `d`, and
  its prior is NOT negated for the trades against the move (`ROLE_SIGN`). Both are
  new: today every factor is signed and every prior is negated by role. A new
  `UNSIGNED_FACTORS` set in `scalp_prob_features` names it, `factors` leaves it
  unsigned, and `scalp_prob_fit.fit` / `_body` skip the `ROLE_SIGN` negation for it.
- **Discrete**: it joins `DISCRETE` and is never rescaled.
- **Prior: 0, with the WIDE spread** (`prior_sd.wide`, 1.0): no assumed push, so the
  history alone decides each trade's weight. Every trade's model gets the factor and
  learns its own weight from 0. A tight prior at an assumed size is not used: the
  only measured size comes from the test window, and centring a prior on it would
  feed the test sessions into the honesty check's scored weeks.
- **The history it learns from** is the backfilled sessions (Aug 17 – Oct 20) and the
  live record from 2026-10-21, as every other dealer factor already does.
- **On the page**: "What moved the chance" lists it as **Dealer gamma
  (afternoon)**, reading "negative: moves run" or "positive: price held", with its
  push in points and its days of history, as the other factors are shown. Nothing
  new in the glance table.
- **For the record, not a gate:** the refit after it is added logs each long trade's
  walk-forward Brier with and without the factor. The page's existing "beats the
  usual rate on unseen days" line is unchanged and decides nothing here.

## 5. What gets built

**Now (phase 1, the study):**

- `services/options_svc/scalp_regime_study.py`, pure (no bus, nothing from
  `compute`): reads `intraday_readings` and `intraday_outcomes` from the store it is
  given, applies the §3.1 gap rule, computes §3.3 and §3.4 per block, and returns
  the result.
- `tools/scalp_regime_report.py`: prints the result as Markdown, like the other two
  study reports. **Before the 40th usable session it prints only the count of usable
  sessions so far, and the date of the first.** No flag prints the effect early.
- `tools/scalp_regime_agree.py` (or a subcommand of `scalp_prob_backfill.py`): the
  §3.2 sign comparison of a backfill extract against the live record.
- `[regime]` in `config/scalp.toml`, every key in `webgui/config_schema.py`:
  `start_day = "2026-10-21"`, `min_sessions = 40`, `alpha = 0.05`,
  `min_effect_pp = 5.0`, `order = ["afternoon", "midday", "morning"]`,
  `permutations = 10000`, `seed`, `min_sign_agreement = 0.95`. The comment says these
  were declared on 2026-10-25 and that changing one after reading the result is a
  new study (the same note `[intraday] min_brier_margin` carries).

**Later, only on a pass (phase 2, the factor):** §4, in `scalp_prob_features`,
`scalp_prob_fit` and `scalp_prob`, plus `[prob.priors]` gaining `regime_<block> = 0.0`
with a comment saying it is unsigned. Phase 2 gets its own plan when the result is
in.

## 6. When the result is in

Whatever it is, it is written up in `docs/reference/scalp-regime-study.md` with the
report printed unedited, as the other two studies are, and linked from
`CLAUDE.md`'s Options section beside them. A fail is published as plainly as a pass.
The Technical Reference and `page_help.py` change only in phase 2.

## 7. Goals, non-goals, anti-goals

**Goals:** a test of the net-GEX regime on sessions nobody has read for it, with its
rule fixed first; the regime in the chance model only if the test supports it.

**Non-goals:** changing the four trades, their rules, the bucket study or its marks;
the 08:36 call (its morning block showed nothing, and its fitted chance is already
withheld from the page); any page change in phase 1; the ridge plot's "positioning
building at a strike", which needs per-strike history the store keeps for only five
sessions (a candidate for a later study once the regime result is in).

**Anti-goals (a failure even if it runs):**
- any outcome from 2026-10-21 or later read before the 40th usable session;
- the in-sample sessions (to 2026-10-20) counted in the test;
- a gap read as either regime;
- the factor multiplied by direction, or its prior negated by role;
- a block that did not pass getting a factor;
- a fail reported as "no effect" rather than "not as large as it first looked".

## 8. Left to the builder

The module and function split; whether the agreement check is its own tool or a
backfill subcommand; the tests' fixtures; reusing `scalp_study`'s Wilson and label
helpers. Within house rules: pure modules, no `compute` import, config through
`scalp_config`, `db_path=None` resolved at call time.

**Check before phase 2:** the branch `claude/options-scalping-app-291aad` holds the
unmerged `[prob] morning_fitted` change (commit 3d613ee) in the same files. Merge or
rebase onto it first. Also confirm whether the fit's `_dealer` already ignores a
matrix whose `sources.matrix.state` is stale; the regime factor must, per §3.1.

## 9. Calls I made that the operator has not ruled on

1. **Long options only decide the test**; the credit roles are reported only.
2. **40 usable sessions, one look**, no interim peek, even though the report could
   show a running number.
3. **Fixed-order testing**, afternoon → midday → morning, each at 0.05, rather than
   splitting 0.05 three ways.
4. **A +5 pp floor** on top of significance.
5. **The 95% sign-agreement precondition** between live and backfilled readings.
6. **The factor's prior is 0 with the wide spread**, not an assumed size.
7. **Every trade's model gets the factor**, credit included, each learning its own
   weight.
8. **The sign alone**, not net GEX's size: size differs across symbols by orders of
   magnitude, and the in-sample terciles by size (all blocks: 42.8 / 41.0 / 36.8%)
   added little to the sign.
