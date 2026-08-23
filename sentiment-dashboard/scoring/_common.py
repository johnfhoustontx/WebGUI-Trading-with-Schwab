"""Shared pure helpers used by scoring modules. No tkinter imports."""
from typing import Iterable, Tuple


def clamp(v, lo, hi) -> float:
    """Bound ``v`` to [lo, hi] as a float.

    Byte-identical to the NINE private ``_clamp`` copies this replaced (measured
    2026-08-21 by AST, docstrings stripped).

    NOTE what this deliberately does NOT do: ``min(hi, nan)`` returns ``hi``, so
    a non-finite value still pins the HIGH bound. Root CLAUDE.md warns against
    "fixing" that here, and it is right to - a NaN reaching
    ``clamp(50 + 50*direction, 0, 100)`` means "neutral 50", reaching
    ``clamp(adx/40, 0.3, 1.0)`` means "floor the magnitude", and reaching
    ``clamp(n_timeframes/3, 0, 1)`` means "confidence 0". Only the CALLER knows
    which, so guard the inputs at the call site (that is what ``num`` is for).
    """
    return float(max(lo, min(hi, v)))


def num(x):
    """A usable float, or ``None`` when the input is missing OR non-finite.

    Byte-identical to the six private ``_num`` copies this replaced, and
    behaviourally identical to ``market_regime``'s differently-spelled seventh.

    The non-finite rejection is the load-bearing half: several of these scorers
    shipped a ``_num`` that caught ``TypeError``/``ValueError`` but let a NaN
    through, so a single NaN reached ``clamp`` and pinned a bound - one NaN
    volume once took effort's ``updown_vol`` from 0.0039 to 1.0 at unchanged
    confidence 1.0.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):   # NaN / inf -> missing
        return None
    return v


def safe_float(value, default: float = 0.0) -> float:
    """Coerce ``value`` to float, returning ``default`` on failure / empty.

    Mirrors the helper in ``sentiment_dashboard.py``; kept here so scoring
    modules stay UI-free.
    """
    try:
        return float(value) if value else default
    except (ValueError, TypeError):
        return default


def score_from_thresholds(value: float,
                          thresholds: Iterable[Tuple[float, int]],
                          ascending: bool = True) -> int:
    """Map a numeric value to a 1..10 score via descending-threshold table.

    The threshold list is ordered from highest to lowest; the first
    entry whose ``thresh`` boundary matches the value (per ``ascending``)
    wins. Matches the behavior of the original helper in
    ``sentiment_dashboard.py``.
    """
    thresholds = list(thresholds)
    if ascending:
        for thresh, score in thresholds:
            if value >= thresh:
                return score
        return thresholds[-1][1]
    else:
        for thresh, score in thresholds:
            if value < thresh:
                return score
        return thresholds[-1][1]
