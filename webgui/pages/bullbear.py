"""Pure display language for the Bull / Bear Map (/sentiment/bullbear).

Two axes, NEVER blended. ``raw.trend`` is the annualised exp-regression slope of
log(close) scaled by R^2 — signed, absolute, benchmark-free. ``raw.excess`` is
excess return vs SPY — signed, relative. Their four combinations are the map, and
the fourth (falling but leading) is precisely what a relative-only screen paints
bullish.

Participation — the share of a group's constituents confirming a move — is a
third, INDEPENDENT dimension: a confidence qualifier drawn beside the quadrant,
never folded into it. Sector and industry rows carry it; stock rows do not.

The rule the module's two halves split on: DEGRADE where the fallback honestly
signals absence — "No reading", slate, an em dash — and RAISE where it would be
a confident false statement, as a count of zero is in ``headline``.

Everything here is pure so it can be pinned by ``tests/test_bullbear.py``
without a browser; ``pages/sentiment_bullbear.py`` holds only widgets and wiring.
See docs/plans/2026-08-19-bull-bear-map-design.md.
"""
import math

from pages.fmt import num as _num  # the ONE copy (pages/fmt.py)

QUADRANTS = ("rising_leading", "rising_lagging",
             "falling_leading", "falling_lagging", "unknown")


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


# ── the display language: labels, colours, formatting ───────────────────────
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

    Drift between the tables and QUADRANTS is caught instead by
    ``test_the_tables_key_exactly_on_quadrants_with_no_empty_entries``.
    """
    return _LABELS.get(q, _LABELS["unknown"])


def quadrant_class(q):
    """A quadrant key -> its chip classes; an unrecognised key degrades."""
    return _CLASSES.get(q, _CLASSES["unknown"])


# ── rows, counts and the headline ────────────────────────────────────────────
# A cell with no reading. An em dash, since absent and unchanged differ.
NO_READING = "—"


def signed_pct(v):
    """A percent -> ``"+1.25%"`` / ``"-0.56%"``, or NO_READING when absent.

    The sign is decided on the ROUNDED value, so anything displaying as zero
    renders unsigned — "+0.00%" and "-0.00%" both claim a move the digits deny.
    ``merge_live`` yields None only for a symbol the proxy OMITS; one returned
    with junk fields yields 0.0, so a flat cell is no proof of a flat tape.
    """
    pct = _num(v)
    if pct is None:
        return NO_READING
    pct = round(pct, 2) + 0.0    # +0.0 folds -0.0 onto 0.0
    return f"{pct:.2f}%" if pct == 0 else f"{pct:+.2f}%"


def _raw(row):
    """A row's ``raw`` block. Shape must be right, contents may be null: a None
    row or a null ``raw`` is a reading we do not have, while a non-dict row is a
    different document and raises rather than being half-rendered.
    """
    return (row or {}).get("raw") or {}


def row_axes(row):
    """A row's ``(trend, excess)``, both through ``_num`` — quadrant()'s input.

    Public because the map renders both numbers per row and the Desk strip calls
    ``quadrant`` per sector; without it two page modules hand-roll
    ``(row.get("raw") or {}).get("trend")`` and drift from ``_raw``'s policy.
    """
    raw = _raw(row)
    return _num(raw.get("trend")), _num(raw.get("excess"))


def quadrant_counts(rows):
    """{quadrant: n} over rows, every bucket present even at zero.

    Indexes directly rather than defensively. quadrant() is total, so a KeyError
    here means that invariant broke — where a setdefault would answer with a
    sixth bucket nobody named and a distribution that no longer sums to what the
    headline claims.
    """
    counts = {q: 0 for q in QUADRANTS}
    for row in rows or []:
        counts[quadrant(*row_axes(row))] += 1
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


# ── participation: the breadth bar ───────────────────────────────────────────
# At or below this share a move is thin — ties to the cautious side, as
# quadrant() reads them. A judgement, not a fitted number: measured 2026-08-19,
# Real Estate was RISING on 0.23 while Energy sat flat on 0.96, a third splits.
THIN_PARTICIPATION = 1.0 / 3.0


def _share(v):
    """``v`` as a 0..1 share, or None for anything that is not one.

    ``momentum.participation`` is ``above/usable``, so out of range is a broken
    contract, not a reading — and a clamped 1.4 would draw the full bar.
    """
    p = _num(v)
    return p if p is not None and 0.0 <= p <= 1.0 else None


def row_participation(row):
    """A row's participation share — the top-level field, a SIBLING of ``raw``.

    ``participation`` names two quantities in one row and the wrong one fails
    silently: this is the raw 0..1 share, while ``components["participation"]``
    is a within-level z-score, signed and unbounded (both set by
    ``sentiment_svc.compute._momentum_score_level``) — fed to ``breadth_width``
    the z-score costs every negative row its bar and mis-draws the rest.
    """
    return (row or {}).get("participation")


def breadth_width(participation):
    """A share -> its whole-percent bar width, or None when there is no bar.

    Two upstream causes, not one: a stock row carries no participation at all,
    and ``momentum.participation`` returns None when no member is usable, so a
    sector can too. The empty bar belongs to a genuine 0.0 — "nothing confirms".
    """
    p = _share(participation)
    return None if p is None else round(p * 100)


def breadth_is_thin(participation):
    """True when too few constituents confirm the move to trust it."""
    p = _share(participation)
    return p is not None and p <= THIN_PARTICIPATION


# ── the tree: sector -> industry -> stock ────────────────────────────────────
def _sort_key(row):
    """Strongest first, unscored last, otherwise stable in the given order.

    Ranks on ``raw.trend`` rather than the blended ``score``, so the order runs
    on the axis the map colours by and no row sits above a greener one. Through
    ``_num``: ``sorted`` is where a NaN orders unpredictably and sNaN raises.
    """
    trend = _num(_raw(row).get("trend"))
    return (1, 0.0) if trend is None else (0, -trend)


def by_strength(rows):
    """Any level's rows, strongest first, with nulls dropped -> a new list.

    Public so the Desk strip can order sectors without building a tree: two
    screens ordering the same rows differently is a defect neither shows. A null
    names neither itself nor a parent, so it goes the way of a row whose parent
    is unknown; a non-dict row still raises, through ``_raw`` in the sort key.
    """
    return sorted((r for r in rows or [] if r is not None), key=_sort_key)


def _level(levels, name):
    """One named level of a payload, ordered by :func:`by_strength`."""
    return by_strength(levels.get(name))


def build_tree(levels):
    """``levels{sector,industry,stock}`` -> nested sectors, strongest first.

    Each sector gains ``industries`` (each with its own ``stocks``) and
    ``orphan_stocks`` — constituents (5 of 296 on 2026-08-19) whose industry has
    no row, since ``sentiment_svc.compute`` scores an industry only when its ETF
    cleared ``_momentum_admit`` while every member stock is scored regardless.
    A row naming a parent that does not exist is DROPPED, never filed under an
    invented bucket that would put a phantom row in the counts — that module
    maps a stock in no scored industry to ``("", "")``, 10 of 296 that day.

    Nodes are shallow copies: Task 10 rebuilds the tree per lazy expansion off
    the same ``levels`` (a mutating build would accumulate ``industries`` across
    rebuilds), and one page build may pass these rows to ``quadrant_counts``.
    """
    levels = levels or {}
    by_sector, out = {}, []
    for row in _level(levels, "sector"):
        node = dict(row, industries=[], orphan_stocks=[])
        by_sector[row.get("label")] = node
        out.append(node)

    # (sector, label): an industry name is unique only within its sector.
    by_industry = {}
    for row in _level(levels, "industry"):
        parent = by_sector.get(row.get("sector"))
        if parent is None:
            continue
        node = dict(row, stocks=[])
        parent["industries"].append(node)
        by_industry[(row.get("sector"), row.get("label"))] = node

    for row in _level(levels, "stock"):
        parent = by_sector.get(row.get("sector"))
        if parent is None:
            continue
        industry = by_industry.get((row.get("sector"), row.get("industry")))
        bucket = industry["stocks"] if industry else parent["orphan_stocks"]
        bucket.append(dict(row))
    return out
