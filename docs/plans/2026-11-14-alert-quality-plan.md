# Alert record and alert quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every fired flow alert forever (A) and score each alert type on Model Health against the same symbol's other windows that day (B).

**Architecture:** A new store `services/options_svc/alert_log.py` written from `handlers.run_flow_alerts`; a scorer `services/options_svc/alert_quality.py` that reads the record and the Gamma Archive's 5-minute spot path; `model_health.build` folds its entries in under `"alerts"`, and the page draws a fourth table.

**Tech Stack:** Python 3.11, sqlite3, numpy, `shared.ml.metrics`, NiceGUI page kit, pytest.

**Spec:** [docs/plans/2026-11-14-alert-quality-design.md](2026-11-14-alert-quality-design.md)

## Global Constraints

- Shadow only: no alert, threshold, cooldown, push, ranking or paper book changes.
- Tier 1 imports unchanged; the page reads only `options:model_health`.
- Every tunable in config with a `webgui/config_schema.py` Field (`test_config_schema.py` fails otherwise).
- Whole words on the page: Unusual activity, Big delta, HIRO surge, HIRO flip, Premium crossover, Gamma flip.
- Tailwind classes only, page-kit widgets only (`kit.table`).
- A naive datetime is Central; convert epoch seconds with `ZoneInfo("America/Chicago")`.
- A swallowed exception over 15 lines calls `_degrade.degraded(...)`.
- Tests use `tmp_path` stores; the root conftest refuses a live SQLite store.
- Write test files with the editor, never a heredoc.
- `gamma_flip` is recorded but NOT in the default scored types.

## Review Focus

1. An alert whose symbol has no archived path that day (a symbol collected only some days): dropped as `price`, never scored as 0.
2. An alert in the last 30 minutes of collection: dropped as `horizon`, never a short window.
3. A symbol-day where every window lies inside some alert's horizon: no base, the alert dropped as `base`, never compared with an empty rate.
4. A type with logged alerts but none scorable yet: reads "too few days", not "no results" (the exit-chance lesson).
5. A carried (modelled) archive row: excluded via `fetched_only_clause`.

---

### Task 1: The alert record and its hook

**Files:**
- Create: `services/options_svc/alert_log.py`
- Modify: `services/options_svc/flow_alerts.py` (`_DEFAULTS` gains `"log": {"enabled": True}`), `config/flow_alerts.toml` (a `[log]` section with a comment), `webgui/config_schema.py` (`_FLOW` gains a "Alert record" Section with Field `log.enabled`), `services/options_svc/handlers.py` (`run_flow_alerts`)
- Test: `services/options_svc/tests/test_alert_log.py`, `services/options_svc/tests/test_handlers.py`

**Interfaces:**
- Produces: `alert_log.DB_PATH` (in the same data folder `scalp_store` uses), `alert_log.connect(db_path=None) -> sqlite3.Connection` (creates the table), `alert_log.record(alerts, *, source="live", db_path=None) -> int` (rows inserted), `alert_log.load(db_path=None, types=None) -> list[dict]` (keys `alert_id, session_date, ts, symbol, type, side, source`, ascending by `ts`; opens read-only, `[]` when the file is missing), `alert_log.logged_enabled() -> bool` (reads `flow_alerts.load_thresholds()["log"]["enabled"]`, a real bool or the default).

- [ ] **Step 1: Write the failing tests** in `test_alert_log.py`:
  - `test_record_writes_each_alert_once`: two alerts `{"id": "SPY|uoa|call|500|2026-11-20", "type": "uoa", "side": "call", "symbol": "SPY", "ts": 1763136000, "text": "x", "volume": 900}`; `record` twice → returns 1 then 0; `load` has one row with `session_date == "2026-11-14"` (Central date of the ts), `source == "live"`.
  - `test_detail_keeps_everything_but_text`: the stored `detail_json` decodes to a dict with `volume` and without `text`, `id`, `symbol`, `type`, `side`, `ts`.
  - `test_alert_without_id_or_ts_is_skipped`: such an alert inserts 0 and raises nothing.
  - `test_load_filters_types_and_missing_file`: `load(types=["uoa"])` drops a `hiro_flip` row; `load(db_path=tmp_path/"none.db") == []`.
- [ ] **Step 2: Run them, expect FAIL** — `.venv\Scripts\python -m pytest services/options_svc/tests/test_alert_log.py -q` → ModuleNotFoundError.
- [ ] **Step 3: Implement `alert_log.py`** — table and columns exactly as the design's "Table `alerts`"; `INSERT OR IGNORE`; one transaction per `record` call.
- [ ] **Step 4: Run them, expect PASS.**
- [ ] **Step 5: Write the failing hook tests** in `test_handlers.py`, set up like `test_run_flow_alerts_hiro_surge_screen_only_and_view`, with `alert_log.DB_PATH` monkeypatched to `tmp_path`:
  - `test_run_flow_alerts_records_every_fresh_alert`: after a tick with one UOA in the stash and one HIRO surge, `alert_log.load()` holds both ids.
  - `test_run_flow_alerts_record_off_writes_nothing`: with `load_thresholds` returning `log.enabled = False`, the store has no rows and the cached alert list is unchanged.
  - `test_run_flow_alerts_record_failure_is_counted`: `alert_log.record` raising → `_degrade.counts()["options.alert_log"]` rises by 1, the cached list and the cooldown map are still written.
- [ ] **Step 6: Run them, expect FAIL.**
- [ ] **Step 7: Hook it** — in `run_flow_alerts`, inside `if fresh:` after the `bus.cache_set(CACHE_FLOW_ALERTS, ...)` block, call `alert_log.record(fresh)` when `alert_log.logged_enabled()`, in its own `try/except` with `_degrade.degraded("options.alert_log")`.
- [ ] **Step 8: Add `[log] enabled = true` to `config/flow_alerts.toml` and the Field**, run `webgui/tests/test_config_schema.py` from `webgui/` and the two test files → PASS.
- [ ] **Step 9: Commit** `feat(alert-log): keep every fired flow alert`.

### Task 2: The seed tool

**Files:**
- Create: `tools/seed_alert_log.py`
- Test: `tests/test_seed_alert_log.py`

**Interfaces:**
- Consumes: `alert_log.record(..., source="seed")`, `gex_history_db.load_flow_contract_days`, `gex_history_db._distinct_session_dates`.
- Produces: `seed(source_conn, db_path=None) -> int` and `main(argv=None) -> int` (opens `gex_history_db.connect(read_only=True)`; prints the count).

- [ ] **Step 1: Write the failing tests:** a tmp `gex_history.db` with `flow_contract_days` rows for two sessions (one `uoa`, one `big_delta`): `seed` returns 2, the stored rows carry `source == "seed"`, `type` from `alert_type`, `ts` from `fired_ts`, `id` from `alert_id`; a second `seed` returns 0; a row with a NULL `fired_ts` is skipped.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**; `detail_json` carries `osi, strike, expiry, volume`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(alert-log): seed from the held contract-day alerts`.

### Task 3: The scoring settings

**Files:**
- Modify: `shared/model_health_config.py`, `config/model_health.toml`, `webgui/config_schema.py`
- Test: `shared/tests/test_model_health_config.py`

**Interfaces:**
- Produces: `alerts_enabled() -> bool`, `alerts_types() -> list[str]` (known names only: `uoa, big_delta, hiro_surge, hiro_flip, crossover, gamma_flip`; unknown dropped; empty → default), `alerts_horizon_min() -> int` (5..120), `alerts_history_sessions() -> int` (5..60), `alerts_min_history() -> int` (3..60, and never above `alerts_history_sessions()`, else its default), `alerts_min_days() -> int` (5..250).
- Defaults: `"alerts": {"enabled": True, "types": ["uoa", "big_delta", "hiro_surge", "hiro_flip", "crossover"], "horizon_min": 30, "history_sessions": 20, "min_history": 10, "min_days": 40}`.

- [ ] **Step 1: Write the failing tests** — defaults returned with no file; `types = ["uoa", "nope"]` → `["uoa"]`; `types = []` → default; `horizon_min = 0` → 30; `min_history = 30` with `history_sessions = 20` → 10; a bool `enabled = "yes"` → True default.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** following the `_exit` / `_exit_number` pattern; the `[alerts]` section in the toml with a comment saying why gamma flip is left out.
- [ ] **Step 4: Add the "Alert quality" Section** to the model_health ConfigFile in `config_schema.py` (types as `kind="symbols"` like `exit.types`), run `test_model_health_config.py` and `webgui/tests/test_config_schema.py` → PASS.
- [ ] **Step 5: Commit** `feat(alert-quality): settings`.

### Task 4: The scorer

**Files:**
- Create: `services/options_svc/alert_quality.py`
- Test: `services/options_svc/tests/test_alert_quality.py`

**Interfaces:**
- Consumes: `alert_log.load`, Task 3's accessors, `gex_archive.DB_PATH`, `gex_history_db.fetched_only_clause`, `gex_history_db.FEED_SYMBOL_LIKE`, `shared.ml.metrics.day_bootstrap_diff`, `model_health_config.bootstrap_draws()`.
- Produces:
  - `GRID_SEC = 300`
  - `load_paths(archive_path=None) -> dict[(symbol, session_date), list[(ts, spot)]]` — `view = 'gex'`, fetched only, `spot > 0`, feed symbols excluded, read-only; `{}` when the file is missing.
  - `window_range(path, i, horizon_sec) -> float | None` — `(max - min) / path[i].spot` over points with `path[i].ts <= ts <= path[i].ts + horizon_sec`; `None` unless some point has `ts >= path[i].ts + horizon_sec - GRID_SEC // 2`.
  - `slot(ts) -> int` — Central minute of day // 5.
  - `usual_ranges(paths, horizon_sec, history, min_history) -> dict[(symbol, session, slot), float]` — median of that slot's `window_range` over the `history` sessions of that symbol strictly before `session` (in date order); absent below `min_history`.
  - `score_type(kind, alerts, paths, usual, horizon_sec, n_boot) -> dict` — keys `type, alerts, logged, days, hit_rate, base_rate, diff, dropped, last_day`; `dropped` = `{"price", "horizon", "history", "base"}` counts.
  - `evaluate(log_path=None, archive_path=None) -> list[dict]` — one entry per configured type, in config order.
- Algorithm (the one choice the signatures leave open):
  - An alert's start is the last path point with `ts <= alert.ts` and `alert.ts - ts <= GRID_SEC`; none → `price`.
  - No window → `horizon`; no usual → `history`.
  - Label = `window_range > usual`.
  - Base windows of a symbol-day are its points with a window and a usual whose `ts` is outside `[a.ts - GRID_SEC, a.ts + horizon_sec]` for every logged alert of ANY type on that symbol-day; none → `base`.
  - Per scored alert: `loss_a = -label`, `loss_b = -base_rate(symbol-day)`, weight `1 / scored alerts that day`; `diff = day_bootstrap_diff(loss_a, loss_b, days, w, n_boot=n_boot)`.
  - `hit_rate` and `base_rate` are the same day-weighted means of `label` and of the base rates.

- [ ] **Step 1: Write the failing tests** on a synthetic archive built with `gex_archive.connect_archive(tmp_path/...)` and a tmp alert log:
  - `test_window_range_needs_a_full_horizon`: a path of 5-minute points 100, 101, 99, 102, 100, 100, 100 → range at 0 with 1800 s is `(102 - 99) / 100`; at index 5 → None.
  - `test_usual_uses_only_earlier_sessions`: 12 sessions where the 10:00 slot range is 0.01 on sessions 1-11 and 0.5 on session 12 → `usual[(sym, s12, slot)] == 0.01` and `usual[(sym, s11, slot)]` is the median of sessions 1-10; session 10's usual is absent with `min_history = 10`.
  - `test_alert_wider_than_usual_beats_a_quiet_base`: 52 sessions, each with one `uoa` alert at 10:00 whose window is wide and quiet windows elsewhere → `hit_rate == 1.0`, `base_rate == 0.0`, `diff["hi"] < 0`, `days == 42` (52 minus the 10 spent as history).
  - `test_busy_day_lifts_both_sides`: alert window and all base windows equally wide → `hit_rate == base_rate`, diff spans 0.
  - `test_drops_are_counted_not_scored`: an alert with no path that day (`price`), one at 14:55 (`horizon`), one on the first session (`history`), one on a symbol-day of nothing but alert windows (`base`) → each counted once, `alerts == 0`.
  - `test_carried_rows_are_ignored`: an archive row with `carried_age_sec = 30` at the window's high leaves the range unchanged.
  - `test_type_with_no_alerts_is_no_data`: `evaluate` with `types = ["uoa", "crossover"]` and only uoa logged → the crossover entry has `alerts == 0, logged == 0, diff is None`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**, guarding nothing inside (the caller in Task 5 counts failures).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(alert-quality): score each alert type against its own day`.

### Task 5: Model Health service

**Files:**
- Modify: `services/options_svc/model_health.py`
- Test: `services/options_svc/tests/test_model_health.py`

**Interfaces:**
- Consumes: `alert_quality.evaluate`, `model_health_config.alerts_enabled / alerts_min_days`, `verdict`.
- Produces: `alert_entries(log_db=None, archive_db=None) -> list | None` and a payload key `"alerts"`. Each entry is the scorer's dict plus `min_days` and `verdict`; `no_data` with `logged > 0` becomes `too_few_days`. `build` gains `log_db=None, archive_db=None` and takes `last_day` from the entries like the exit entries. In `report_markdown`, a `## Alert quality (shadow)` section with the table `| Alert | Verdict | Wider after the alert | Wider at other times | Difference (95%) | Alerts | Days | Dropped |`, the difference shown as alert minus base (`-mean`, from `-hi` to `-lo`).

- [ ] **Step 1: Write the failing tests:**
  - Extend the autouse fixture to stub `alert_quality.evaluate` → `[]`.
  - `test_alert_entries_verdicts`: two entries, one at 45 days with diff `{mean: -0.1, lo: -0.15, hi: -0.05}` → `beats`, one at 9 days → `too_few_days`.
  - `test_logged_but_unscored_reads_too_few_days`: `days == 0, diff None, logged 12` → `too_few_days`.
  - `test_alert_quality_failure_is_counted`: `evaluate` raising → `payload["alerts"] is None`, `options.alert_quality` count +1, the other sections still built.
  - `test_alert_quality_switched_off`: with `[alerts] enabled = false` → `None`, `evaluate` not called.
  - `test_report_shows_alert_section`: the report contains `## Alert quality (shadow)` and `| Unusual activity | Too few days (9 of 40)` and the difference flipped to the alert's side (mean -0.1 printed as `0.100`).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**; the word map for types lives here as `ALERT_WORDS` and is mirrored on the page (Task 6 pins both).
- [ ] **Step 4: Run** `.venv\Scripts\python -m pytest services/options_svc/tests/test_model_health.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(alert-quality): on Model Health and its report`.

### Task 6: The page

**Files:**
- Modify: `webgui/pages/model_health.py`
- Test: `webgui/tests/test_model_health_page.py`

**Interfaces:**
- Consumes: the payload's `"alerts"` list from Task 5.
- Produces: `ALERT_TITLE = "Alert quality (shadow)"`, `ALERT_NOTE`, `ALERT_OFF`, `ALERT_WORDS`, `alert_rows(payload) -> list`, `alert_columns() -> list`, `dropped_text(entry) -> str`.

- [ ] **Step 1: Write the failing tests:**
  - `test_alert_rows_read_from_the_alert_side`: an entry with `hit_rate 0.61, base_rate 0.44, diff {mean: -0.17, lo: -0.25, hi: -0.09}` → row `wider_after == "61%"`, `wider_other == "44%"`, difference text shows `+0.17` with `+0.09 to +0.25`, `type == "Unusual activity"`.
  - `test_alert_rows_unknown_type_shows_its_name`: `type "new_kind"` → shown as `new_kind`.
  - `test_dropped_text`: `{"horizon": 3, "history": 0, "price": 1, "base": 0}` → `"3 too late in the session, 1 with no price"`; all zero → `""`.
  - `test_alert_words_match_the_service`: `model_health_page.ALERT_WORDS == services.options_svc.model_health.ALERT_WORDS` (read by parsing the service file's literal, like the existing cross-tier checks, so Tier 1 imports nothing new).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**: a fourth block in `render` after the exit table, the same title/note/status/`kit.table` shape, the verdict slot, and a row of `dropped_text` lines; `_paint` fills it, and `ALERT_OFF` shows when the payload has no `alerts`.
- [ ] **Step 4: Run** the page tests plus `webgui/tests/test_no_inline_style.py` and `test_ui_kit_guard.py` from `webgui/` → PASS.
- [ ] **Step 5: Check it in the browser** with `tools/ui_harness.py` on a fake payload with two alert entries; screenshot the new table.
- [ ] **Step 6: Commit** `feat(alert-quality): the Model Health table`.

### Task 7: Documentation

**Files:**
- Modify: `webgui/page_help.py` (Model Health guide), `docs/manuals/user-guide.md`, `docs/manuals/technical-reference.md`, `docs/manuals/reference-guide.md` (the same sections the exit chance touched), `docs/webgui-routes.md` (the Model Health route), `docs/CHANGELOG.md` (a new top entry; the previous one becomes Prior), `CLAUDE.md` (one Folder-map row: `services/options_svc/data/alert_log.db` kept forever and in the nightly backup, never in `DB_EXCLUDE`)
- Test: `tests/test_claude_md_size.py`, the manuals' own tests, `webgui/tests/test_page_help*.py`

- [ ] **Step 1: Write the text** — what the table says, the same-day comparison, that gamma flip is recorded but not scored until the regime study reads, the drop reasons, the seed tool's one-off run.
- [ ] **Step 2: Run** `tests/test_claude_md_size.py` and the page-help and manual tests → PASS.
- [ ] **Step 3: Run the suites the change touches**, comparing the failing SET with the start: shared, root, options_svc, webgui.
- [ ] **Step 4: Commit** `docs(alert-quality): help, manuals, routes, changelog`.
