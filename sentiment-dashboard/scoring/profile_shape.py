"""Volume-by-price profile shape classifier — NEUTRAL-state sharpener.

Pure functions: a list of intraday OHLCV dicts in, a frozen ProfileShape out
(no pandas, no tk, no I/O). Builds a volume-by-price histogram (each bar's
volume dropped at its typical price (h+l+c)/3), finds the point of control
(POC = the max-volume bin) and the ~70% value area, and classifies the session
into "balance" (one dominant central peak — price rotating around a single HVN,
which *boosts* the classifier's Neutral state), "trend" (POC skewed to an
extreme), or "double" (two separated high-volume nodes). Mirrors the
frozen-result / _clamp / confidence-in-[0,1] idiom of scoring/effort.py.

Thresholds (module constants, tuned for a defensible split):
- POC_DOMINANCE_MIN: POC bin must hold this volume fraction to count as a peak.
- VA_SPAN_MAX: value area must span <= this fraction of bins to be "narrow".
- CENTRAL_MAX: POC distance from the histogram center (0=center, 1=extreme).
- SECONDARY_FRAC / SEPARATION_MIN_FRAC / VALLEY_FRAC: a second node counts when
  it is >= SECONDARY_FRAC of POC volume, >= SEPARATION_MIN_FRAC of the bins away
  from the POC, with a valley between them dipping <= VALLEY_FRAC of that node.
"""
from __future__ import annotations
from dataclasses import dataclass

VA_TARGET = 0.70            # value-area volume fraction (Market Profile standard)
POC_DOMINANCE_MIN = 0.12    # POC bin volume fraction to qualify as a real peak
VA_SPAN_MAX = 0.55          # narrow value area (fraction of bins)
CENTRAL_MAX = 0.45          # POC within this normalized distance of center
SECONDARY_FRAC = 0.6        # a 2nd node's peak vs POC volume
SEPARATION_MIN_FRAC = 0.2   # ... min bin separation from POC (fraction of bins)
VALLEY_FRAC = 0.6           # ... valley between must dip below this * node volume
CONC_REF = 0.35             # poc_frac at which the concentration factor saturates
_REQUIRED = ("high", "low", "close", "volume")


@dataclass(frozen=True)
class ProfileShape:
    shape: str            # "balance" | "trend" | "double"
    balance_strength: float   # [0.0, 1.0]; 1 = tight central single peak
    neutral_signal: float     # == balance_strength (the classifier consumes this)


def _clamp(v, lo, hi):
    return float(max(lo, min(hi, v)))


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _typical_vol(bar):
    if not isinstance(bar, dict):
        return None
    vals = [_num(bar.get(k)) for k in _REQUIRED]
    if any(v is None for v in vals):
        return None
    h, l, c, v = vals
    if v <= 0:
        return None
    return ((h + l + c) / 3.0, v)


def _default():
    return ProfileShape("trend", 0.0, 0.0)


def classify_profile_shape(bars, n_bins=20) -> ProfileShape:
    if not bars or n_bins < 2:
        return _default()

    points = [p for p in (_typical_vol(b) for b in bars) if p is not None]
    if not points:
        return _default()

    prices = [p for p, _ in points]
    lo, hi = min(prices), max(prices)
    if hi <= lo or len({round(p, 6) for p in prices}) < 2:
        return _default()

    span = hi - lo
    hist = [0.0] * n_bins
    for price, vol in points:
        idx = int((price - lo) / span * n_bins)
        idx = 0 if idx < 0 else n_bins - 1 if idx >= n_bins else idx
        hist[idx] += vol

    total = sum(hist)
    if total <= 0:
        return _default()

    poc = max(range(n_bins), key=lambda i: hist[i])
    poc_vol = hist[poc]
    poc_frac = poc_vol / total

    # Value area: grow contiguously from the POC toward the richer neighbor.
    lo_i = hi_i = poc
    acc = poc_vol
    while acc < VA_TARGET * total:
        left = hist[lo_i - 1] if lo_i > 0 else -1.0
        right = hist[hi_i + 1] if hi_i < n_bins - 1 else -1.0
        if left < 0 and right < 0:
            break
        if right >= left:
            hi_i += 1
            acc += hist[hi_i]
        else:
            lo_i -= 1
            acc += hist[lo_i]
    va_span = (hi_i - lo_i + 1) / n_bins

    center = (n_bins - 1) / 2.0
    dist = abs(poc - center) / center if center > 0 else 0.0

    # Double: a second node far from the POC with a valley between.
    double = False
    sep_min = SEPARATION_MIN_FRAC * n_bins
    for i in range(n_bins):
        if abs(i - poc) >= sep_min and hist[i] >= SECONDARY_FRAC * poc_vol:
            a, b = (i, poc) if i < poc else (poc, i)
            between = hist[a + 1:b]
            valley = min(between) if between else min(hist[a], hist[b])
            if valley <= VALLEY_FRAC * hist[i]:
                double = True
                break

    balance = (poc_frac >= POC_DOMINANCE_MIN and va_span <= VA_SPAN_MAX
               and dist <= CENTRAL_MAX and not double)
    if balance:
        shape = "balance"
    elif double:
        shape = "double"
    else:
        shape = "trend"

    conc = _clamp((poc_frac - 0.05) / (CONC_REF - 0.05), 0.0, 1.0)
    centr = _clamp(1.0 - dist, 0.0, 1.0)
    narrow = _clamp(1.0 - va_span, 0.0, 1.0)
    # Centrality weighs as heavily as concentration so a narrow-but-off-center
    # (one-sided/trend) profile can't masquerade as balanced.
    strength = 0.4 * conc + 0.4 * centr + 0.2 * narrow
    if double:
        strength *= 0.3
    strength = round(_clamp(strength, 0.0, 1.0), 4)
    return ProfileShape(shape, strength, strength)
