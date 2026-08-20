"""Pure display language for the Bull / Bear Map (/sentiment/bullbear).

Two axes, NEVER blended. ``raw.trend`` is the annualised exp-regression slope of
log(close) scaled by R^2 — signed, absolute, benchmark-free. ``raw.excess`` is
excess return vs SPY — signed, relative. Their four combinations are the map, and
the fourth (falling but leading) is precisely what a relative-only screen paints
bullish. See docs/plans/2026-08-19-bull-bear-map-design.md.
"""
import math

QUADRANTS = ("rising_leading", "rising_lagging",
             "falling_leading", "falling_lagging", "unknown")


def _reading(v):
    """The value when it is a real number, else ``None`` — i.e. "no reading".

    ``bool`` is rejected first because it subclasses ``int``: ``isfinite(True)``
    is True and ``True > 0`` is True, so a boolean sails through every numeric
    guard and renders as a rising trend. Anything ``isfinite`` cannot interpret
    at all — a string from a malformed payload — is an absent reading too;
    ``unknown`` renders that absence, where raising would take the page build
    down with it. This is not a degrade that hides a bug: it reports "no data"
    instead of inventing a plausible number.
    """
    if isinstance(v, bool):
        return None
    try:
        return v if math.isfinite(v) else None
    except TypeError:
        return None


def quadrant(trend, excess):
    """Absolute trend x relative strength -> one of QUADRANTS.

    Ties go to the cautious side: a dead-flat trend is not "rising", and a zero
    excess is not "leading". A missing axis yields ``unknown`` rather than a
    default bucket — the cascade returns None for a series too short or too thin
    to score, and inventing a reading there is worse than showing none.

    A NaN carries that same meaning and so takes the same branch, but needs its
    own guard for a reason worth stating: ``None`` announces itself with a
    TypeError if you forget it, while every comparison against NaN silently
    returns False. Unguarded, ``nan > 0`` is False and a scoreless row would
    render as a confident *bearish* one — the failure CLAUDE.md records shipping
    twice in ``sentiment_svc``, where a NaN reaching ``min(hi, nan)`` returned
    ``hi`` and a data outage painted a maximum-confidence reading.
    """
    trend, excess = _reading(trend), _reading(excess)
    if trend is None or excess is None:
        return "unknown"
    if trend > 0:
        return "rising_leading" if excess > 0 else "rising_lagging"
    return "falling_leading" if excess > 0 else "falling_lagging"
