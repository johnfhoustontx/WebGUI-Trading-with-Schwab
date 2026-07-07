"""Rejection/defense score (signed, [-1, 1]) — signed AGGRESSION refinement.

Pure functions: a list of recent DAILY OHLC dicts in, a frozen RejectionDefense
out (no pandas, no tk, no I/O). One input to the five-state classifier's signed
*aggression* axis — is the tape RESILIENT (dips get bought, no supply at highs
-> positive) or EXHAUSTED (long upper wicks / rejection near the highs ->
negative)? Mirrors the frozen-result / _clamp / confidence-in-[0,1] idiom of
scoring/effort.py.

Design choices (documented constants below):
- The rejection signal reads UPPER-wick ratios on bars printing near the period
  high: (high - max(open, close)) / (high - low). Big clustered upper wicks near
  the highs = supply/exhaustion.
- The resilience signal reads the CLOSE-LOCATION-VALUE (close-vs-range) on bars
  printing near the period low: (close - low) / (high - low). A bar that dips to
  the lows but closes back in the upper half of its range = a defended dip; this
  single measure captures both "long lower wick" and "closed back up".
- score = mean_resilience(near-low bars) - mean_rejection(near-high bars).
"""
from __future__ import annotations
from dataclasses import dataclass

MIN_BARS = 5           # below this -> confidence 0.0
FULL_CONF_BARS = 15    # bar count at which confidence saturates
TOP_FRAC = 0.3         # a bar is "near the high" if its high is in the top 30% of range
LOW_FRAC = 0.3         # a bar is "near the low" if its low is in the bottom 30% of range
_REQUIRED = ("open", "high", "low", "close")


@dataclass(frozen=True)
class RejectionDefense:
    score: float          # signed [-1.0, 1.0]; + = resilience, - = exhaustion
    confidence: float     # [0.0, 1.0]


def _clamp(v, lo, hi):
    return float(max(lo, min(hi, v)))


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _clean(bar):
    """Return (open, high, low, close) or None; zero/negative-range bars dropped."""
    if not isinstance(bar, dict):
        return None
    vals = [_num(bar.get(k)) for k in _REQUIRED]
    if any(v is None for v in vals):
        return None
    o, h, l, c = vals
    if h <= l:
        return None
    return (o, h, l, c)


def score_rejection_defense(bars) -> RejectionDefense:
    if not bars:
        return RejectionDefense(0.0, 0.0)

    clean = [b for b in (_clean(x) for x in bars) if b is not None]
    n = len(clean)
    if n < MIN_BARS:
        return RejectionDefense(0.0, 0.0)

    range_high = max(b[1] for b in clean)
    range_low = min(b[2] for b in clean)
    span = range_high - range_low
    top_thr = range_high - TOP_FRAC * span   # near-high if high >= this
    low_thr = range_low + LOW_FRAC * span     # near-low if low <= this

    rejections = []
    resiliences = []
    for o, h, l, c in clean:
        rng = h - l
        if h >= top_thr:
            rejections.append((h - max(o, c)) / rng)   # upper-wick ratio
        if l <= low_thr:
            resiliences.append((c - l) / rng)          # close-location value

    score = _clamp(_mean(resiliences) - _mean(rejections), -1.0, 1.0)
    confidence = _clamp(n / FULL_CONF_BARS, 0.0, 1.0)
    return RejectionDefense(round(score, 4), round(confidence, 4))
