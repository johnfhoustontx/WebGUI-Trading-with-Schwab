# Model Health (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A nightly, report-only score of the app's stated chances, published as `cache:options:model_health`, written as a dated report, and shown on a private More → Model Health page.

**Architecture:** `services/options_svc/model_health.py` runs one adapter per model, scores each with `shared/ml`, publishes the view and writes the report. It runs as the last step of the 16:20 scalp branch and on the `model_health_rebuild` command. `webgui/pages/model_health.py` reads the view.

**Tech Stack:** Python 3.11, numpy, NiceGUI, Redis bus, pytest.

**Spec:** [2026-11-14-model-health-design.md](2026-11-14-model-health-design.md)

## Global Constraints

- Report only: no chance, rule, config or other page changes; no alert.
- `shared/ml` stays numpy-only and imports no store or config.
- Tier 1 imports nothing new (`bus_client`, the page kit, `fmt`); the page is private (not in `live_screens`).
- Every tunable in `config/model_health.toml` and `webgui/config_schema.py`.
- Tailwind classes only; kit components only.
- Brier prints three decimals (design call); everything else through `fmt`.

## Settled details (my calls, from the design's open edges)

- **Scalp models pool their four trades** into one row per call kind (each trade's chance is a chance; the usual rate is per (symbol, trade[, block])). The refit-history row pools the stored per-trade results, weighted by `rows`: **Beats** only if all four trades' `beats_usual` are true, **Does not beat** if the pooled Brier is above the pooled usual, else **Matches**; it also shows "n of 4 trades beat".
- A signal with `R <= 0` is a loss (y = 0); a scratch at exactly 0 counts as not a win.
- `data_date` is the latest day with a scored row; the payload has no wall-clock field, so `skip_unchanged` holds.
- Verdict `no_data` when a model has no scored row at all.

## Payload

```
{"data_date": "YYYY-MM-DD" | None,
 "models": [{"key", "label", "verdict", "days", "min_days", "rows",
             "brier", "brier_usual", "diff": {"mean","lo","hi","days"} | None,
             "reliability": [[lo, hi, mean_p, rate, n] x 10],
             "drift_status": "steady"|"moved"|"shifted"|"too_little_history",
             "drift": [{"input","psi","band","missing_before","missing_after"}],
             "note": str | None,
             "history": {"verdict","brier","brier_usual","weeks","days","beat_trades"} | None}]}
```

Model keys, in page order: `scalp_morning`, `scalp_intraday`, `signals_0DTE`, `signals_SWING`, `signals_INCOME` (a signals model with no rows is omitted).

## Review Focus

- A model with rows on fewer days than `min_days` must never print Beats / Does not beat.
- Drift with a reading absent on every recent day: reported as a missing-share change, PSI finite.
- An input present only in the recent window (new factor): no reference → not flagged as shifted.
- A cold view: the page says it has not been computed yet, never an empty table as if all models were fine.
- The nightly step failing must leave the previous view untouched and count a degrade.

---

### Task 1: PSI and the config loader

**Files:** Modify `shared/ml/metrics.py`; Create `shared/model_health_config.py`, `config/model_health.toml`; Modify `repo_paths.py` (`MODEL_HEALTH_TOML`); Test `shared/tests/test_ml_foundation.py`, `shared/tests/test_model_health_config.py`.

**Produces:** `metrics.psi(reference, recent, bins=10) -> dict(psi: float | None, missing_before: float | None, missing_after: float | None)` (None entries are the missing bin; edges at reference deciles, duplicates merged; each bin's share floored at 1e-4; `psi` None when either side is empty). Config accessors: `enabled()`, `min_days()`, `recent_sessions()`, `min_reference_sessions()`, `psi_moved()`, `psi_shifted()`, `bootstrap_draws()`; defaults true / 20 / 5 / 8 / 0.10 / 0.25 / 2000; a bad value falls back to its default.

- [ ] Tests: identical samples → psi 0; a hand-computed two-bin case; all-missing recent → missing_after 1.0 and finite psi; empty side → None; loader defaults and a bad-value fallback.
- [ ] Implement; run both test files: PASS; commit.

### Task 2: The signal reader and the scoring module

**Files:** Modify `options-scanner/signal_db.py` (`get_model_health_rows(db_path=DEFAULT_DB_PATH) -> list[dict]`, closed signals only, `not_tracked_sql` in WHERE); Create `services/options_svc/model_health.py`; Test `services/options_svc/tests/test_model_health.py`; update `tests/test_tracked_rows_stay_hidden.py` only if it names readers.

**Produces:** `model_health.build(scalp_db=None, signals_db=None) -> dict` (the payload); adapters `_scalp_rows(kind, db)`, `_signal_rows(db)`; `verdict(days, min_days, diff) -> str`; `history_row(models_by_role) -> dict | None`.

- [ ] Tests (seeded tmp stores): live scalp rows join call chance to outcome by (day, slot, symbol, role); the usual rate uses earlier days of the full history; `verdict` order and the zero edges; too few days; history pooling; signals per scanner type, ICs and tracked rows left out; drift lists only moved/shifted inputs, worst first; a failure inside one adapter raises (build is not silent).
- [ ] Implement; run the test file: PASS; commit.

### Task 3: Command, nightly step, view and report

**Files:** Modify `services/options_svc/handlers.py` (`model_health_rebuild` in `_COMMANDS` and `SLOW_LANE`), `services/options_svc/scheduler.py` (last guarded step of `_scalp_bars_branch`, skipped when not `enabled()`); add `model_health.run(bus, now=None) -> dict | None` (build, `cache_set("cache:options:model_health", payload, skip_unchanged=True)`, write `services/options_svc/data/model_health/<data_date>/report.md`; on exception `_degrade.degraded("options.model_health")`, publish nothing, return None).

- [ ] Tests: `run` publishes the view and writes the report on a fake bus and tmp folder; a failing build publishes nothing and counts a degrade; the command is registered and on the slow lane; the scheduler step calls `run` after the intraday rebuild and respects `enabled`.
- [ ] Implement; run `test_model_health.py` and the handlers / scheduler tests: PASS; commit.

### Task 4: The page

**Files:** Create `webgui/pages/model_health.py`; Modify `webgui/main.py` (`MORE_CHILDREN`, the route colour, `@_page("/model-health")`), `webgui/page_help.py`, `webgui/config_schema.py` (the file's catalogue entry); Test `webgui/tests/test_model_health_page.py`, plus the route in `test_shell.py` and the page in `test_no_inline_style.py`.

**Produces:** `rows(payload) -> list[dict]`, `verdict_text(model) -> str`, `status_text(payload) -> str`, `render()`.

- [ ] Tests: verdict words and fixed classes; Brier to three decimals; "Too few days (19 of 20)"; a cold view's status line; history rows labelled as the refit's; drift summary text.
- [ ] Implement with `kit.page`, `kit.header(view=...)`, `kit.status_line`, `kit.region`, `kit.table`, `kit.button("Recompute")` → `bus_client.request("options", {"type": "model_health_rebuild"})`, `watch_view`; run the page tests: PASS; commit.

### Task 5: Docs, suites, a look in the harness

- [ ] Run `tools/ui_harness.py` on the page with a seeded view; screenshot.
- [ ] Run `model_health.build` on copies of prod's `scalp.db` and `signals.db` (2026-11-13 backup) and read the numbers.
- [ ] `docs/webgui-routes.md`, the User Guide, Reference Guide and Technical Reference sections, CHANGELOG.
- [ ] Suites: shared, root, options_svc, webgui; compare failing sets with `main`; commit.
