# Opening Scalp weighted chance of target — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Opening Scalp call (08:36 and each 15-minute slot) carries a fitted
probability per trade of reaching its target, weighted from the price inputs, the
moving-average slope and momentum on 5-minute / 15-minute / hourly bars, the dealer
read, net premium, flow and sentiment; the weights are refitted every Saturday and
the page shows the chance in place of the confirmation word.

**Architecture:** Four new `options_svc` modules (`scalp_trend` → `scalp_prob_features`
→ `scalp_prob_fit` → `scalp_prob`), two new `scalp.db` tables, a one-time history
loader in `tools/`, a weekly scheduler branch plus a command, and the page change.
The call builders compute the chance from the newest stored model and freeze it in
the call; nothing imports `compute`.

**Tech Stack:** Python 3.11, numpy (already a service dependency), sqlite3, NiceGUI
page kit, pytest with the fake bus.

**Spec:** [`docs/plans/2026-10-22-scalp-weighted-probability-design.md`](2026-10-22-scalp-weighted-probability-design.md)
(read it first; section numbers below refer to it).

## Global Constraints

- Never alter an existing `scalp.db` table; add `prob_sources` and `prob_models` only.
- No new module imports `services.options_svc.compute` (the walls come from `gamma_tool.get_directional_walls` directly).
- Nothing at or after a call's `entry_ts` is read, in the fit or the live call.
- A missing reading is `None` in the raw factors and `0.0` after scaling; never disagreement.
- Scaling divides by the interquartile range and caps at ±3; it **never centres** (0 stays neutral).
- Priors are written for the trades WITH the move; `long_against` and `credit_against` use them negated.
- Prior spreads: `wide = 1.0` for price, slope, momentum and time of day; `tight = 0.10` for dealer, premium, flow, sentiment.
- Each symbol-day counts once: a row's fit weight is `1 / (that symbol's rows that day)`.
- The 08:36 model learns from `outcomes` rows with `interval = 5` at `scalp_config.iv_scale()`; the intraday ones from `intraday_outcomes` at the same scale. Labels: win 1, loss/timeout 0, skip left out.
- Every tunable lives in `config/scalp.toml [prob]` (or `config/sessions.toml [slots.scalp_prob]`) with a `webgui/config_schema.py` entry.
- Percentages on the page print two decimals through `pages/fmt.py`; page styling is Tailwind through the page kit.
- Test commands (repo root, the shared venv):
  `PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`; service tests `"$PY" -m pytest services/options_svc/tests/<file> -q`; page tests `cd webgui && "$PY" -m pytest tests/<file> -q`; shared tests `"$PY" -m pytest shared/tests/<file> -q`.
- Never weaken an existing test to make a new one pass; a test that pins the old behaviour this plan replaces (the confirmation word on the page) is changed, with the reason in the commit.

## Review Focus

1. **A bar or source row at or after the entry leaks in.** An hourly or 15-minute bar that is still open at the entry, or a gamma row stamped after `entry_ts`, must not be read. Tests in Task 3 (incomplete bar dropped) and Task 8 (row after entry ignored).
2. **Untrusted walls (net GEX 0 or missing, e.g. Oct 19) read as "no room".** They must be a gap. Test in Task 4.
3. **A call with no source history at all** (every dealer/premium/flow/sentiment factor `None`) must still get a finite chance from the other factors, never NaN or an exception. Test in Task 6.
4. **A refit after a call is published changes the call.** A reused (frozen) entry keeps its chance and model version. Test in Task 7.
5. **No model stored yet** (first deploy, or every refit failed): the call publishes with `chance = None` and the page shows the dash and "not fitted yet". Tests in Tasks 7 and 9.

---

### Task 1: Configuration

**Files:**
- Modify: `config/scalp.toml` (new `[prob]` and `[prob.priors]`), `shared/scalp_config.py` (DEFAULTS + accessors), `config/sessions.toml` (new `[slots.scalp_prob]`), `webgui/config_schema.py` (entries)
- Test: `shared/tests/test_scalp_config.py` (add), `webgui/tests/test_config_schema.py` (existing guard must pass)

**Interfaces — Produces** (all in `shared/scalp_config.py`, each returns the default on a bad value, never raises):
- `prob_ema_period() -> int` (20), `prob_slope_bars() -> int` (3), `prob_rsi_period() -> int` (14)
- `prob_timeframes() -> list[int]` ([5, 15, 60]; each a multiple of 5, ascending)
- `prob_lookback_sessions() -> int` (10; sessions of bars read before the entry for EMA/RSI warm-up)
- `prob_cap() -> float` (3.0; the scaled cap), `prob_room_cap() -> float` (3.0; room ÷ target points cap), `prob_flow_cap() -> int` (3)
- `prob_priors() -> dict[str, float]` — keys exactly: `slope_5` 0.15, `slope_15` 0.10, `slope_60` 0.05, `rsi_5` 0.10, `rsi_15` 0.07, `rsi_60` 0.04, `room` 0.15, `breadth_room` 0.05, `flip_side` 0.0, `netprem` 0.10, `flow` 0.10, `sentiment` 0.05; any other factor's prior is 0.0
- `prob_prior_sd() -> dict[str, float]` (`{"wide": 1.0, "tight": 0.10}`; both > 0)
- `prob_min_train_weeks() -> int` (8), `prob_min_brier_margin() -> float` (0.005; 0 ≤ x < 0.25)
- `prob_refit_weekday() -> int` (5 = Saturday; 0..6)
- `prob_show() -> bool` (True)
- `[slots.scalp_prob]`: `grace_min = 180`, `refit = "08:00"` (read through `market_calendar.slot_times("scalp_prob")`)

- [ ] **Step 1: Write the failing tests** — `test_prob_defaults` (each accessor returns the value above with no file), `test_prob_bad_values_read_as_defaults` (e.g. `prob_timeframes` given `[7, 15]` → `[5, 15, 60]`; `prob_prior_sd` given `{"wide": 0}` → defaults; `prob_refit_weekday` given 9 → 5), `test_prob_priors_unknown_key_kept_out` (a TOML key `foo = 1` does not appear).
- [ ] **Step 2: Run** `"$PY" -m pytest shared/tests/test_scalp_config.py -q` → FAIL (attribute errors).
- [ ] **Step 3: Implement** the DEFAULTS `"prob"` section and accessors in `shared/scalp_config.py` (follow `confirm_weights` for the dict ones); add `[prob]` / `[prob.priors]` with comments to `config/scalp.toml`; add `[slots.scalp_prob]` to `config/sessions.toml` with a comment saying it fires on `[prob] refit_weekday`, not on trading days; add one `Field` per key to `webgui/config_schema.py` beside the existing scalp entries.
- [ ] **Step 4: Run** the shared test and `cd webgui && "$PY" -m pytest tests/test_config_schema.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(scalp): [prob] configuration for the weighted chance`.

### Task 2: Store tables

**Files:**
- Modify: `services/options_svc/scalp_store.py`
- Test: `services/options_svc/tests/test_scalp_store.py`

**Interfaces — Produces:**
- Table `prob_sources (kind TEXT, day TEXT, slot TEXT, symbol TEXT, json TEXT, PRIMARY KEY (kind, day, slot, symbol))`; `kind` is `"morning"` or `"intraday"`; `json` is a snapshot in **`scalp_confirm.snapshot`'s shape** (`matrix`, `netprem`, `sentiment`, `flow` with `flow.window`).
- Table `prob_models (version INTEGER, kind TEXT, role TEXT, fitted_at TEXT, json TEXT, PRIMARY KEY (version, kind, role))`.
- `save_prob_sources(rows: list[tuple[str, str, str, str, dict]], db_path=None) -> int` — upsert; returns rows written.
- `prob_sources_all(kind: str, db_path=None) -> dict[tuple[str, str, str], dict]` — `{(day, slot, symbol): body}`.
- `readings_all(db_path=None) -> dict[tuple[str, str, str], dict]` — every `intraday_readings` row as `{(day, slot, symbol): body}` (the live record of all 22 calls a day; slot `"08:36"` is the morning call).
- `save_prob_models(version: int, fitted_at: str, models: dict[tuple[str, str], dict], db_path=None) -> None` — one transaction.
- `latest_prob_models(db_path=None) -> dict` — `{"version": int, "fitted_at": str, "models": {(kind, role): body}}` of the highest version, or `{}`.
- `next_prob_version(db_path=None) -> int` — max + 1, 1 when empty.

- [ ] **Step 1: Write the failing tests** — `test_prob_tables_are_new_and_old_tables_untouched` (the pre-existing `CREATE TABLE` statements are byte-identical to before; assert by comparing `sqlite_master.sql` for `bars`, `outcomes`, `intraday_readings` to their current text), `test_prob_sources_round_trip`, `test_save_prob_sources_replaces_same_key`, `test_latest_prob_models_is_highest_version_and_keeps_older`, `test_latest_prob_models_empty_is_empty_dict`.
- [ ] **Step 2: Run** `"$PY" -m pytest services/options_svc/tests/test_scalp_store.py -q` → FAIL.
- [ ] **Step 3: Implement** — append the two `CREATE TABLE IF NOT EXISTS` statements to `_SCHEMA`; functions as above (JSON with `sort_keys=True`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(scalp): prob_sources and prob_models tables`.

### Task 3: Trend factors (moving-average slope, momentum)

**Files:**
- Create: `services/options_svc/scalp_trend.py`
- Test: `services/options_svc/tests/test_scalp_trend.py`

**Interfaces:**
- Consumes: `scalp_store.bars(symbol, 5, start_ts, end_ts, db_path)` (bars carry `ts` = start, `open/high/low/close`), `market_calendar.regular_open_on(day)`, Task 1 accessors.
- Produces:
  - `resample(bars5: list, minutes: int, open_ts_of_day: Callable[[date], int], close_ts_of_day: Callable[[date], int], before_ts: int) -> list[tuple[int, float]]` — `(start_ts, close)` of COMPLETE regular-session bars of `minutes`, aligned to each day's 08:30 open, whose END is ≤ `before_ts`; the last bar of a day may be short (hourly 14:30–15:00) and counts once that day's session has closed. A bar missing any of its 5-minute parts is dropped.
  - `ema_slope_pct(closes: list[float], period: int, slope_bars: int) -> float | None` — EMA seeded with the SMA of the first `period` closes; returns `(ema[-1] / ema[-1 - slope_bars] - 1) * 100`; `None` with fewer than `period + slope_bars` closes.
  - `rsi(closes: list[float], period: int) -> float | None` — Wilder's RSI; `None` with fewer than `period + 1` closes; 100 when there are no losses, 50 when no change at all.
  - `trend_factors(symbol: str, day: date, entry_ts: int, db_path=None, cache: dict | None = None) -> dict[str, float | None]` — keys `slope_5, slope_15, slope_60, rsi_5, rsi_15, rsi_60` (names follow `prob_timeframes()`), RAW and unsigned (RSI as 0..100). Reads `prob_lookback_sessions()` regular sessions of 5-minute bars ending before `entry_ts`. `cache` follows `scalp_intraday_inputs`' rule: a completed day's bars only.

- [ ] **Step 1: Write the failing tests:**
  - `test_ema_slope_on_a_straight_line` — closes `[100 + i for i in range(40)]`, period 20, slope 3: result equals `(e[-1]/e[-4]-1)*100` computed by hand in the test (independent loop), to 1e-9.
  - `test_rsi_known_values` — the textbook Wilder example (14-period, the 15 closes `44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28`) → `rsi == pytest.approx(70.53, abs=0.01)`; all-up series → 100; flat → 50.
  - `test_resample_drops_the_bar_still_open_at_entry` — 5-minute bars 08:30…09:40, `before_ts` = 09:50 entry: the 15-minute list ends with the 09:30 bar (09:30–09:45), not 09:45; hourly ends with 08:30–09:30.
  - `test_resample_skips_a_bar_with_a_missing_part`.
  - `test_trend_factors_never_reads_at_or_after_entry` — a store whose bar at `entry_ts` has an absurd close (×10): results equal those of a store without it.
  - `test_hourly_runs_on_from_previous_sessions` — at 08:51 `slope_60` is not None when 10 prior sessions exist; None with none.
- [ ] **Step 2: Run** `"$PY" -m pytest services/options_svc/tests/test_scalp_trend.py -q` → FAIL.
- [ ] **Step 3: Implement** the four functions; regular-session bounds from `market_calendar` (respect half days through `regular_close_on` if it exists there, else 15:00).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(scalp): moving-average slope and RSI on 5-minute, 15-minute and hourly bars`.

### Task 4: Factor assembly and scaling

**Files:**
- Create: `services/options_svc/scalp_prob_features.py`
- Test: `services/options_svc/tests/test_scalp_prob_features.py`

**Interfaces:**
- Consumes: Task 3 `trend_factors`; `scalp_confirm` pure helpers on a snapshot (`_wall_view`, `_room`, `flow_sense` as already used by `_flow_scope`; read the snapshot's `matrix`, `netprem`, `flow.window`, `sentiment` fields directly); `scalp_confirm._STATE_SENSE`.
- Produces:
  - `ROLE_SIGN = {"long_with": 1, "credit_with": 1, "long_against": -1, "credit_against": -1}`
  - `WIDE = frozenset` of factor-name prefixes with the wide prior: `"price:"`, `"slope_"`, `"rsi_"`, `"block:"`; everything else tight.
  - `DISCRETE = frozenset({"flip_side", "sentiment", "flow", "block:midday", "block:afternoon"})` — never rescaled.
  - `price_factors(inputs: dict, d: int) -> dict[str, float | None]` — each existing input as `price:<key>`: a percent move × `d`; `vol_change` × `-d`; `breadth_up_share` → `(share - 0.5) * 2 * d`; `fut_range_pos` / `open_close_loc` → `(pos - 0.5) * 2 * d`; `open_broke_pm` × `d`; `open_range_ratio` → raw (unsigned; information only); `None` stays `None`.
  - `trend_signed(trend: dict, d: int) -> dict` — `slope_*` × `d`; `rsi_*` → `(rsi - 50) / 50 * d`.
  - `source_factors(snapshot: dict | None, symbol: str, d: int, win_points: float | None, entry_spot: float | None) -> dict[str, float | None]` — keys `room` (room ÷ |win_points|, capped at `prob_room_cap()`, can be negative; `None` for a missing or **untrusted** wall or no win points), `breadth_room` (share of big ten with room minus 0.5, `None` under `breadth_min_members`), `flip_side` (+1 when the entry spot is above the flip for an up call or below for a down call, −1 the other way, `None` without a flip), `netprem` ((call − put)/(call + put) × `d`, `None` without a level or a zero total), `flow` ((bull − bear) × `d`, capped ±`prob_flow_cap()` then ÷ cap; `None` when the window is absent or another day's), `sentiment` (`_STATE_SENSE[state] * d`, `None` for no state or a state with no sense).
  - `factors(kind: str, symbol: str, slot: str, d: int, inputs: dict, trend: dict, snapshot: dict | None, win_points: float | None, entry_spot: float | None) -> dict[str, float | None]` — the union; intraday adds `block:midday` / `block:afternoon` as 1.0/0.0 from `scalp_config.intraday_block(slot)`.
  - `fit_scaling(rows: list[dict[str, float | None]]) -> dict[str, float]` — per non-discrete factor, the interquartile range of its non-None values (1.0 when the range is 0 or fewer than 4 values).
  - `scaled(f: dict[str, float | None], scaling: dict[str, float]) -> dict[str, float]` — `None` → 0.0; discrete as-is; others `f / scaling[name]` clipped to ±`prob_cap()`; a factor not in `scaling` → 0.0.

- [ ] **Step 1: Write the failing tests:**
  - `test_untrusted_wall_is_a_gap_not_no_room` — snapshot matrix row with `net_gex` 0 and a call wall below spot → `room is None`.
  - `test_room_ratio_capped_and_signed` — wall 30 points away, target 10 → `room == 3.0` (cap); spot beyond the wall → negative.
  - `test_against_roles_flip_sign` — `ROLE_SIGN["long_against"] == -1`.
  - `test_scaled_never_centres` — values `[1, 2, 3, 4]` scaled: a 0.0 input stays 0.0; `None` → 0.0.
  - `test_discrete_factors_not_rescaled` — `flow` 1.0 stays 1.0 whatever `scaling` says.
  - `test_netprem_sign_follows_direction` — call 3, put 1 → 0.5 for `d=1`, −0.5 for `d=-1`.
  - `test_flow_other_day_is_a_gap`.
  - `test_price_inputs_signed_by_direction` — `since_open` 0.2 with `d=-1` → −0.2; `vol_change` 1.0 with `d=1` → −1.0.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(scalp): the chance's factors, signed by the call and scaled without centring`.

### Task 5: The fit and the weekly honesty check

**Files:**
- Create: `services/options_svc/scalp_prob_fit.py`
- Test: `services/options_svc/tests/test_scalp_prob_fit.py`

**Interfaces:**
- Consumes: Tasks 2–4; `scalp_inputs.morning`, `scalp_intraday_inputs.reading` (with the per-day cache, as `scalp_intraday_study.rebuild` does); `scalp_store` outcome tables; `scalp_study._short`, `scalp_config.intraday_slot_cut`.
- Produces:
  - `@dataclass Row: kind: str, day: str, slot: str, symbol: str, role: str, win: int, weight: float, factors: dict[str, float | None]`
  - `training_rows(kind: str, db_path=None) -> list[Row]` — one row per labelled outcome at `iv_scale()` (morning: `interval = 5`); factors from Task 4 with the snapshot taken from `readings_all()` first, else `prob_sources_all(kind)`, else `None` (all source factors gaps); `win_points` from the call-day's simulated long-with level (`scalp_sim.level_points` at the row's entry spot and vol, as `scalp_call._price` does) — `None` when unpriceable; `weight = 1 / rows of that (day, symbol, role)`. Skips what the studies skip (short session, early-close cut, no direction).
  - `@dataclass Model: role: str, kind: str, weights: dict[str, float], intercepts: dict[str, float], scaling: dict[str, float], days: dict[str, int], n_days: int`
  - `fit(rows: list[Row], role: str, kind: str, priors: dict[str, float], prior_sd: dict[str, float]) -> Model` — MAP logistic regression: minimise weighted log-loss + Σ (w − sign·prior)² / (2·sd²) with `sign = ROLE_SIGN[role]`, `sd` wide/tight per `WIDE`; one intercept per symbol with prior 0 and the wide spread. Newton steps (numpy) until the step is < 1e-8 or 50 iterations; scaling from `fit_scaling` over the rows used. `days[name]` = distinct days where that factor was not None.
  - `predict(model: Model, symbol: str, factors: dict) -> tuple[float, dict[str, float]]` — probability and each factor's push in log-odds (`weight × scaled value`).
  - `walk_forward(rows: list[Row], role: str, kind: str, priors, prior_sd) -> dict` — ISO weeks sorted; for each week after the first `prob_min_train_weeks()`, fit on earlier weeks, predict that week; also the usual rate per row as the share of wins among EARLIER days' rows for the same (symbol, role) — and, for `kind == "intraday"`, the same block — weighted the same way. Returns `{"weeks": n, "brier": b, "brier_usual": bu, "beats_usual": bool(bu - b >= prob_min_brier_margin()), "calibration": [[lo, hi, mean_p, rate, n], ...tenths]}`; with no scored week `beats_usual` is False and the Brier fields None.
  - `refit(db_path=None, now=None) -> dict` — builds rows per kind, for each of the four roles `fit` + `walk_forward`, stores all eight under `next_prob_version()` with `fitted_at = now` (CT ISO); returns `{"version", "rows": {kind: n}, "seconds"}`. A role with no rows is stored with empty weights and `n_days = 0`. Never deletes older versions. Wrapped: a failure is `_degrade.degraded("options.scalp_prob")` and stores nothing.

- [ ] **Step 1: Write the failing tests:**
  - `test_fit_recovers_a_planted_weight` — synthetic rows: factor `slope_5` ~ U(-1, 1) on 200 days × 1 symbol, P(win) = sigmoid(1.2·x); wide prior → fitted weight within 0.25 of 1.2.
  - `test_tight_prior_holds_a_thin_source` — `netprem` present on 5 days only, planted weight 2.0 → fitted within 0.05 of its prior 0.10.
  - `test_against_role_prior_is_negated` — no data for `room`: `fit(..., role="long_against")` gives `room ≈ -0.15`.
  - `test_each_symbol_day_counts_once` — 21 identical rows of one day weigh the same as 1 row of that day (fitted weights equal to 1e-6).
  - `test_walk_forward_never_scores_a_fitted_week` — monkeypatch `fit` to record the max day it was given; for every scored week that max is before the week's first day.
  - `test_walk_forward_needs_min_train_weeks` — 5 weeks of rows → `weeks == 0`, `beats_usual is False`.
  - `test_refit_stores_eight_models_under_one_version_and_keeps_old` (fake store in `tmp_path`).
  - `test_training_rows_skip_what_the_study_skips` — a short-session day yields no rows.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(scalp): fit the chance's weights with priors, walk-forward by week`.

### Task 6: Live chance

**Files:**
- Create: `services/options_svc/scalp_prob.py`
- Test: `services/options_svc/tests/test_scalp_prob.py`

**Interfaces:**
- Consumes: `scalp_store.latest_prob_models`, Tasks 3–5.
- Produces:
  - `chance(kind: str, symbol: str, entry: dict, snapshot: dict | None, day: date, db_path=None, models: dict | None = None, trend_cache: dict | None = None) -> dict` — `entry` is a built call (`direction`, `inputs`, `entry_ts`, `entry_spot`, `slot`, `roles` with `win_points`). Returns
    `{"version": int | None, "fitted_at": str | None, "by_role": {role: float | None}, "beats_usual": {role: bool}, "pushes": [{"factor", "reading", "points", "days", "assumed"}]}`
    where `pushes` are for `long_with`, `points` the push converted to percentage points of chance at the predicted probability (`p·(1−p)·push·100`), `assumed` = `days < 5` (the factor's weight is still mostly the prior). With no direction or no models: `by_role` all `None`, `version` None. Never raises (degrade `options.scalp_prob`).
- [ ] **Step 1: Write the failing tests** — `test_no_models_gives_none_not_a_number`, `test_no_source_history_still_finite` (snapshot `None`, every source factor gap → each `by_role` value finite in (0, 1)), `test_flat_tape_has_no_chance`, `test_pushes_name_assumed_factors`, `test_against_and_with_move_opposite_ways` (a strongly agreeing factor raises `long_with` and lowers `long_against`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(scalp): the live chance of target from the newest weights`.

### Task 7: Wire the chance into both calls and the view contract

**Files:**
- Modify: `services/options_svc/scalp_call.py` (`build`/`_symbol_call`), `services/options_svc/scalp_intraday_call.py` (`_symbol_call`, `_cell`), `shared/contracts/options.py` (`_RATE_KEYS` += `"chance"`; `ScalpView` checks `entry["chance"]["by_role"]` values as unit numbers)
- Test: `services/options_svc/tests/test_scalp_call.py`, `services/options_svc/tests/test_scalp_intraday_call.py`, `shared/tests/test_contracts*.py` (whichever holds the `ScalpView` tests)

**Interfaces:**
- Consumes: Task 6 `chance`; `scalp_confirm.sources(bus, now)`, `scalp_confirm.snapshot(src, symbols, entry_ts=, symbol=)`.
- Produces (payload shape the page reads):
  - every call entry gains `entry["chance"]` = Task 6's dict (or `None` for an entry with no roles);
  - every role gains `role["chance"]` = `by_role[role]` (float 0..1 or None);
  - the timeline cell gains `"chance"`: the named trade's chance (highlight, else `long_with`).
- Rules: the 08:36 `build` reads `scalp_confirm.sources` ONCE before the symbols and passes the snapshot of each symbol; the intraday `_symbol_call` uses the `src` it already has. The chance is computed AFTER `_verdict` and changes nothing else. A stored (frozen) entry is reused whole, chance included. With `scalp_config.prob_show()` False no chance is computed and `entry["chance"]` is absent (the page then hides the column; Tier 1 never reads the service's config). "Absent" (switched off) and `{"version": None, …}` (not fitted yet) are different and the page tells them apart.
- [ ] **Step 1: Write the failing tests** — `test_0836_call_carries_a_chance_per_role` (seed `prob_models` in a temp store; built entry has `chance.version` and four `role["chance"]` in (0,1)), `test_intraday_call_carries_a_chance`, `test_refit_does_not_change_a_frozen_call` (build, save, store a new model version, build again with `stored` → identical `chance`), `test_no_model_publishes_with_none` (view still validates), `test_scalpview_rejects_a_chance_above_one`.
- [ ] **Step 2: Run** the three test files → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS, and the whole `services/options_svc/tests/test_scalp_*.py` set → PASS.
- [ ] **Step 5: Commit** `feat(scalp): every call carries its chance of target, frozen with the call`.

### Task 8: History loader

**Files:**
- Create: `tools/scalp_prob_backfill.py`
- Test: `tests/test_scalp_prob_backfill.py` (repo-root `tests/`; uses `tmp_path` SQLite files built by the test, never a real store)

**Interfaces:**
- Consumes: `gex_history_db._decode_grid`, `gex_history_db.fetched_only_clause` (options-scanner on `sys.path`), `gamma_tool.get_directional_walls`, `scalp_config.intraday_slots/intraday_entry_ts/symbols`, `shared.symbols.big10`, `scalp_confirm.flow_sense`, Task 2 `save_prob_sources`.
- Produces: CLI with two subcommands.
  - `extract --gamma PATH [PATH ...] [--flow-db PATH] --out FILE.jsonl` — for every session day in each gamma copy (read-only, `mode=ro&immutable=1`), every call time (`"08:36"` with entry 08:35, and each intraday slot's `intraday_entry_ts`), every scalp symbol: a snapshot in `scalp_confirm.snapshot`'s shape built from the newest **fetched** (not carried) `view='gex'` row with `ts ≤ entry_ts` and `ts ≥ entry_ts - [confirm] max_age_min*60` per symbol and big-ten member: `spot`, `call_wall`/`put_wall` (`get_directional_walls` over the decoded grid, split by spot), `flip`, `net_gex = net_total`, `walls_untrusted` (net_total None or 0); `netprem` from that row's `call_prem`/`put_prem`. With `--flow-db`, `flow.window` from `flow_contract_days` alerts of that session with `fired_ts` in `[entry_ts - flow_window_min*60, entry_ts]` on the symbol or the big ten, sense by `flow_sense`. Sentiment is not extracted (live records only — design §4: assumed a gap before them). One JSON object per line: `{"kind", "day", "slot", "symbol", "snapshot"}`.
  - `load --in FILE.jsonl [FILE ...] --db PATH` — merges per key (a later file's non-null `matrix`/`netprem`/`flow` fill an earlier one's nulls; never overwrite a non-null) and calls `save_prob_sources`. Never writes a key that already exists in `intraday_readings` (the live record wins).
- [ ] **Step 1: Write the failing tests** — `test_extract_ignores_a_row_after_entry` (a gex row at `entry_ts + 60` with a different wall is not used), `test_extract_marks_zero_net_gex_untrusted`, `test_extract_skips_carried_rows`, `test_load_merges_without_overwriting`, `test_load_never_shadows_a_live_reading`.
- [ ] **Step 2: Run** `"$PY" -m pytest tests/test_scalp_prob_backfill.py -q` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(scalp): history loader for the chance's dealer, premium and flow sources`.

### Task 9: Weekly refit branch and the command

**Files:**
- Modify: `services/options_svc/scheduler.py` (a `scalp_prob_due(now, ran_slots)` and a branch), `services/options_svc/handlers.py` (`_cmd_scalp_prob_refit`, `SLOW_LANE`, `_REPLAY_GUARDED` reason, the docstring's command list)
- Test: `services/options_svc/tests/test_scheduler*.py` (whichever holds `scalp_bars_due` tests), `services/options_svc/tests/test_handlers*.py`

**Interfaces:**
- `scalp_prob_due(now, ran_slots) -> str | None` — fires on `now.weekday() == prob_refit_weekday()` (NOT gated on a trading day) when `target <= now < target + grace` and `(date, slot)` is not in `ran_slots`.
- The branch runs `scalp_prob_fit.refit` in the executor, logs `version` and row counts, latched before it runs.
- Command `{"type": "scalp_prob_refit"}` runs the same refit; slow lane; replay-guarded.
- [ ] **Step 1: Write the failing tests** — `test_scalp_prob_due_only_on_the_refit_weekday` (Saturday 08:05 → due; Friday 08:05 → None; Saturday 11:30 with grace 180 → due; 11:01 + already ran → None), `test_scalp_prob_refit_command_registered_and_slow`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(scalp): Saturday refit of the chance's weights, and a command for it`.

### Task 10: The page

**Files:**
- Modify: `webgui/pages/options/scalp.py`
- Test: `webgui/tests/test_scalp_page.py`

**Interfaces:**
- Consumes: the payload of Task 7 (`entry["chance"]`, `role["chance"]`, timeline `cell["chance"]`).
- Produces (pure helpers, tested):
  - `chance_text(value) -> str` — `_rate(value)` (two decimals) or the dash.
  - `chance_note(chance: dict | None, role: str) -> str` — `"fitted <Mon D> · beats the usual rate on unseen days: yes|no"`; `"not fitted yet"` with no version.
  - `glance_row` (existing) replaces `word` / `sources` / `_word_class` with `chance`, `chance_note`; the column `word` becomes `chance` labelled **Chance of target** in `glance_columns()` and `_GLANCE_SLOTS`. A row with no roles shows the dash.
  - `role_rows` and `intraday_role_rows` gain `"chance"`; `role_columns()` / `intraday_role_columns()` gain **Chance of target** after **Usual win rate**.
  - `push_rows(chance: dict | None) -> list[dict]` — one row per push: factor label in words (`slope_5` → "Moving-average slope, 5-minute", `rsi_15` → "Momentum (RSI), 15-minute", `room` → "Room to the wall", `netprem` → "Net premium", `flow` → "Options flow", `sentiment` → "Market sentiment", `price:<key>` → the existing input label, `block:*` → "Time of day"), `reading` (two decimals), `points` signed two decimals with "pts", `days`, and **assumed** where `assumed`.
  - `_confirmation_block` is replaced by `_chance_block(entry)`: title "What moved the chance", the note line, `kit.table` of `push_rows`, sorted by |points| descending.
- Rules: when no entry in the view carries a `chance` key (switched off), the Chance of target column, the per-trade column and the "What moved the chance" block are not drawn (`test_switched_off_hides_the_chance`); the confirmation word and "n of 4 sources" no longer appear anywhere on the page; `CONFIRMATION_*` texts that only served them are removed with their tests (say so in the commit); the scorecard's split by word stays.
- [ ] **Step 1: Write the failing tests** — `test_glance_shows_the_chance_not_the_word` (row has `chance == "47.12%"` from 0.4712, no `word` key; `glance_columns()` has a "Chance of target" label and no "Confirmation"), `test_no_model_reads_not_fitted_yet`, `test_flat_row_chance_is_the_dash`, `test_push_rows_in_words_with_assumed_marked`, `test_each_trade_has_its_chance_column`, `test_details_show_what_moved_the_chance` (drawn fixture: "What moved the chance" in texts, no "Confirmation ·").
- [ ] **Step 2: Run** `cd webgui && "$PY" -m pytest tests/test_scalp_page.py -q` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; then the full webgui suite in the background (no edits while it runs) → PASS.
- [ ] **Step 5: Verify in the browser** — `tools/ui_harness.py options.scalp --port <free> --seed <seed.json>` with a seed that has `chance` on every entry (build it from today's live view plus synthetic `chance` dicts); screenshot the glance and one opened symbol section.
- [ ] **Step 6: Commit** `feat(scalp): the page shows the chance of target and what moved it`.

### Task 11: Documentation

**Files:**
- Modify: `CLAUDE.md` (correct in place the scalp bullet "The probability is price only; the confirmation … never in it" — now: the chance is a fitted weighted model; the bucket study's rates stay price only; the confirmation's raw sources are recorded and feed the chance), `docs/manuals/user-guide/user-guide.md`, `docs/manuals/reference-guide/reference-guide.md`, `docs/manuals/technical-reference/technical-reference.md` (the factors, scaling, priors, fit, walk-forward, refit cadence), `docs/manuals/api-reference/api-reference.md` (payload fields, `scalp_prob_refit`, the two tables), `docs/manuals/glossary/glossary.md` (Chance of target, prior, moving-average slope, RSI), `webgui/page_help.py`, `docs/webgui-routes.md`, `docs/CHANGELOG.md`; rebuild the five manuals with `docs/manuals/build_docs.py`.
- [ ] **Step 1:** Edit each file; `tests/test_claude_md_size.py` must still pass.
- [ ] **Step 2: Run** `"$PY" -m pytest tests/test_claude_md_size.py -q` and `cd webgui && "$PY" -m pytest tests -q -k "help or manual"` → PASS.
- [ ] **Step 3: Commit** `docs(scalp): the weighted chance of target`.

## Rollout (after merge and the operator's promote; each step the operator approves)

1. On the Windows box: `"$PY" tools/scalp_prob_backfill.py extract --gamma <the four E: copies> --out e_drive.jsonl`; copy the file to prod.
2. On prod: `extract --gamma ~/backups/prod_*/options-scanner/gex_history.db options-scanner/gex_history.db --flow-db options-scanner/gex_history.db --out vps.jsonl`, then `load --in e_drive.jsonl vps.jsonl --db services/options_svc/data/scalp.db`.
3. Enqueue `{"type": "scalp_prob_refit"}` once; read `prob_models` for version 1 and each model's `n_days`, `days` per factor and walk-forward result; report them to the operator before the next session.
4. Next session: the 08:36 and 08:51 calls carry `chance.version == 1`.
