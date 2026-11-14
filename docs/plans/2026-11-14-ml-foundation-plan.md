# Machine learning foundation (Phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A numpy-only `shared/ml` library, with the Opening Scalp's chance-of-target fit moved onto it and producing identical numbers.

**Architecture:** Four small pure modules (logistic, walk-forward, baselines, metrics). `services/options_svc/scalp_prob_fit.py` keeps its public names and stored JSON and delegates the general parts.

**Tech Stack:** Python 3.11, numpy, pytest.

**Spec:** [2026-11-14-ml-foundation-design.md](2026-11-14-ml-foundation-design.md)

## Global Constraints

- numpy only; no new dependency, no `requirements*` change.
- `shared/ml` imports nothing from `services/`, no store, no config; the webgui imports nothing from it.
- Numeric guards from `shared.numeric` (no new private `_finite` / `_num`).
- Tests in `shared/tests/` (no new test folder).
- Stored `prob_models` JSON and every scalp output identical, to the last bit.

## Review Focus

- `walk_forward` in `scalp_prob_fit` must still call the MODULE-LEVEL `fit` at call time (`test_walk_forward_never_scores_a_fitted_week` monkeypatches it).
- Float summation order in Brier and calibration must stay the scalp code's (Python sums in row order), or the golden test moves in the last digit.
- Empty inputs: `brier` / `reliability` / `usual_rates` on no rows return `None` / empty tenths / `[]`, never raise.
- A zero or non-finite total weight gives `None`, not a division error.
- Day bootstrap with one day returns an interval equal to the point estimate, not NaN.

---

### Task 1: Golden values on today's code

**Files:** Test: `services/options_svc/tests/test_scalp_prob_fit_golden.py`

- [ ] Write `test_fit_and_walk_forward_match_frozen_values`: two symbols, factors `slope_5` (planted 1.2), `netprem` (thin, tight prior), `block:midday`; fit all four roles and walk-forward `long_with`; assert a sha256 of the canonical JSON (`repr` floats, sorted keys) of every model field and walk-forward dict equals the frozen digest, plus three explicit values.
- [ ] Run it on the unchanged code: PASS. Commit.

### Task 2: `shared/ml`

**Files:** Create `shared/ml/__init__.py`, `logistic.py`, `walkforward.py`, `baselines.py`, `metrics.py`; Test `shared/tests/test_ml_foundation.py`.

**Produces:**
- `logistic.sigmoid(z)`; `logistic.map_fit(x, y, w, mean, prec, *, tol=1e-8, max_steps=50) -> np.ndarray` (raises `ValueError` on a non-finite end); `logistic.data_share(x, w, theta, prec) -> np.ndarray`.
- `walkforward.walk_forward(n, period_of, min_train, fit_fn, predict_fn, keep=None) -> (scored: list[tuple[int, object]], periods: int)`: `period_of(i)` orderable; `fit_fn(train_indices)`; `predict_fn(model, i)`; `keep(i)` filters test rows.
- `baselines.usual_rates(groups, days, wins, weights) -> list[float | None]`.
- `metrics.brier(p, y, w)`, `metrics.log_loss(p, y, w)` -> `float | None`; `metrics.reliability(p, y, w, bins=10) -> list[[lo, hi, mean_p, rate, n]]`; `metrics.day_bootstrap_diff(loss_a, loss_b, days, w, n_boot=2000, seed=0, level=0.95) -> dict(mean, lo, hi, days)`.

- [ ] Tests first: `map_fit` with a tiny prior matches the closed-form log-odds of a two-group table; `data_share` in 0..1; `walk_forward` never fits on a test period and skips the first `min_train`; `usual_rates` excludes same-day rows; Brier / log loss / reliability on hand-computed cases; empty and zero-weight inputs; bootstrap on one day and on a known difference.
- [ ] Implement; run `shared/tests/test_ml_foundation.py`: PASS. Commit.

### Task 3: Move the scalp fit onto the library

**Files:** Modify `services/options_svc/scalp_prob_fit.py` (`_sigmoid`, `_newton`, `_data_share`, `_usual_rates`, `_calibration`, `walk_forward`).

- [ ] Replace the bodies with library calls; keep every public name and the stored dict shape.
- [ ] Run `test_scalp_prob_fit_golden.py`, `test_scalp_prob_fit.py`, `test_scalp_prob.py`, `test_scalp_prob_features.py` unchanged: PASS. Commit.

### Task 4: Real data, suites, docs

- [ ] Copy prod `scalp.db` (2026-11-13 backup) twice to the scratchpad; `refit` on one with the pre-move commit and on the other with the new one; compare the stored `prob_models` JSON byte for byte.
- [ ] Run the options_svc suite and the shared suite; compare failing sets to `main`.
- [ ] CHANGELOG entry; CLAUDE.md folder-map line for `shared/ml` (Tier 2 and tools only). Commit.
