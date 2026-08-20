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
    ``float(True)`` is 1.0, so a boolean would sail through every numeric guard
    and render as a rising trend. NaN is rejected because it is the one that
    ships silently — ``None`` announces itself with a TypeError, while every
    comparison against NaN returns False, so an unguarded NaN trend falls through
    to the falling branch and paints a scoreless row as confidently bearish.

    Byte-identical to the ``_num`` in ``sector_heat`` / ``rotation_view`` /
    ``rrg_view`` / ``momentum_view``, the sibling modules over this same payload.
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

    Ties go to the cautious side: a flat trend is not "rising", a zero excess is
    not "leading". A missing axis yields ``unknown`` rather than a default
    bucket, since the cascade scores None for a series too thin to read.
    """
    trend, excess = _num(trend), _num(excess)
    if trend is None or excess is None:
        return "unknown"
    if trend > 0:
        return "rising_leading" if excess > 0 else "rising_lagging"
    return "falling_leading" if excess > 0 else "falling_lagging"


# The label names BOTH axes because one word is the ambiguity this page exists to
# remove: "Falling / Leading" is a symbol dropping while it still beats SPY, and
# any single-word verdict paints exactly that row as strength. Absolute axis
# first, so the eye finds the same quantity in the same place on every row.
_LABELS = {
    "rising_leading": "Rising · Leading",
    "rising_lagging": "Rising · Lagging",
    "falling_leading": "Falling · Leading",
    "falling_lagging": "Falling · Lagging",
    "unknown": "No reading",
}

# A fixed finite palette of static classes: five literals keep the colour
# vocabulary deduped and greppable. The ramp runs emerald (strong on both axes)
# through a dimmed emerald and amber to rose (weak on both). Amber is
# deliberately the falling-but-leading state — the trap quadrant, the row a
# relative-strength-only screen calls a buy. Unknown takes slate so a scoreless
# row cannot borrow a direction it never earned.
_CLASSES = {
    "rising_leading": "text-emerald-300 bg-emerald-400/15 border-emerald-400/30",
    "rising_lagging": "text-emerald-200/80 bg-emerald-400/5 border-emerald-400/15",
    "falling_leading": "text-amber-300 bg-amber-400/10 border-amber-400/25",
    "falling_lagging": "text-rose-300 bg-rose-400/15 border-rose-400/30",
    "unknown": "text-slate-400 bg-slate-400/10 border-slate-400/20",
}


def quadrant_label(q):
    """A quadrant key -> its display name; an unrecognised key degrades.

    The module's rule, since its two halves differ: degrade where the fallback
    honestly signals absence — "No reading", slate — and raise where it would be
    a confident false statement, as a count of zero is in ``headline``. Table
    drift is caught instead by
    ``test_the_tables_key_exactly_on_quadrants_with_no_empty_entries``.
    """
    return _LABELS.get(q, _LABELS["unknown"])


def quadrant_class(q):
    """A quadrant key -> its chip classes; an unrecognised key degrades."""
    return _CLASSES.get(q, _CLASSES["unknown"])


def _raw(row):
    """A row's ``raw`` block. Shape must be right, contents may be null: a None
    row or a null ``raw`` is a reading we do not have, while a non-dict row is a
    different document and raises rather than being half-rendered.
    """
    return (row or {}).get("raw") or {}


def quadrant_counts(rows):
    """{quadrant: n} over rows, every bucket present even at zero.

    Indexes directly rather than defensively. quadrant() is total, so a KeyError
    here means that invariant broke — where a setdefault would answer with a
    sixth bucket nobody named and a distribution that no longer sums to what the
    headline claims.
    """
    counts = {q: 0 for q in QUADRANTS}
    for row in rows or []:
        raw = _raw(row)
        counts[quadrant(raw.get("trend"), raw.get("excess"))] += 1
    return counts


def headline(counts, noun):
    """"5 of 11 sectors rising and leading" — a fact, deliberately not a verdict
    (see the design doc), and empty when there is nothing to count.

    "0 of 0 sectors rising and leading" would state a maximally bearish tape
    where in fact nothing was published. ``noun`` renders verbatim, so the caller
    owns pluralisation. On the numerator not degrading, see ``quadrant_label``.
    """
    total = sum(counts.values())
    if not total:
        return ""
    return f"{counts['rising_leading']} of {total} {noun} rising and leading"


# At or below this share a move is thin — ties to the cautious side, as
# quadrant() reads them. A judgement, not a fitted number: measured 2026-08-19,
# Real Estate was RISING on 0.23 while Energy sat flat on 0.96, a third splits.
THIN_PARTICIPATION = 1.0 / 3.0


def _share(v):
    """``v`` as a 0..1 share, or None for anything that is not one.

    Out of range is a payload bug, not a reading to clamp back into view:
    participation is ``above/usable``, and a clamped 1.4 draws the full bar
    meaning "every member confirms" — this bar's own verdict, inverted silently.
    """
    p = _num(v)
    return p if p is not None and 0.0 <= p <= 1.0 else None


def breadth_width(participation):
    """A share -> its whole-percent bar width, or None when there is no bar.

    None rather than 0 for a stock row: one name has no constituents, and the
    empty bar belongs to a genuine 0.0, which says "nothing confirms".
    """
    p = _share(participation)
    return None if p is None else round(p * 100)


def breadth_is_thin(participation):
    """True when too few constituents confirm the move to trust it."""
    p = _share(participation)
    return p is not None and p <= THIN_PARTICIPATION
