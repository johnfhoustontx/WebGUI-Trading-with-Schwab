# Machine learning foundation (roadmap Phase 0) — design

Date: 2026-11-14. Status: proposed. Roadmap:
[2026-11-14-machine-learning-roadmap.md](2026-11-14-machine-learning-roadmap.md).

## Intent

Every model this app has fitted rebuilt the same machinery privately. Phase 0
lifts the parts that are not specific to one model into one shared library, so
the range forecast (Phase 2) and meta-labeling (Phase 3) start from tested code
instead of a third and fourth private copy. The proof that the library is right
is that the Opening Scalp's chance of target, moved onto it, produces exactly the
numbers it produces today.

## Goals

- `shared/ml/`, a pure library with no store, no config and no Schwab call:
  - **logistic**: the MAP logistic fit (Gaussian prior per weight, Newton steps
    with step halving), prediction, and each weight's data share of its
    posterior precision.
  - **walk-forward**: score a model only on periods it was not fitted on, given a
    function that names a row's period, a fit function and a predict function.
  - **baselines**: the expanding "usual rate", the weighted win share of a
    group over strictly earlier days.
  - **metrics**: weighted Brier score and log loss; the reliability table (mean
    predicted against observed per tenth); a day-clustered bootstrap interval
    for the difference between two scores.
- `services/options_svc/scalp_prob_fit.py` uses the library for the fit, the
  prediction, the walk-forward split, the usual rate and the calibration table.
  Its public names (`Row`, `Model`, `fit`, `predict`, `walk_forward`, `refit`,
  `training_rows`) and the JSON it stores are unchanged.

## Non-goals

- No new model, no screen change, no config key.
- No new dependency. The library uses numpy only; scikit-learn and LightGBM are
  added in Phase 2, the first phase that fits a tree or quantile model.
- No model registry and no prediction log yet (see Approaches).
- The swing factor model is not moved: it is a cross-sectional ranking scored by
  correlation over rolling, purged windows of trading days, a different shape.

## Anti-goals

- A scalp chance that changes by any amount, on any stored day.
- The library importing anything from `services/`, a store, or config: it must
  be usable by a timer-run tool, a service, or a research script alike.

## Constraints

- Tier 2 and tools only. The webgui imports nothing from `shared/ml` (the Tier 1
  allow-list does not grow).
- Numeric guards come from `shared/numeric` (a new private `_finite` fails
  `shared/tests/test_numeric.py`).
- Tests go in `shared/tests/` (a new test folder would need a CI row).

## Approaches

| | What Phase 0 holds | Trade-off |
|---|---|---|
| **A. Library and port (chosen)** | `shared/ml` on numpy; the scalp model moved onto it | Smallest change that proves the foundation. The registry and prediction log arrive in Phase 1, with the Model Health page that reads them |
| B. Roadmap as written | A, plus scikit-learn and LightGBM in the lock, an `ml.db` registry and prediction log, and the scalp call writing to it | Everything up front, but a dependency nothing uses until Phase 2, and a new store whose only writer already has its own log (`prob_models`, the frozen chance on each call) |
| C. Also move the swing model | A, plus the swing fit's walk-forward | Dropped: a different shape of model, and it ships through its own gate; forcing both into one splitter risks the model that is live |

## Verification

1. **Golden values first.** Before any code moves, a test fits and walk-forwards
   the existing synthetic rows and freezes the outputs (weights, intercepts, data
   shares, Brier scores, calibration table) to full float precision. It passes on
   today's code and must pass, unchanged, after the move.
2. **The existing suite**, `services/options_svc/tests/test_scalp_prob_fit.py`
   and `test_scalp_prob.py`, passes without one edit.
3. **Library tests** against independent references: the logistic fit with a
   near-zero prior matches the maximum-likelihood answer of a hand-solvable case;
   Brier and reliability on hand-computed examples; the walk-forward never
   predicts a period with a model fitted on it; the usual rate never sees its own day.
4. **Real data.** On a copy of prod's `scalp.db` from the 2026-11-13 backup,
   `refit` before and after the move, each into its own copy, and the stored model
   JSON compared byte for byte.

## Left to the builder

- Module names inside `shared/ml/` and the exact signatures, provided the scalp
  fit reads naturally through them.
- Whether `_calibration` keeps its current list shape inside the library or the
  scalp module adapts a richer one (the stored shape must not change).
