# Scalp intraday calls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new, frozen 0DTE scalp call every 15 minutes (08:51–13:51 CT) for $SPX, SPY and QQQ, with a walk-forward-tested price core, a recorded (not scored-into-probability) confirmation reading from flow, net premium, the walls and sentiment, and every reading saved from day one.

**Architecture:** New sibling modules in `services/options_svc` (`scalp_confirm`, `scalp_record`, `scalp_intraday_inputs`, `scalp_intraday_study`, `scalp_intraday_call`, `scalp_intraday_grade`), new tables in `scalp.db` keyed by `(day, slot, symbol)`, one scheduler branch on the 15-minute slots, and an `intraday` section in the existing `cache:options:scalp` view. The confirmation reads Redis views only; the price core reuses `scalp_sim`, `scalp_model` and the bar store.

**Tech Stack:** Python 3.11, SQLite, `options_calculator`, `shared.config_toml`, `shared.bus`, pydantic contracts, NiceGUI page kit, pytest.

**Spec:** [docs/plans/2026-10-20-scalp-intraday-calls-design.md](2026-10-20-scalp-intraday-calls-design.md) — read it first. It extends [the Opening Scalp design](2026-10-18-opening-scalp-design.md); the existing code is `services/options_svc/scalp_*.py`, `shared/scalp_config.py`, `webgui/pages/options/scalp.py`.

## Global Constraints

- `$PY` = `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` (Windows) / `/home/administrator/dev/.venv/bin/python` (VPS). Service suites run from the repo root; webgui tests from inside `webgui/`.
- New modules import **nothing from `compute.py`**. A 15+ line swallowing guard calls `_degrade.degraded("options.scalp_<area>")`.
- Every tunable in `config/scalp.toml` through `shared/scalp_config.py` (defaults for a missing/bad value, never raising) and catalogued in `webgui/config_schema.py`. Values (verbatim from the spec): `[intraday] start = "08:51"`, `end = "13:51"`, `interval_min = 15`, `grace_min` 5, `dead_zone_pct` 0.03, `breadth_agree` 0.6, blocks morning 08:51–09:51 / midday 10:06–12:21 / afternoon 12:36–13:51; `[confirm] flow_window_min` 30, `flow_margin` 2, `netprem_margin` (fraction of total, default 0.1 — my call), `max_age_min` 5, weights flow 1.0 / net premium 1.0 / dealer 1.0 / sentiment 0.5, `promotion_sessions` 40, the Confirms/Contradicts cuts (default ±0.34 — my call).
- The big ten is `shared.symbols.big10()` (the existing BIG10 basket) — never a second list.
- **Existing `scalp.db` tables are never altered** (prod holds them): intraday data goes in NEW tables via `CREATE TABLE IF NOT EXISTS`.
- Frozen-call rules carry over: a call is saved only after it validates AND publishes; a stored call is reused whole; no new call after its own entry + time stop; grades are write-once from what was published; highlight only above base, below-base edges in `avoid`; "no measured edge" is the default.
- The statistical unit of the intraday study is the **day** (spec §4): `n` = distinct days, `min_samples` gates on days, Wilson with `n` = days.
- Times are CT via `zoneinfo`; candle `ts` is epoch seconds of the bar START; time to expiry only through `options_calculator.expiry_time_to_years`.
- Never `priority=True` on a proxy call; no proxy change.
- Tests never open a live store (`db_path=None` resolved at call time; `tmp_path` in tests); no network.
- Use the editor for every edit (scripts have collapsed line continuations before); never weaken an existing test; commit per task with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Schwab sends no Greeks** (as on 2026-10-19; main's a944df2 makes walls read as missing): the dealer read and the recorded walls are "no reading", never "disagrees" and never a tie wall. Test in Task 3.
2. **A stale source** (options_svc restarted, the matrix or net-premium view older than `max_age_min`): that component is "no reading" and the record says `stale`. Test in Task 3.
3. **The 08:36 call and an intraday slot, or a Refresh at a slot boundary, at the same moment**: one run at a time (`scalp_call._RUN_LOCK`), one fetch per symbol, saved equals published. Test in Task 8.
4. **A daylight-saving week** (2026-11-01): the 21 slots stay 08:51…13:51 CT on both sides. Test in Task 1.
5. **A shortened session** (a half day closes 12:00 CT): calls published after the real close are never graded as if the session ran to 15:00 — the grading's short-session skip covers them, and the scorecard does not count them. Test in Task 9.

---

## Phase 1 — record from day one (merge and promote as soon as Tasks 1–4 are green)

### Task 1: Config, slots and the big ten in the bar store

**Files:** Modify `config/scalp.toml`, `shared/scalp_config.py`, `webgui/config_schema.py`. Test: `shared/tests/test_scalp_config.py` (extend).

**Interfaces — produces (`shared/scalp_config.py`):**
- `intraday_slots(day: date) -> list[str]` — `"HH:MM"` CT slot names from `start` to `end` every `interval_min`.
- `intraday_slot_time(day: date, slot: str) -> datetime` (aware CT) and `intraday_entry_ts(day, slot) -> int` — the slot minute floored to the last 5-minute boundary (08:51 → 08:50).
- `intraday_block(slot: str) -> str | None` — `"morning" | "midday" | "afternoon"`.
- `intraday_grace_min()`, `intraday_dead_zone_pct()`, `breadth_agree()`, `confirm_weights() -> dict`, `flow_window_min()`, `flow_margin()`, `netprem_margin()`, `confirm_max_age_min()`, `confirm_cuts() -> tuple[float, float]`, `promotion_sessions()`.
- `bar_symbols()` now also contains every `shared.symbols.big10()` member (no duplicates; existing order first).

- [ ] **Step 1: Failing tests** — `test_intraday_slots_are_21_from_0851_to_1351`, `test_entry_ts_floors_to_the_5_minute_boundary` (08:51 → 08:50:00 CT), `test_blocks_cover_every_slot_once`, `test_slots_keep_ct_across_dst` (2026-10-30 and 2026-11-02 both 08:51 local), `test_bar_symbols_include_the_big10`, `test_bad_intraday_values_fall_back`.
- [ ] **Step 2: Run; expect FAIL.** `$PY -m pytest shared/tests/test_scalp_config.py -q`
- [ ] **Step 3: Implement** + the config keys with comments + the Settings catalogue.
- [ ] **Step 4: Run; expect PASS**, plus `cd webgui && $PY -m pytest tests/test_config_schema.py -q`.
- [ ] **Step 5: Commit** `feat(scalp): intraday slots and the big ten in the bar store`

### Task 2: The new tables

**Files:** Modify `services/options_svc/scalp_store.py`. Test: `services/options_svc/tests/test_scalp_store.py` (extend).

**Interfaces — produces:** tables (all `CREATE TABLE IF NOT EXISTS`, primary keys as named):
`intraday_readings(day, slot, symbol, json)` PK (day, slot, symbol);
`intraday_calls(day, slot, symbol, json)` PK (day, slot, symbol);
`intraday_chain_slices(day, slot, symbol, ts, body BLOB)` PK (day, slot, symbol);
`intraday_outcomes(day, slot, symbol, iv_scale, role, structure, block, bucket, result, minutes, entry_value, both_in_bar)` PK (day, slot, symbol, iv_scale, role);
`intraday_verdicts(symbol, block, role, bucket, json)` PK (symbol, block, role, bucket);
`intraday_grades(day, slot, symbol, role, structure, model_result, chain_result, highlighted, avoided, confirm_word, p_base, block)` PK (day, slot, symbol, role).
Functions: `save_reading(day, slot, symbol, body, db_path=None)`, `readings_on(day, db_path=None) -> dict[(slot, symbol), dict]`, `save_intraday_call(...)`, `intraday_calls_on(day, db_path=None) -> dict[(slot, symbol), dict]`, `save_intraday_slice(day, slot, symbol, ts, body, db_path=None)`, `intraday_slice(day, slot, symbol, db_path=None)`.

- [ ] **Step 1: Failing tests** — round trips; `test_existing_tables_unchanged` (the column lists of `bars`, `chain_slices`, `calls`, `grades`, `outcomes`, `mornings`, `verdicts`, `iv_calibration` equal the pre-change schema); `test_new_tables_open_on_an_old_store` (a store created with only the old schema gains the new tables on `connect`).
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(scalp): intraday tables`

### Task 3: The confirmation reading

**Files:** Create `services/options_svc/scalp_confirm.py`. Test: `services/options_svc/tests/test_scalp_confirm.py`.

**Interfaces — produces:**
- `sources(bus, now) -> dict` — one read each of `cache:options:matrix`, `cache:options:net_premium`, `cache:options:flow_alerts`, `cache:sentiment:composite` (`bus.cache_get(k).payload`), each with its age; a source older than `confirm_max_age_min()` or missing is marked `stale`/`absent`.
- `flow_sense(alert) -> int | None` — the spec §5 mapping table (+1 bullish, −1 bearish, None not directional). Confirm each type's real `side` values in `services/options_svc/flow_alerts.py` and `services/options_svc/hiro.py` before writing it; `uoa` and `big_delta` use the bought/sold estimate fields when present.
- `components(src, symbol, direction, win_points, entry_ts) -> dict` — `{"flow", "netprem", "dealer", "sentiment"}` each `{"verdict": +1|-1|0|None, "raw": {...}, "stale": bool}`; `"score"` in [−1, 1] (weights from config, over present components; None when none present); `"word"` ("Confirms" / "Mixed" / "Contradicts" from `confirm_cuts()`, None with no score); `"dealer_line"` (text for the page); `"truncated"` for flow.
- `wall_context(src, symbol, spot, target_spot, stop_spot) -> list[str]` — walls lying between entry and target / stop.
- `snapshot(src, symbols) -> dict` — the raw values to record (walls, flip, net GEX, spot for each symbol and BIG10 member; net premium levels incl. the BIG10 basket; sentiment state/score; flow per-type counts).

**Rules the tests pin (spec §5):** net premium uses the LATEST level, never a difference; the dealer read uses the long-with role's `win_points`; a missing wall (no Greeks) is no reading; the flip is recorded, not scored; sentiment weight 0.5 by default.

- [ ] **Step 1: Failing tests** — fixtures copied from the real view shapes (matrix rows with `call_wall`, `put_wall`, `flip`, `net_gex`, `spot`; net premium `series[sym] = [[ts, call, put], ...]`; a flow alert of each type; `derived.trend.state`): `test_flow_counts_directional_alerts_only`, `test_a_sold_call_is_bearish`, `test_flow_window_truncated_is_marked`, `test_net_premium_reads_the_level_not_a_difference`, `test_dealer_room_agrees_when_wall_beyond_target`, `test_wall_inside_target_disagrees`, `test_no_greeks_walls_are_no_reading` (Review Focus 1), `test_stale_matrix_is_no_reading` (Review Focus 2), `test_sentiment_weighs_half`, `test_score_and_word_cuts`, `test_wall_context_names_walls_in_the_path`, `test_snapshot_holds_every_big10_member`.
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(scalp): the confirmation reading`

### Task 4: Recording every slot

**Files:** Create `services/options_svc/scalp_record.py`. Modify `services/options_svc/scheduler.py` (a `scalp_intraday_due(now, ran) -> str | None` over `intraday_slots`, within `intraday_grace_min`, trading days only; a latched branch), `services/options_svc/scalp_call.py` (record at 08:36 after a successful publish, guarded). Test: `services/options_svc/tests/test_scalp_record.py`, `test_scheduler.py` (extend).

**Interfaces — produces:** `record(bus, day, slot, now=None, db_path=None, directions=None, win_points=None) -> int` — one `intraday_readings` row per scalp symbol (written with or without a direction; `components` scored only where a direction is given), returns rows written; never raises (degrade). In Phase 1 the intraday branch calls `record` only.

- [ ] **Step 1: Failing tests** — `test_record_writes_three_rows_without_directions`, `test_record_is_idempotent_per_slot`, `test_scalp_intraday_due_fires_each_slot_once`, `test_not_on_a_holiday`, `test_0836_publish_also_records`.
- [ ] **Steps 2–4:** FAIL → implement → PASS; then the whole options_svc suite (failing SET: none).
- [ ] **Step 5: Commit** `feat(scalp): record the confirmation inputs every 15 minutes`

### Phase 1 checkpoint (operator)

Merge to `main`, push, promote outside 06:30–15:20 CT. Then on the box, with `.env` loaded, run `tools/scalp_backfill.py` (it now includes the BIG10) and report rows per new symbol. Next trading day: confirm `intraday_readings` holds 22 slots × 3 symbols.

---

## Phase 2 — the intraday study

### Task 5: The intraday reading (price core)

**Files:** Create `services/options_svc/scalp_intraday_inputs.py`. Test: `services/options_svc/tests/test_scalp_intraday_inputs.py`.

**Interfaces — produces:** `Reading` dataclass (`day, slot, symbol, direction, entry_spot, entry_ts, inputs, agree, vol_at_entry, vol_source`) and `reading(symbol, day, slot, db_path=None, cache=None) -> Reading`; `alignment` reuses `scalp_inputs.alignment` semantics (agree / (agree + disagree)).

**Definitions (spec §4):** direction = sign of the symbol's move over the three 5-minute bars ending at `entry_ts`, dead zone `intraday_dead_zone_pct`; inputs: since-open (08:30 open → entry), VWAP side (typical price × volume over 08:30 → entry; **$SPX takes SPY's VWAP side**), big-ten breadth (share of BIG10 members whose last-15-minute move agrees; ≥ `breadth_agree` agrees, ≤ 1 − it disagrees), futures last 15 minutes (/ES for $SPX and SPY, /NQ for QQQ), $VIX last 15 minutes (inverted), each sector fund last 15 minutes (the 08:36 mapping). `vol_at_entry` = the vol symbol's close at the entry bar / 100 (QQQ falls back to $VIX). Absent stays absent. `cache` is an optional per-day bar cache for the study (a cached and uncached reading must be equal).

- [ ] **Step 1: Failing tests** — on hand-built bars: `test_direction_from_last_15_minutes`, `test_dead_zone_no_direction`, `test_spx_uses_spy_vwap`, `test_breadth_counts_big10_members`, `test_vix_rise_disagrees_with_up`, `test_missing_bar_is_absent`, `test_cached_equals_uncached`, `test_entry_never_reads_past_entry_ts` (a bar after entry changes nothing).
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(scalp): the intraday price reading`

### Task 6: The intraday study and report

**Files:** Create `services/options_svc/scalp_intraday_study.py`, `tools/scalp_intraday_report.py`. Modify `services/options_svc/scalp_model.py` (add day-unit functions; existing functions unchanged), `services/options_svc/scheduler.py` (nightly: after the existing rebuild, `scalp_intraday_study.rebuild`, guarded on its own). Tests: `test_scalp_model.py` (extend), `test_scalp_intraday_study.py`.

**Interfaces — produces:**
- `scalp_model.verdict_by_day(scored, rows, min_samples) -> dict` — as `verdict`, but `n_scored` counts DISTINCT days and `min_samples` gates on them; `scalp_model.wilson_days(wins, rows_n, days_n)` → the interval at the row mean with `n = days_n`.
- `scalp_intraday_study.rebuild(db_path=None) -> dict` — every stored trading day (short sessions skipped), every slot, every symbol with a direction and a usable vol, every role, every scale in `iv_scales()` ∪ {`iv_scale()`}, 5-minute bars only, a path with fewer than `time_stop_min / 5` bars skipped; rows into `intraday_outcomes`; verdicts per (symbol, block, role, bucket) — walk-forward over rows sorted by day (rows of one day never see each other — `walk_forward` already holds this), edge only at every scale — into `intraday_verdicts`; one transaction; deterministic; returns counts and seconds.
- `current_rates(symbol, block, bucket, db_path=None) -> dict[role, {p, lo, hi, n_days, p_base, n_base_days, edge}]`.
- The report prints per symbol × block × role × bucket the walk-forward Brier vs base at each scale, n days, edge; "k of 108 cells pass at every scale"; per-block base rates; and a plain "no edge" line per symbol where none.

- [ ] **Step 1: Failing tests** — `test_verdict_by_day_counts_days_not_rows`, `test_min_samples_gates_on_days`, `test_wilson_days_is_wider_than_rows`, `test_rebuild_rows_of_one_day_never_see_each_other` (flip every outcome on a day; no prediction on that day or earlier moves), `test_rebuild_skips_short_sessions`, `test_rebuild_is_deterministic`, `test_rebuild_leaves_the_0836_tables_untouched`, `test_report_says_k_of_cells`.
- [ ] **Steps 2–4:** FAIL → implement → PASS; time a rebuild on a synthetic 150-day, 23-symbol store and record it (target under 10 minutes; if over, add the per-day bar cache from Task 5 — never change a definition).
- [ ] **Step 5: Commit** `feat(scalp): the intraday study`

### Phase 2 checkpoint (operator)

Promote; on the box run the rebuild and `tools/scalp_intraday_report.py`; write the result with a plain reading to `docs/reference/scalp-intraday-study.md` (dated) and link it from CLAUDE.md beside the opening study's link. Report the verdict to the operator, then continue to Phase 3 unless told to stop. Never tune a definition after reading the result.

---

## Phase 3 — the call, grading, page, docs

### Task 7: The view contract

**Files:** Modify `shared/contracts/options.py` (`ScalpView` gains `intraday: dict = {}`; its validator also walks `intraday["latest"][sym]["roles"]` and `intraday["timeline"][*]` rates with the same [0, 1] / finite rules). Test: `shared/contracts/tests/test_scalp_view.py` (extend).

- [ ] Steps: failing tests (an intraday rate of 1.2 refused; a missing `intraday` validates; an old payload without it validates) → implement → PASS → commit `feat(scalp): intraday in the view contract`.

### Task 8: The intraday call

**Files:** Create `services/options_svc/scalp_intraday_call.py`. Modify `services/options_svc/scalp_fetch.py` (`top_up_today(symbols, interval=5, db_path=None, client=None) -> dict` — today's bars only, one request per symbol), `services/options_svc/scheduler.py` (the intraday branch now runs `scalp_intraday_call.run_slot`, which records too), `services/options_svc/handlers.py` (`scalp_refresh` also fills the current intraday slot, per spec §3). Test: `services/options_svc/tests/test_scalp_intraday_call.py`.

**Interfaces — produces:** `build_slot(day, slot, now, db_path=None, client=None) -> dict` (per symbol: slot, block, direction, reading, bucket, roles with rates + strikes + entry value + points + `wall_context`, `confirmation` (Task 3 output), `dealer_line`, `highlight`/`avoid`/`note` via `scalp_call._verdict`, `made_at`, `rules`); `run_slot(bus, slot, now=None, db_path=None, client=None)` — under `scalp_call._RUN_LOCK`: a stored slot is reused whole; no new call after the slot's entry + time stop (note `MISSED`, inputs kept); else top up today's bars for the bar symbols, capture the three 0DTE chains into `intraday_chain_slices`, build, record (Task 4), validate the WHOLE view with the new `intraday` section (`{"slot", "latest": {sym: entry}, "timeline": [compact rows for every published slot today]}`), publish by merging into the current view (the 08:36 `symbols` byte-identical), then save to `intraday_calls`. Same sigma rule as the 08:36 call (chain ATM IV, else vol × iv_scale; none → no strikes, a note).

- [ ] **Step 1: Failing tests** — `test_slot_call_published_and_frozen`, `test_refresh_reuses_a_frozen_slot_whole`, `test_no_new_call_after_the_slot_window`, `test_flat_last_15_minutes_has_no_direction_note`, `test_0836_symbols_untouched_by_an_intraday_publish`, `test_concurrent_0836_and_slot_runs_fetch_once_each` (Review Focus 3), `test_invalid_payload_not_saved`, `test_confirmation_never_changes_the_probability`, `test_refresh_never_makes_an_off_slot_call`, `test_modules_do_not_import_compute`.
- [ ] **Steps 2–4:** FAIL → implement → PASS; whole options_svc suite (failing SET: none).
- [ ] **Step 5: Commit** `feat(scalp): a new call every 15 minutes`

### Task 9: Grading and the scorecard

**Files:** Create `services/options_svc/scalp_intraday_grade.py`. Modify `services/options_svc/scalp_score.py` (an `intraday` section), `services/options_svc/scheduler.py` (nightly: grade intraday after the intraday rebuild, then the existing scorecard republish). Test: `test_scalp_intraday_grade.py`, `test_scalp_score.py` (extend).

**Interfaces — produces:** `grade_day(day, db_path=None) -> int` and `grade_pending(db_path=None) -> dict` — every role of every published slot call, write-once, model result (vol × the call's frozen `iv_scale`) and chain result (vol implied from that slot's own saved slice at its `underlyingPrice`, short leg for a spread), frozen rules from the call, short sessions not graded, the call's confirmation word and `p_base` stored with the grade; `scalp_score.scorecard()` gains `intraday`: per symbol per block — highlighted hit rate vs mean base, cautions, and win rate split by confirmation word, each with its day count.

- [ ] **Step 1: Failing tests** — `test_grades_every_role_of_every_slot`, `test_write_once`, `test_chain_result_uses_the_slots_own_slice`, `test_short_session_calls_not_graded_or_counted` (Review Focus 5), `test_scorecard_splits_by_confirmation_word`.
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(scalp): grade the intraday calls`

### Task 10: The page

**Files:** Modify `webgui/pages/options/scalp.py`, `webgui/page_help.py`. Test: `webgui/tests/test_scalp_page.py` (extend).

**What it shows (spec §10):** the 08:36 card unchanged on top; a "Latest call" section per symbol (slot time, "moving up over the last 15 minutes", the four roles with measured rates or "no measured edge" and day counts labelled "days", the confirmation word with each component, the dealer line, the wall context per role); a "Today's calls" timeline (time, direction per symbol, highlight or none, confirmation word; results after grading); the confirmation panel labelled "Not in the probability — being recorded to be tested"; clock-honest: a latest call older than its next slot reads as past, and the next slot's time is shown. Pure helpers for each, with tests; Tailwind and the page kit only; fmt for numbers; no `ui.html`.

- [ ] **Step 1: Failing tests** — `test_latest_call_names_contracts_by_direction`, `test_confirmation_never_shown_as_a_probability`, `test_dealer_line_and_wall_context_shown`, `test_a_past_slot_is_labelled_past`, `test_timeline_rows`, `test_sample_label_says_days`.
- [ ] **Steps 2–4:** FAIL → implement → PASS; the focused webgui set and the whole webgui suite once; render in `tools/ui_harness.py` with a seed (desktop and 375 px), stop it.
- [ ] **Step 5: Commit** `feat(scalp): intraday calls on the page`

### Task 11: Documentation

CHANGELOG (dated), `docs/webgui-routes.md`, the User Guide / Reference Guide / Technical Reference / API Reference sections (the slots, the price core, the confirmation and its mapping, the dealer read, recording, the tables, the view's `intraday` section, the refresh semantics), `page_help.py`, rebuilt manuals (only the edited ones committed), CLAUDE.md only where an invariant changed (the intraday tables are new; never alter the old). Tests: the docs and manual tests, `tests/test_claude_md_size.py`. Commit `docs(scalp): intraday calls`.

### Final

Whole suites (options_svc, shared, webgui, root) — failing SETs unchanged; final whole-branch review; promote outside 06:30–15:20 CT; first trading day: 21 slot calls published and frozen, readings recorded, and that evening's grades.
