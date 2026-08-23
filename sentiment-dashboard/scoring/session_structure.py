"""Session-structure score (signed, [-1, 1]) — intraday DIRECTION refinement.

Pure functions: a list of today's intraday OHLCV dicts in, a frozen
SessionStructure out (no pandas, no tk, no I/O). One input to the five-state
classifier's *direction* axis — is price holding constructively above the
session VWAP and clearing the opening range (bullish structure, positive), or
pinned below both (bearish, negative)? Mirrors the frozen-result / _clamp /
confidence-in-[0,1] idiom of scoring/effort.py.
"""
from __future__ import annotations
from dataclasses import dataclass
from ._common import clamp as _clamp, num as _num

VWAP_WEIGHT = 0.6      # blend weight of the VWAP-hold sub-signal
OR_WEIGHT = 0.4        # blend weight of the opening-range-break sub-signal
FULL_CONF_BARS = 30    # bar count at which confidence saturates
_REQUIRED = ("high", "low", "close", "volume")


@dataclass(frozen=True)
class SessionStructure:
    score: float          # signed [-1.0, 1.0]; + = bullish structure
    confidence: float     # [0.0, 1.0]


def _clean(bar):
    if not isinstance(bar, dict):
        return None
    vals = [_num(bar.get(k)) for k in _REQUIRED]
    if any(v is None for v in vals):
        return None
    return tuple(vals)   # (high, low, close, volume)


def _vwap_signal(bars):
    """Fraction of bars closing above the running cumulative VWAP, mapped to
    [-1, 1]. Bars before any volume has accumulated (VWAP undefined) are not
    counted; no valid bars -> 0.0."""
    cum_pv = 0.0
    cum_v = 0.0
    above = 0
    valid = 0
    for h, l, c, v in bars:
        typ = (h + l + c) / 3.0
        cum_pv += typ * v
        cum_v += v
        if cum_v > 0:
            valid += 1
            if c > cum_pv / cum_v:
                above += 1
    if valid == 0:
        return 0.0
    return _clamp((above / valid) * 2.0 - 1.0, -1.0, 1.0)


def _or_signal(bars, or_bars):
    """Latest close vs the opening range (first `or_bars` bars): +1 above the
    OR high, -1 below the OR low, else its linear position inside."""
    opening = bars[:or_bars]
    or_high = max(b[0] for b in opening)
    or_low = min(b[1] for b in opening)
    close = bars[-1][2]
    if or_high <= or_low:
        return 0.0
    if close > or_high:
        return 1.0
    if close < or_low:
        return -1.0
    mid = (or_high + or_low) / 2.0
    half = (or_high - or_low) / 2.0
    return _clamp((close - mid) / half, -1.0, 1.0)


def score_session_structure(bars, or_bars=6) -> SessionStructure:
    if not bars:
        return SessionStructure(0.0, 0.0)

    clean = [b for b in (_clean(x) for x in bars) if b is not None]
    n = len(clean)
    if n < or_bars:
        return SessionStructure(0.0, 0.0)

    vwap_sig = _vwap_signal(clean)
    or_sig = _or_signal(clean, or_bars)
    score = _clamp(VWAP_WEIGHT * vwap_sig + OR_WEIGHT * or_sig, -1.0, 1.0)
    confidence = _clamp(n / FULL_CONF_BARS, 0.0, 1.0)
    return SessionStructure(round(score, 4), round(confidence, 4))
