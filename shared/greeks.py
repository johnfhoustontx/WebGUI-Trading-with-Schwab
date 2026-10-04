"""Is this Greek a reading? One check for every tier that reads a Schwab chain.

Schwab sends ``-999.0`` for a Greek it could not compute, and a chain can carry
a NaN. Both are numbers to Python, so ``c.get("gamma") or 0`` takes either as a
reading: one ``-999`` gamma made a -1.8e12 exposure cell, one ``-999`` delta
fired a delta stop and put the book's net delta at -998.7, and one NaN delta
silenced every big-delta alert for its symbol (audit AC-09, AC-52).

Every function returns a ``float`` or ``None``. ``None`` means "no reading" and
the CALLER decides what that implies (skip the contract, fall back to a model,
keep the old value) - the same rule the NaN guards elsewhere in this repo
follow. Zero is a reading and comes back as ``0.0``, never ``None``.

Imports only ``math``, so ``options-scanner``, ``services`` and Tier 1 can all
use it.
"""
import math

# What Schwab sends for "not computed". Anything at or below SENTINEL_FLOOR is
# treated as that value: no option Greek this app reads is that negative.
SENTINEL = -999.0
SENTINEL_FLOOR = -900.0

# A delta is a fraction of one share. A hair over 1 is rounding; more is not a delta.
DELTA_LIMIT = 1.0001


def usable(value):
    """``value`` as a float when it is a real, finite number that is not
    Schwab's ``-999`` placeholder; else None. A bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if not math.isfinite(v) or v <= SENTINEL_FLOOR:
        return None
    return v


def delta(value):
    """A usable delta: between -1 and 1. Else None."""
    v = usable(value)
    if v is None or abs(v) > DELTA_LIMIT:
        return None
    return v


def gamma(value):
    """A usable gamma: zero or above. Else None."""
    v = usable(value)
    if v is None or v < 0:
        return None
    return v
