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


# The label names BOTH axes, always, because one word is the ambiguity this page
# exists to remove: "Falling / Leading" is a symbol whose price is dropping while
# it still beats SPY — losing money more slowly than the index — and any wording
# that collapses the two axes into a single verdict paints exactly that row as
# strength. The axes read in the key's own order, absolute trend then relative
# strength, so the eye finds the same quantity in the same place on every row.
_LABELS = {
    "rising_leading": "Rising · Leading",
    "rising_lagging": "Rising · Lagging",
    "falling_leading": "Falling · Leading",
    "falling_lagging": "Falling · Lagging",
    "unknown": "No reading",
}

# A fixed, finite palette of static classes, per the house Tailwind-first rule
# that a data-driven colour maps from a known finite set onto a written-out
# class. The reason is vocabulary hygiene — one deduped, greppable set of colours
# instead of magic hexes scattered down the module — and NOT that the alternative
# fails to render. NiceGUI bundles the Tailwind browser JIT, and CLAUDE.md records
# as a measured finding that it DOES generate a runtime-built arbitrary class;
# the sibling rotation_view builds its entire quadrant vocabulary from f-strings
# and paints correctly. Literals are right HERE because these are Tailwind's own
# named scale steps, so the literals are the source and interpolating them would
# add indirection over nothing. rotation_view interpolates because its colours
# are computed by oklch math from the QUAD_HUE/QUAD_CHROMA root that rrg_view and
# momentum_view import, where typing them out would copy that palette into a
# second place and let the three screens drift apart. Same rule, opposite
# spelling, because the source of the colour differs.
# The ramp runs emerald (rising and leading — strength on both axes)
# through a dimmed emerald and amber for the two mixed states to rose (weak on
# both). Amber, the caution colour, is deliberately the falling-but-leading one:
# it is the trap quadrant, the row a relative-strength-only screen calls a buy.
# Unknown takes slate so a scoreless row cannot borrow a direction — grey reads
# as an absence, whereas green or red reads as a call the data never made.
_CLASSES = {
    "rising_leading": "text-emerald-300 bg-emerald-400/15 border-emerald-400/30",
    "rising_lagging": "text-emerald-200/80 bg-emerald-400/5 border-emerald-400/15",
    "falling_leading": "text-amber-300 bg-amber-400/10 border-amber-400/25",
    "falling_lagging": "text-rose-300 bg-rose-400/15 border-rose-400/30",
    "unknown": "text-slate-400 bg-slate-400/10 border-slate-400/20",
}


def quadrant_label(q):
    """A quadrant key -> its display name, degrading to the no-reading label.

    The fallback is unreachable from the intended caller — ``quadrant`` is total
    and only ever returns a member of QUADRANTS — and it is still a quiet degrade
    rather than a KeyError, because this runs inside a page build, where raising
    forfeits the whole page to spare one row. "No reading" is the honest thing to
    render for a key that means nothing: it shows the absence instead of picking
    a direction, which is the same trade ``_num`` makes one layer down.

    The cost of degrading quietly is real, though, and is NOT waved away here: a
    quadrant added to QUADRANTS without a label would render "No reading" for
    every row of its kind — plausible, silent, wrong. That is paid for at test
    time, where ``test_the_label_and_class_tables_key_exactly_on_quadrants``
    fails by name, rather than by making the render path brittle at runtime.
    """
    return _LABELS.get(q, _LABELS["unknown"])


def quadrant_class(q):
    """A quadrant key -> its chip classes, degrading to the no-reading style.

    Total, and guarded at test time, for the reasons set out on
    ``quadrant_label``. Note both lookups are total over any *hashable* key and
    no further: a quadrant key is produced internally by ``quadrant``, unlike the
    payload numbers ``quadrant`` itself takes, which arrive from the cascade and
    so earn the fuller guard in ``_num``.
    """
    return _CLASSES.get(q, _CLASSES["unknown"])
