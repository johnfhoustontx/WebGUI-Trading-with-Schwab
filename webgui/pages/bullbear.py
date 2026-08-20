"""Pure display language for the Bull / Bear Map (/sentiment/bullbear).

Two axes, NEVER blended. ``raw.trend`` is the annualised exp-regression slope of
log(close) scaled by R^2 — signed, absolute, benchmark-free. ``raw.excess`` is
excess return vs SPY — signed, relative. Their four combinations are the map, and
the fourth (falling but leading) is precisely what a relative-only screen paints
bullish. See docs/plans/2026-08-19-bull-bear-map-design.md.
"""

QUADRANTS = ("rising_leading", "rising_lagging",
             "falling_leading", "falling_lagging", "unknown")


def quadrant(trend, excess):
    """Absolute trend x relative strength -> one of QUADRANTS.

    Ties go to the cautious side: a dead-flat trend is not "rising", and a zero
    excess is not "leading". A missing axis yields ``unknown`` rather than a
    default bucket — the cascade returns None for a series too short or too thin
    to score, and inventing a reading there is worse than showing none.
    """
    if trend is None or excess is None:
        return "unknown"
    if trend > 0:
        return "rising_leading" if excess > 0 else "rising_lagging"
    return "falling_leading" if excess > 0 else "falling_lagging"
