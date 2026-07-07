"""Volume-effort score (signed, [-1, 1]) — effort-vs-result confirmation.

Pure functions: a list of daily OHLCV dicts in, a frozen EffortResult out (no
pandas, no tk, no I/O). One input to the five-state classifier's *aggression*
axis — does volume confirm price? Positive = motivated buying (volume backs
up-moves, dips come on no supply); negative = a hollow rally or urgent selling.
Distinguishes "Lack of Bullishness" (price up but volume drying up) from "Lack
of Bearishness" (price down but volume drying up). Mirrors the frozen-result /
_clamp / confidence-in-[0,1] idiom of scoring/intraday_trend.py.
"""
from __future__ import annotations
from dataclasses import dataclass
import math

MIN_BARS = 10          # below this -> confidence 0.0
WINDOW = 20            # trailing bars the sub-signals read
FULL_CONF_BARS = 30    # bar count at which confidence saturates
_REQUIRED = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class EffortResult:
    score: float          # signed [-1.0, 1.0]
    confidence: float     # [0.0, 1.0]
    components: dict       # signed sub-signals: updown_vol, vol_trend, clv


def _clamp(v, lo, hi):
    return float(max(lo, min(hi, v)))


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _clean(bar):
    if not isinstance(bar, dict):
        return None
    vals = [_num(bar.get(k)) for k in _REQUIRED]
    if any(v is None for v in vals):
        return None
    return tuple(vals)   # (open, high, low, close, volume)


def _updown_vol(win):
    """Mean up-day volume vs mean down-day volume -> signed via log-ratio
    (a 2.5x edge saturates to +/-1). Neither side present -> 0.0."""
    up = [v for v, d in win if d > 0]
    dn = [v for v, d in win if d < 0]
    mu, md = _mean(up), _mean(dn)
    if not mu or not md or mu <= 0 or md <= 0:
        return 0.0
    return _clamp(math.log(mu / md) / math.log(2.5), -1.0, 1.0)


def _vol_trend(win):
    """Is up-day volume expanding while down-day volume contracts (positive),
    or drying up on up-days (negative)? Split the window and diff the slopes."""
    half = len(win) // 2
    early, late = win[:half], win[half:]

    def cat(sub, sign):
        return _mean([v for v, d in sub if d * sign > 0])

    eu, lu = cat(early, 1), cat(late, 1)
    ed, ld = cat(early, -1), cat(late, -1)
    up_slope = (lu - eu) / (lu + eu) if (eu and lu and lu + eu > 0) else 0.0
    dn_slope = (ld - ed) / (ld + ed) if (ed and ld and ld + ed > 0) else 0.0
    return _clamp(up_slope - dn_slope, -1.0, 1.0)


def _clv(bars):
    """Mean close-location-value mapped to [-1, 1]. Zero-range bars skipped."""
    clvs = []
    for o, h, l, c, v in bars:
        rng = h - l
        if rng <= 0:
            continue
        clvs.append((c - l) / rng)
    m = _mean(clvs)
    if m is None:
        return 0.0
    return _clamp(m * 2.0 - 1.0, -1.0, 1.0)


def score_effort(daily_ohlcv) -> EffortResult:
    zero = {"updown_vol": 0.0, "vol_trend": 0.0, "clv": 0.0}
    if not daily_ohlcv:
        return EffortResult(0.0, 0.0, dict(zero))

    bars = [b for b in (_clean(x) for x in daily_ohlcv) if b is not None]
    n = len(bars)
    if n < MIN_BARS:
        return EffortResult(0.0, 0.0, dict(zero))

    closes = [b[3] for b in bars]
    vols = [b[4] for b in bars]
    dirs = [0]
    for i in range(1, n):
        dc = closes[i] - closes[i - 1]
        dirs.append(1 if dc > 0 else -1 if dc < 0 else 0)

    w = min(WINDOW, n)
    idx = list(range(n - w, n))
    win = [(vols[i], dirs[i]) for i in idx]

    updown = _updown_vol(win)
    voltrend = _vol_trend(win)
    clv = _clv([bars[i] for i in idx])

    comps = {"updown_vol": round(updown, 4),
             "vol_trend": round(voltrend, 4),
             "clv": round(clv, 4)}
    score = _clamp((updown + voltrend + clv) / 3.0, -1.0, 1.0)
    confidence = _clamp(n / FULL_CONF_BARS, 0.0, 1.0)
    return EffortResult(round(score, 4), round(confidence, 4), comps)
