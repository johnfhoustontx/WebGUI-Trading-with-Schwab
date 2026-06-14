"""Shared dataclasses for the scoring package."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreResult:
    """Result returned by every scoring module.

    Attributes
    ----------
    score : int
        Integer 1..10 (1 = bearish, 10 = bullish) or 0 when undefined.
    confidence : float
        Data-quality confidence in [0.0, 1.0].
    interp : str
        Optional human-readable interpretation. Empty by default.
    """
    score: int
    confidence: float
    interp: str = ""
