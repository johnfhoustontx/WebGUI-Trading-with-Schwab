"""Signed net-aggression blend (-1..+1, 0.0 = neutral).

Pure functions — scalar in, scalar out (no pandas, no tk, no I/O). The AGGRESSION
axis of the five-state classifier: motivated buying / no supply on dips (positive)
vs urgent selling / protection-buying (negative). The signed analog of
scoring/intraday_trend.py:blend_trend (neutral 0.0 not 50, output clamped to
[-1, 1]). Sub-signals arrive ALREADY signed by the caller — effort is positive for
motivated buying; skew and flow are passed in already sign-flipped so that rising
put demand arrives NEGATIVE. This function is sign-agnostic: it blends signed
inputs and flips nothing.
"""
from __future__ import annotations
from ._common import clamp as _clamp


# ⚠ These sum to 1.30, NOT 1.0 -- `rejection` and `option_flow` were added without
# rebalancing the original four. They are left as written because the blend divides
# by the weight actually present, so the numbers below are RATIOS and the effective
# shares are each w/1.30: effort 26.9%, skew 23.1%, flow 15.4%, order_flow 11.5%,
# rejection 15.4%, option_flow 7.7%. What was NOT safe was reporting the raw sum as
# a confidence -- see blend_aggression (fixed 2026-08-20).
AGG_WEIGHTS = {"effort": 0.35, "skew": 0.30, "flow": 0.20, "order_flow": 0.15,
               "rejection": 0.20, "option_flow": 0.10}


def blend_aggression(components, confs, weights=None):
    """Confidence-weighted signed blend of aggression sub-signals.

    components: {name: signed value in [-1,1]} (0.0 = neutral).
    confs: {name: confidence in [0,1]}. Missing/None value -> 0.0; missing/None
      confidence -> 0.0 (drops out).
    Returns (score, aggregate_confidence): score in [-1,1] (round 3), aggregate
      confidence in [0,1] = the SHARE of total weight that reported, times its
      confidence (round 3). den<=0 -> (0.0, 0.0).

    The confidence is divided by the total weight because AGG_WEIGHTS sums to 1.30
    (see above): returning the raw sum published 1.3 for a fully-confident read,
    breaking the [0,1] invariant every consumer assumes -- it is stored in
    market_state_history_db and blended as a confidence downstream. The SCORE is
    untouched by this: it already divided by the same present-weight sum.
    """
    weights = weights or AGG_WEIGHTS
    total_w = sum(weights.values()) or 1.0
    num = den = 0.0
    for k, w in weights.items():
        c = float(confs.get(k, 0.0) or 0.0)
        s = float(components.get(k, 0.0) or 0.0)
        num += w * s * c
        den += w * c
    if den <= 0:
        return 0.0, 0.0
    return (round(_clamp(num / den, -1.0, 1.0), 3),
            round(_clamp(den / total_w, 0.0, 1.0), 3))
