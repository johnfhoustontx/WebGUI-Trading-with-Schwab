"""The shared machine learning library (roadmap Phase 0).

Pure functions over numbers: no store, no config, no Schwab call, nothing from
``services/``, so a service, a timer-run tool and a research script use the same
code. Tier 1 (the webgui) never imports it. Numpy only until a phase needs more.

* ``logistic``: the MAP logistic fit, prediction and each weight's data share.
* ``walkforward``: score a model only on periods it was not fitted on.
* ``linear``: weighted ridge regression on standardised inputs.
* ``baselines``: the usual rate, a group's win share on earlier days.
* ``metrics``: Brier score, log loss, the reliability table and a
  day-clustered bootstrap of the difference between two scores, the
  pinball loss and a weighted quantile.

Design: docs/plans/2026-11-14-ml-foundation-design.md.
"""
