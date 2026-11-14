# Exit chance (shadow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score, nightly and walk-forward, the chance that an open credit capture still reaches its target before the app's own exits end it, against the "distance travelled" baseline, and show it as a third Model Health table.

**Architecture:** A new sibling module `services/options_svc/exit_chance.py` reads closed captures and their marks from `signals.db`, builds one row per thinned mark before the first target hit, fits `shared/ml/logistic.map_fit` walk-forward by entry day, and returns one entry per scanner type. `model_health.build` adds it as `payload["exit"]`, and the page and report draw it.

**Tech Stack:** Python 3.11, numpy, sqlite3 (read-only), NiceGUI page through `pages/ui_kit`, pytest.

**Spec:** [2026-11-14-exit-chance-design.md](2026-11-14-exit-chance-design.md)

## Global Constraints

- Shadow only: nothing outside Model Health reads `payload["exit"]`; no exit rule, paper book or order changes.
- Configurable by default: every tunable in `[exit]` of `config/model_health.toml`, an accessor in `shared/model_health_config.py` that never raises, and a `webgui/config_schema.py` Field.
- The target fraction is `shared.trade_mgmt.stops()["tp_frac"]`; the early-stop exclusion uses `stops()["time_stop_min_hold_min"]`. No new copy of either.
- `exit_chance.py` imports nothing from `compute`. Reads `signals.db` with `mode=ro` (or `immutable=1` in tools), filtered by `shared.structures.not_tracked_sql`.
- Model Health's rules: walk-forward by entry day, day-clustered bootstrap (`metrics.day_bootstrap_diff`), verdict through `model_health.verdict(days, min_days, diff)`.
- A missing input never becomes 0: mean-fill plus a missing-indicator column.
- Page: Tailwind classes only, kit widgets only, whole words (no "TP", "DTE" in labels).
- A failure is `_degrade.degraded("options.exit_chance")` and `payload["exit"] = None`; the other models still publish.

## Review Focus

1. **Income rows are per share.** Fractions must come from `pnl_pct_of_credit` (marks) and a scale derived from the marks for the close, never `entry_credit * 100` alone. A test with a per-share trade.
2. **Leakage across the split.** A trade still open on the test day (closed after it began) must not be in that day's training set. A test that a trade closing on the test day is excluded from that day's fit.
3. **A trade that hits the target on its first mark** has no rows. It must not crash the walk-forward or count as a day with zero weight.
4. **A type with fewer than `min_train_days` entry days** returns an entry with `days = 0`, `diff = None` and verdict `too_few_days`, not an exception.
5. **A NULL `entry_iv_rank`, `entry_short_delta` or `current_underlying`** on every training row: the column's mean-fill must not be NaN (fall back to 0 after standardising, with the indicator = 1).

---

### Task 1: The `[exit]` settings

**Files:**
- Modify: `shared/model_health_config.py`, `config/model_health.toml`, `webgui/config_schema.py`
- Test: `shared/tests/test_model_health_config.py`

**Interfaces:**
- Produces: `exit_enabled() -> bool` (True), `exit_types() -> list[str]` (["SWING", "0DTE", "INCOME"]), `exit_thin_min() -> int` (30, 5..240), `exit_min_train_days() -> int` (15, 5..120), `exit_min_days() -> int` (40, 10..250), `exit_penalty() -> float` (1.0, 1e-6..1e3), `exit_buckets() -> list[float]` ([-1.0, -0.5, -0.25, 0.0, 0.25], sorted, finite, 1..12 edges; a bad list returns the default).

- [ ] **Step 1: Write the failing tests** — `test_exit_defaults` asserts every value above; `test_exit_bad_values_fall_back` writes a toml with `thin_min = "x"`, `buckets = [0.5, "a"]`, `min_days = 0` through the same override fixture the `[range]` tests use and asserts the defaults come back.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest shared/tests/test_model_health_config.py -q` — FAIL (`no attribute exit_enabled`).
- [ ] **Step 3: Implement** a `DEFAULTS["exit"]` dict and the accessors, mirroring `_range` / `_range_number`. Add `[exit]` with a comment per key to `config/model_health.toml`, and a "Exit chance" Section in `config_schema.py` with one Field per key (`buckets` as a list field, the way `[range] horizons_min` is).
- [ ] **Step 4: Run** the config tests and `webgui/tests/test_config_schema.py` — PASS.
- [ ] **Step 5: Commit** `feat(exit-chance): the [exit] settings`.

### Task 2: Rows from the marks

**Files:**
- Create: `services/options_svc/exit_chance.py`
- Test: `services/options_svc/tests/test_exit_chance.py`

**Interfaces:**
- Produces:
  - `INPUTS = ("pnl_frac", "peak_frac", "worst_frac", "life_used", "log_mins_left", "short_delta", "delta_change", "cushion", "entry_iv_rank", "is_ic", "is_ccs")`
  - `DRIFT_INPUTS = ("short_delta", "cushion", "entry_iv_rank")`
  - `@dataclass Trade`: `signal_id, kind, strategy, entry_day: str, first_seen: datetime, closed: datetime, expiration: str, short_strike, call_short, entry_short_delta, entry_iv_rank, exit_reason, close_frac: float | None, marks: list[tuple[datetime, float, float | None, float | None]]` (time, pnl fraction, underlying, short delta)
  - `load(db_path=None) -> list[Trade]` — closed PCS/CCS/IC captures, not tracked, not `MANUAL_CLOSE`, at least two marks with a usable `pnl_pct_of_credit`; `close_frac = realized_pnl / scale`, where `scale` is the median of `unrealized_pnl / (pnl_pct_of_credit / 100)` over the trade's marks (falls back to `entry_credit * 100`). Naive timestamps are Central.
  - `@dataclass Row`: `day: str, trade: str, x: dict[str, float | None], y: int`
  - `rows(trades, *, tp_frac=None, thin_min=None, min_hold=None) -> list[Row]`

- [ ] **Step 1: Write the failing tests** (synthetic `Trade`s; one temp-db test for `load`):
  - `test_rows_stop_at_first_target_hit`: fractions `[-0.1, 0.2, 0.55, 0.3]`, `close_frac` 0.3 → 2 rows, both `y == 1`.
  - `test_rows_label_zero_when_series_ends_short`: `[-0.1, -0.4]`, `close_frac` -1.2 → `y == 0` on every row.
  - `test_close_counts_as_reaching`: marks never reach 0.5, `close_frac` 1.0 (expired) → `y == 1`.
  - `test_thinning_keeps_one_mark_per_thin_min`: 13 marks 5 min apart, `thin_min=30` → rows at minutes 0, 30, 60.
  - `test_early_time_stop_left_out`: `exit_reason="TIME_STOP"`, closed 10 min after `first_seen`, `min_hold=30` → no rows.
  - `test_first_mark_hit_gives_no_rows`: first fraction 0.6 → `[]`.
  - `test_inputs_are_point_in_time`: `peak_frac` / `worst_frac` at row k use marks 0..k only; `cushion` for a PCS with spot 505 and short strike 500 is `(505 - 500) / 505 * 100` (percent of spot toward safety), for a CCS short 510 at spot 505 is `(510 - 505) / 505 * 100`, and an IC takes the smaller of its two sides.
  - `test_missing_inputs_stay_none`: NULL underlying and delta → `x["cushion"] is None`, `x["short_delta"] is None`.
  - `test_load_reads_per_share_trade` (temp `signals.db` built with `signal_db.init_db`): a trade whose marks carry `unrealized_pnl` per share with `pnl_pct_of_credit` set → its `close_frac` equals `realized_pnl / per-share credit`; a tracked row, a `MANUAL_CLOSE` and a debit row are not loaded.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest services/options_svc/tests/test_exit_chance.py -q` — FAIL (no module).
- [ ] **Step 3: Implement** `load` and `rows`. `log_mins_left` = `log(max(years * 525600, 1))` with `years = options_calculator.expiry_time_to_years(mark_time, expiration_date)` (never compute inline). `life_used` = elapsed since `first_seen` over (settlement − `first_seen`), clipped 0..1.
- [ ] **Step 4: Run** the tests — PASS.
- [ ] **Step 5: Commit** `feat(exit-chance): rows and labels from the captured marks`.

### Task 3: Walk-forward, score, evaluate

**Files:**
- Modify: `services/options_svc/exit_chance.py`
- Test: `services/options_svc/tests/test_exit_chance.py`

**Interfaces:**
- Consumes: Task 1 accessors, `Trade`, `Row`, `rows`, `load`.
- Produces:
  - `bucket_rate(train_rows, w, edges) -> callable(pnl_frac) -> float` — the weighted rate per `pnl_frac` bucket; an empty bucket takes the training rate overall.
  - `walk_forward(trades, rows_, *, min_train_days=None, penalty=None, edges=None) -> list[tuple[Row, float, float, float]]` — `(row, p_model, p_distance, weight)` for each scored row. A test day D trains on rows of trades whose `closed` is before D's 00:00 Central. Weights: each entry day 1, split over its trades, each trade over its rows.
  - `score(scored, *, n_boot=None) -> dict` with `days`, `rows`, `trades`, `brier`, `brier_distance`, `diff` (model minus distance, `day_bootstrap_diff`), `reliability`, `last_day`.
  - `evaluate(db_path=None) -> list[dict]` — one per type in `exit_types()` present in the store: `{"type", **score, "inputs": [(day, {name: value})]}` (the drift inputs at each row).
- Model: columns = intercept + standardised `INPUTS` + one missing indicator per input that is ever None in training; prior mean 0, precision `penalty` on every column but the intercept (1e-6).

- [ ] **Step 1: Write the failing tests**:
  - `test_open_trade_not_in_training`: trade A entered day 1 and closed on day 20; with test day 20, A's rows are absent from the fit (assert through a `fit_rows` spy the module exposes, or by checking `walk_forward` with a monkeypatched `_fit` that records its row ids).
  - `test_too_few_days_scores_nothing`: 5 entry days, `min_train_days=15` → `walk_forward` returns `[]`, `score([])["days"] == 0`, `["diff"] is None`.
  - `test_model_beats_distance_when_path_matters`: synthetic trades where `delta_change > 0.2` always ends at y=0 and otherwise y=1 at equal `pnl_frac`, 60 entry days → `score(...)["diff"]["hi"] < 0`.
  - `test_distance_matches_when_only_distance_matters`: y determined by `pnl_frac > 0` alone → `diff` interval spans 0.
  - `test_all_missing_column_is_finite`: every `entry_iv_rank` None → predictions all finite.
  - `test_weights_one_per_day`: the weights of each scored day sum to 1.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; `pytest services/options_svc/tests -q -k "exit_chance or model_health"` green.
- [ ] **Step 5: Commit** `feat(exit-chance): walk-forward score against distance travelled`.

### Task 4: Model Health payload and report

**Files:**
- Modify: `services/options_svc/model_health.py`
- Test: `services/options_svc/tests/test_model_health.py`

**Interfaces:**
- Produces: `exit_entries(signals_db=None) -> list[dict] | None` — `None` when `exit_enabled()` is false or evaluate raises (degrade `options.exit_chance`); else each entry plus `min_days`, `verdict` (`verdict(days, exit_min_days(), diff)`), `drift_status`, `drift` (from `drift(inputs)`, `inputs` popped). `build()` returns `"exit": exit_entries(signals_db)`, and `data_date` also considers their `last_day`. `report_markdown` adds `## Exit chance (shadow)`: one line per type with the verdict words used for the range rows ("Beats" / "Matches" / "Does not beat" / "Too few days"), the difference and range, rows, days, and one sentence that the labels follow the exit rules running at the time.

- [ ] **Step 1: Write the failing tests** — `test_build_carries_exit` (monkeypatched `exit_chance.evaluate` returning one SWING entry → `payload["exit"][0]["verdict"]`), `test_exit_off_is_none`, `test_exit_failure_degrades` (evaluate raises → `None`, degrade counted, `models` still present), `test_report_has_exit_section`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest services/options_svc/tests -q -k model_health` — PASS.
- [ ] **Step 5: Commit** `feat(exit-chance): Model Health payload and report section`.

### Task 5: The page table, help and docs

**Files:**
- Modify: `webgui/pages/model_health.py`, `webgui/page_help.py`, `docs/manuals/user-guide/user-guide.md`, `docs/manuals/reference-guide/reference-guide.md`, `docs/manuals/technical-reference/technical-reference.md`, `docs/webgui-routes.md`, `docs/CHANGELOG.md`
- Test: `webgui/tests/test_model_health_page.py`

**Interfaces:**
- Produces: `EXIT_TITLE = "Exit chance (shadow)"`, `exit_rows(payload) -> list[dict]` (keys: `type`, `verdict`, `difference`, `brier`, `brier_distance`, `rows`, `days`), `exit_columns() -> list[dict]`, `exit_drift(payload) -> list[str]`; `None` exit shows one line ("Off, or it failed tonight: see System Status").

- [ ] **Step 1: Write the failing tests** — rows from a sample payload; `None` → `exit_rows` is `[]`; verdict words match the range table's; column labels contain no "DTE" / "TP".
- [ ] **Step 2: Run** `cd webgui && ../.venv/Scripts/python -m pytest tests/test_model_health_page.py -q` — FAIL.
- [ ] **Step 3: Implement** the table under the range table with `kit.table`, plus the docs: page help bullet, User Guide paragraph, Reference Guide subsection, Technical Reference `### The exit chance (exit_chance.py, payload exit)` (rows, label, inputs, baseline, model, scoring, the weights), the routes line, a CHANGELOG entry with the first reading from Task 6.
- [ ] **Step 4: Run** the page tests plus `tests/test_no_inline_style.py`, `tests/test_ui_kit_guard.py` — PASS.
- [ ] **Step 5: Commit** `feat(exit-chance): the Exit chance table on Model Health, help and manuals`.

### Task 6: First reading and full suites

- [ ] **Step 1:** Run `exit_chance.evaluate` against `E:/TradingBackups/prod_2026-11-13_2003/options-scanner/data/signals.db` (opened `immutable=1`) and the local page harness on that payload. Expected: three entries, each `too_few_days` (Swing about 18 scored days), finite Brier scores.
- [ ] **Step 2:** Put the figures into the CHANGELOG entry.
- [ ] **Step 3:** Run options-scanner, shared, root, options_svc and webgui suites. Expected: no failures; the failing set and skipped set unchanged from the 2026-11-14 runs (2 / 1 / 0 / 0 / 1 skipped).
- [ ] **Step 4: Commit** `docs(exit-chance): first reading`.
