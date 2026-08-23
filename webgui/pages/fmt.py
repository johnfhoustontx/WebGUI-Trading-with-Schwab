"""Shared numeric coercion + display formatters for the pages.

These were written out by hand across the page modules — measured 2026-08-20 as
11 clone groups / 32 defs / ~123 lines of identical bodies, of which ``num``
alone accounted for six copies whose own docstring recorded the duplication.

The distinction the whole file turns on: **an absent reading and a zero are
different facts.** ``num`` returns None for "no reading" so callers can tell them
apart, and ``fixed`` renders the absence as an em-dash rather than ``0.00``,
which would claim a measurement that was never taken.
"""
import math

# The em-dash a display formatter shows for an ABSENT reading. Not "0.00", not
# "n/a" — one mark, used everywhere, meaning "nothing was measured".
NO_READING = "—"


def num(v):
    """``v`` as a float, or None for anything that isn't a real reading.

    ``bool`` is rejected AHEAD of the coercion because it subclasses ``int``:
    ``float(True)`` is 1.0, so a boolean would sail through every numeric guard
    and render as a rising trend. NaN is rejected because it is the one that
    ships silently — ``None`` announces itself with a TypeError, while every
    comparison against NaN returns False, so an unguarded NaN falls through to
    the falling branch and paints a scoreless row as confidently bearish.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def float_or(v, default=None):
    """``float(v)``, or ``default`` when it will not coerce.

    ⚠ PERMISSIVE, and deliberately different from :func:`num`: this preserves
    whatever ``float()`` produced, so a NaN or a bool passes straight through.
    It is the right helper when you have a sensible fallback for junk input and
    the value is about to be formatted or summed. When the question is "is this
    a real reading" — anything that feeds a comparison, a colour, or a
    direction — use :func:`num`, which answers None for NaN and bool. Four pages
    carried this body with three different defaults (consolidated 2026-08-20).
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def clamp(v, lo, hi):
    """``v`` bounded to ``[lo, hi]``."""
    return max(lo, min(hi, v))


def round_or_none(value, ndigits=2):
    """Round a real number; pass anything else through untouched.

    Bools pass through as themselves — ``round(True)`` is 1, and a flag turning
    into a number is the same class of bug ``num`` guards against.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(value, ndigits)


def fixed(v, nd=2):
    """``v`` to ``nd`` decimal places, or :data:`NO_READING` when there is none."""
    f = num(v)
    return NO_READING if f is None else f"{f:.{nd}f}"


def signed_pct(v, nd=1):
    """A percentage that always carries its sign (``+1.2%`` / ``-0.5%``).

    Empty string — not a dash — when there is no reading: these render inline in
    a sentence, where a stray em-dash reads as punctuation.
    """
    f = num(v)
    return "" if f is None else f"{'+' if f >= 0 else ''}{f:.{nd}f}%"
