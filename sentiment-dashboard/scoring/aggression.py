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
from ._common import clamp as _clamp, num as _num


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
        # A NON-FINITE input is MISSING, not extreme, and it drops the component
        # out entirely rather than contributing a neutral 0 that still consumes
        # weight. `float(x or 0.0)` did NOT do this: NaN is truthy, so it survived
        # into num/den; `den <= 0` never caught it (nan <= 0 is False); and
        # `clamp(nan, -1, 1)` returns +1.0. Measured before the fix, one NaN
        # component read (1.0, 0.5) and one NaN CONFIDENCE read (1.0, 1.0) -
        # maximum bullish aggression at maximum confidence, from no data. This
        # value is stored in market_state_history_db and drives the
        # state-transition phone alert.
        #
        # An ABSENT key still means neutral-0.0-with-its-confidence, as documented;
        # only a broken NUMBER drops out.
        c = _num(confs.get(k, 0.0))
        raw = components.get(k, 0.0)
        sv = _num(raw)
        if c is None or sv is None:
            continue
        num += w * sv * c
        den += w * c
    if den <= 0:
        return 0.0, 0.0
    return (round(_clamp(num / den, -1.0, 1.0), 3),
            round(_clamp(den / total_w, 0.0, 1.0), 3))
