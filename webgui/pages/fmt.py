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

# Digits after the decimal point for every price, strike, ratio, percentage and
# dollar total on a screen (2026-10-04): a whole-number price prints 450.00,
# never 450. Scores, ranks, counts, DTE and the Greeks are NOT in that family.
# A constant, not a config value: the browser-side table format
# (``ui_kit.DECIMAL_FORMAT``) and the Highcharts format strings follow it.
DECIMALS = 2


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


def signed_pct(v, nd=DECIMALS):
    """A percentage that always carries its sign (``+1.20%`` / ``-0.50%``).

    Empty string — not a dash — when there is no reading: these render inline in
    a sentence, where a stray em-dash reads as punctuation.
    """
    f = num(v)
    return "" if f is None else f"{'+' if f >= 0 else ''}{f:.{nd}f}%"


# ── the display families ────────────────────────────────────────────────────
# One function per KIND of reading, so a page says what the number is and this
# file says how it prints. Every one answers NO_READING for an absent reading.
# A page-local helper that needs a different absence (an empty string inside a
# sentence, a "$0" on a fresh account) keeps that contract and calls one of
# these for the number.

def plain(v):
    """A count, a day count, a score or a setting, printed as it is: ``5`` for
    5.0, ``2.5`` for 2.5. NOT for a price, strike, ratio, percentage or dollar
    total - those are the functions below. This is the only ``:g`` the pages
    may use (``tests/test_two_decimals_guard.py``), so choosing it is a
    statement that the number is none of those."""
    f = num(v)
    return NO_READING if f is None else f"{f:g}"


def price(v):
    """An underlying or option price, or a level: ``6,712.81`` / ``450.00``."""
    f = num(v)
    return NO_READING if f is None else f"{f:,.{DECIMALS}f}"


def strike(v):
    """A strike: ``450.00``. No thousands separator, because a strike sits in
    pairs (``5800.00/5795.00``) and beside a right (``5800.00 P``)."""
    f = num(v)
    return NO_READING if f is None else f"{f:.{DECIMALS}f}"


def strike_text(v, missing="?"):
    """A strike inside a strikes cell (``450.00/445.00``): ``missing`` for None,
    and text that is not a number passes through as it came."""
    if v is None:
        return missing
    return strike(v) if num(v) is not None else str(v)


def ratio(v):
    """A ratio or a multiple: ``1.50``. The page adds its own ``×``."""
    f = num(v)
    return NO_READING if f is None else f"{f:,.{DECIMALS}f}"


def pct(v, signed=False):
    """A percentage ALREADY in percent units: ``65.00%``, or ``+1.20%`` signed."""
    f = num(v)
    if f is None:
        return NO_READING
    return f"{f:+,.{DECIMALS}f}%" if signed else f"{f:,.{DECIMALS}f}%"


def money(v, signed=False):
    """A dollar total: ``$1,250.00`` / ``-$40.00``; ``signed`` adds the ``+``
    to a gain and leaves exactly flat unsigned (``$0.00``)."""
    f = num(v)
    if f is None:
        return NO_READING
    sign = "-" if f < 0 else ("+" if signed and f > 0 else "")
    return f"{sign}${abs(f):,.{DECIMALS}f}"


# Largest first. A figure that ROUNDS to 1000 of its unit steps up one unit, so
# 999,999 reads $1.00M and never $1000.00K.
_MONEY_UNITS = ((1e9, "B"), (1e6, "M"), (1e3, "K"), (1.0, ""))


def scaled(v):
    """``(text, suffix)`` for a magnitude: ``("1.20", "M")`` / ``("950.00", "")``.
    ``v`` is taken as a magnitude; the caller owns the sign and the currency."""
    a = abs(v)
    i = next((k for k, (size, _) in enumerate(_MONEY_UNITS) if a >= size),
             len(_MONEY_UNITS) - 1)
    if i > 0 and round(a / _MONEY_UNITS[i][0], DECIMALS) >= 1000:
        i -= 1
    size, suffix = _MONEY_UNITS[i]
    return f"{a / size:,.{DECIMALS}f}", suffix


def money_short(v, signed=False):
    """An abbreviated dollar total: ``$1.20M`` / ``$45.00K`` / ``$950.00``."""
    f = num(v)
    if f is None:
        return NO_READING
    sign = "-" if f < 0 else ("+" if signed and f > 0 else "")
    text, suffix = scaled(f)
    return f"{sign}${text}{suffix}"
