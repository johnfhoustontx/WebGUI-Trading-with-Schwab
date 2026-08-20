"""Pure display language for the Bull / Bear Map (/sentiment/bullbear).

Two axes, NEVER blended. ``raw.trend`` is the annualised exp-regression slope of
log(close) scaled by R^2 — signed, absolute, benchmark-free. ``raw.excess`` is
excess return vs SPY — signed, relative. Their four combinations are the map, and
the fourth (falling but leading) is precisely what a relative-only screen paints
bullish. Everything here is pure so it can be pinned by ``tests/test_bullbear.py``
without a browser; ``pages/sentiment_bullbear.py`` holds only widgets and wiring.
See docs/plans/2026-08-19-bull-bear-map-design.md.
"""
import math

QUADRANTS = ("rising_leading", "rising_lagging",
             "falling_leading", "falling_lagging", "unknown")


def _num(v):
    """``v`` as a float, or None for anything that isn't a real reading.

    ``bool`` is rejected ahead of the coercion because it subclasses ``int``:
    ``float(True)`` is 1.0 and ``True > 0`` is True, so a boolean would sail
    through every numeric guard and render as a rising trend. A NaN is rejected
    for the reason worth stating, since it is the one that ships silently:
    ``None`` announces itself with a TypeError if you forget it, while every
    comparison against NaN quietly returns False, so an unguarded NaN trend falls
    through to the falling branch and paints a scoreless row as confidently
    bearish — the failure CLAUDE.md records shipping twice in ``sentiment_svc``,
    where a NaN reaching ``min(hi, nan)`` returned ``hi`` and a data outage
    rendered as a maximum-confidence reading. A value ``float()`` cannot read at
    all is an absent reading too, so it degrades rather than raising inside a
    page build — that reports "no data" instead of inventing a plausible number.

    Byte-identical to the ``_num`` in ``sector_heat`` / ``rotation_view`` /
    ``rrg_view`` / ``momentum_view``, the sibling modules over this same payload.
    Two notions of "is this a reading" between adjacent screens is how they end
    up disagreeing about identical data.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def quadrant(trend, excess):
    """Absolute trend x relative strength -> one of QUADRANTS.

    Ties go to the cautious side: a dead-flat trend is not "rising", and a zero
    excess is not "leading". An axis with no reading yields ``unknown`` rather
    than a default bucket — the cascade returns None for a series too short or
    too thin to score, and inventing a reading there is worse than showing none.
    """
    trend, excess = _num(trend), _num(excess)
    if trend is None or excess is None:
        return "unknown"
    if trend > 0:
        return "rising_leading" if excess > 0 else "rising_lagging"
    return "falling_leading" if excess > 0 else "falling_lagging"
