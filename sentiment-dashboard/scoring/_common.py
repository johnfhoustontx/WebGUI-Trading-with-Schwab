"""Shared pure helpers used by scoring modules. No tkinter imports."""
from typing import Iterable, Tuple


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
