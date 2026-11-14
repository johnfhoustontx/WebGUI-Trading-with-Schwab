# Range forecast on Model Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score a range forecast for SPY, QQQ and `$SPX` nightly against a realised-volatility yardstick and the implied move, and show the verdicts on Model Health.

**Architecture:** A new `services/options_svc/range_forecast.py` builds point-in-time rows from `scalp.db` 5-minute bars, fits three ridge-plus-residual-percentile models walk-forward by session, and scores pinball loss. `model_health.build` adds its result as `payload["range"]`; the page draws a second table.

**Tech Stack:** Python 3.11, numpy, existing `shared/ml`, NiceGUI page kit.

**Spec:** `docs/plans/2026-11-14-range-forecast-design.md`

## Global Constraints

- Report only: no other page, rule or chance changes.
- Every input from bars that closed at or before the forecast time t.
- Regular session 08:30 to 15:00 CT; a session missing any of its 78 bars (symbol or its volatility index) is left out whole.
- Tunables in `config/model_health.toml [range]`, each with a `webgui/config_schema.py` field.
- Tier 1 imports nothing new; the page reads only the view.
- No new dependency.

## Review Focus

1. A forecast time near the close where the 60-minute horizon runs past 15:00: left out for that horizon, kept for 30 minutes and rest-of-session.
2. A zero realised volatility (flat 30 minutes): floored before the log, never `-inf`.
3. The first session of a symbol (no previous session): its rows left out, not crashed.
4. `$SPX` volume of 0: the volume input comes from SPY.
5. A symbol with fewer than `min_train_days` sessions: verdict `too_few_days` / `no_data`, no exception.

---

### Task 1: Ridge and pinball loss in `shared/ml`

**Files:** Create `shared/ml/linear.py`; Modify `shared/ml/metrics.py`, `shared/ml/__init__.py`; Test `shared/tests/test_ml_foundation.py`.

**Produces:** `linear.ridge(x, y, w, lam) -> (coef, intercept, mean, scale)` (inputs standardised by weighted mean and std; intercept unpenalised); `linear.predict(model, x) -> ndarray`; `metrics.pinball(q, pred, y, w) -> float | None` (weighted mean of `max(q*(y-pred), (q-1)*(y-pred))`); `metrics.weighted_quantile(values, w, q) -> float`.

- [ ] Tests: ridge with lam=0 recovers an exact line; lam large shrinks toward the weighted mean; a constant column does not divide by zero; pinball of a perfect forecast is 0 and of a hand case is the hand value; weighted_quantile on equal weights matches `np.quantile(method="inverted_cdf")`.
- [ ] Implement; run `pytest shared/tests/test_ml_foundation.py`: PASS; commit.

### Task 2: `range_forecast.py`: rows, inputs, scoring

**Files:** Create `services/options_svc/range_forecast.py`; Test `services/options_svc/tests/test_range_forecast.py`.

**Produces:** `sessions(symbol, db_path) -> {date: [Bar]}` (regular, complete only); `rows(symbol, by_symbol) -> list[Row]` with `Row(day, t_min, horizon, y, inputs: dict)`; `score(symbol, horizon, rows) -> dict` with keys `symbol, horizon, days, min_days, loss, loss_yardstick, loss_implied, diff_yardstick, diff_implied, verdict_yardstick, verdict_implied, coverage, drift_status, drift`; `build(db_path=None) -> list[dict]`.

- [ ] Tests on synthetic bars (a helper writing 78 bars a day into a temp `scalp.db`): target equals hand range for one row; no input uses a bar at or after t (perturb later bars, inputs unchanged); horizon past 15:00 dropped; zero volatility floored; first session dropped; `$SPX` volume from SPY; incomplete session dropped; walk-forward never fits on the test day (a day with a planted outlier target does not change its own forecast); a forecast with all inputs beats the yardstick on data generated from the implied input (sanity); too few days gives `too_few_days`.
- [ ] Implement; verdict reuses `model_health.verdict`; drift reuses `model_health.drift` on `(day, inputs)`; run the tests: PASS; commit.

### Task 3: Config and the payload

**Files:** Modify `shared/model_health_config.py`, `config/model_health.toml`, `webgui/config_schema.py`, `services/options_svc/model_health.py`; Tests `shared/tests/test_model_health_config.py`, `services/options_svc/tests/test_model_health.py`.

**Produces:** `cfg.range_enabled()`, `range_symbols() -> dict[symbol, vol_index]` (default `{"SPY": "$VIX", "QQQ": "$VXN", "$SPX": "$VIX"}`), `range_horizons() -> list[int]` (`[30, 60, 0]`, 0 = rest of session), `range_first()`, `range_last()` (`"08:45"`, `"14:30"`), `range_step_min()` (15), `range_min_train_days()` (40), `range_min_days()` (40), `range_ridge()` (1.0), `range_floor()` (1e-8). `build()` payload gains `"range": [...]`; a failure inside degrades `options.range_forecast` and yields `"range": None`.

- [ ] Tests: shipped file equals defaults; bad values fall back; payload carries `range` and survives a raising `range_forecast.build`.
- [ ] Implement; run both suites: PASS; commit.

### Task 4: Page, report, docs

**Files:** Modify `webgui/pages/model_health.py`, `services/options_svc/model_health.py` (`report_markdown`), `webgui/page_help.py`, the three manuals, `docs/webgui-routes.md`, `docs/CHANGELOG.md`; Test `webgui/tests/test_model_health_page.py`.

**Produces:** `range_rows(payload) -> list[dict]`, `range_columns()`; a second table "Range forecast (shadow)".

- [ ] Tests: row per (symbol, horizon) with "Rest" for 0, both verdict words and classes, coverage as a percentage, absent `range` draws no table rows.
- [ ] Implement; harness look with a seeded payload; commit.

### Task 5: Real data and suites

- [ ] Run `model_health.build` on the 2026-11-13 backup copies; read the verdicts; record them in the CHANGELOG.
- [ ] Suites: shared, root, options_svc, webgui; failing set empty; commit.
