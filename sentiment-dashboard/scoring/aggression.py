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


AGG_WEIGHTS = {"effort": 0.35, "skew": 0.30, "flow": 0.20, "order_flow": 0.15,
               "rejection": 0.20}


def _clamp(v, lo, hi):
    return float(max(lo, min(hi, v)))


def blend_aggression(components, confs, weights=None):
    """Confidence-weighted signed blend of aggression sub-signals.

    components: {name: signed value in [-1,1]} (0.0 = neutral).
    confs: {name: confidence in [0,1]}. Missing/None value -> 0.0; missing/None
      confidence -> 0.0 (drops out).
    Returns (score, aggregate_confidence): score in [-1,1] (round 3), aggregate
      confidence = den (round 3). den<=0 -> (0.0, 0.0).
    """
    weights = weights or AGG_WEIGHTS
    num = den = 0.0
    for k, w in weights.items():
        c = float(confs.get(k, 0.0) or 0.0)
        s = float(components.get(k, 0.0) or 0.0)
        num += w * s * c
        den += w * c
    if den <= 0:
        return 0.0, 0.0
    return round(_clamp(num / den, -1.0, 1.0), 3), round(den, 3)
