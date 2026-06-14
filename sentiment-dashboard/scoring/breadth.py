"""Market breadth scoring. Pure function lifted from sentiment_dashboard.py."""
import math
from typing import Optional

from .types import ScoreResult
from ._common import safe_float, score_from_thresholds

BREADTH_THRESHOLDS = [(75, 10), (65, 8), (55, 6), (45, 5), (35, 3), (0, 1)]
NET_AD_THRESHOLDS = [(1500, 10), (1000, 9), (500, 8), (200, 7), (100, 6),
                     (-100, 5), (-200, 4), (-500, 3), (-1000, 2), (-9999, 1)]


def score(pct_above_50: str = "",
          nyse_ad: str = "Neutral",
          new_highs: float = 0.0,
          new_lows: float = 0.0,
          breadth_ratio: Optional[float] = None,
          breadth_numeric: Optional[float] = None) -> ScoreResult:
    """Score market breadth using A/D ratio (primary), %>50DMA, highs/lows.

    Mirrors ``calculate_breadth_score`` from sentiment_dashboard.py.
    Returns a 1..10 score or 0 if no data, with sqrt-weighted confidence
    over the four primary fields.
    """
    pct_raw = (pct_above_50 or "").strip()
    has_ratio = breadth_ratio is not None and breadth_ratio not in (0,)
    has_net = breadth_numeric is not None and breadth_numeric != 0

    if (not pct_raw and not has_ratio and not has_net
            and nyse_ad == "Neutral" and new_highs == 0 and new_lows == 0):
        return ScoreResult(score=0, confidence=0.0,
                           interp="No breadth data entered")

    fields_present = sum([
        1 if has_ratio else 0,
        1 if pct_raw else 0,
        1 if new_highs > 0 else 0,
        1 if new_lows > 0 else 0,
    ])
    confidence = (fields_present / 4.0) ** 0.5

    ratio_score = None
    if has_ratio:
        r = breadth_ratio
        if r == math.inf or r >= 4.0: ratio_score = 10
        elif r >= 3.0: ratio_score = 9
        elif r >= 2.0: ratio_score = 8
        elif r >= 1.5: ratio_score = 7
        elif r >= 1.15: ratio_score = 6
        elif r >= 0.87: ratio_score = 5
        elif r >= 0.67: ratio_score = 4
        elif r >= 0.50: ratio_score = 3
        elif r >= 0.33: ratio_score = 2
        else: ratio_score = 1

    dma_score = None
    pct = None
    if pct_raw:
        pct = safe_float(pct_raw)
        dma_score = score_from_thresholds(pct, BREADTH_THRESHOLDS, ascending=True)

    if ratio_score is not None and dma_score is not None:
        s = round(ratio_score * 0.7 + dma_score * 0.3)
    elif ratio_score is not None:
        s = ratio_score
    elif dma_score is not None:
        s = dma_score
    elif has_net:
        s = score_from_thresholds(breadth_numeric, NET_AD_THRESHOLDS, ascending=True)
    else:
        s = {"Advancing": 7, "Declining": 3}.get(nyse_ad, 5)

    if new_highs > 0 and new_lows > 0:
        hl = new_highs / new_lows
        if hl > 3.0: s = min(10, s + 1)
        elif hl < 0.5: s = max(1, s - 1)

    s = max(1, min(10, s))

    parts = []
    if has_ratio:
        r_str = "∞:1" if breadth_ratio == math.inf else f"{breadth_ratio:.2f}:1"
        parts.append(f"A/D {r_str}")
    if pct is not None:
        parts.append(f"{pct:.0f}% >50d")
    if has_net and not has_ratio:
        parts.append(f"Net {breadth_numeric:+.0f}")
    summary = ', '.join(parts) if parts else "no data"
    if s >= 8:
        interp = f"{summary} — strong breadth, bullish"
    elif s >= 5:
        interp = f"{summary} — normal breadth"
    else:
        interp = f"{summary} — weak breadth, bearish"
    return ScoreResult(score=s, confidence=confidence, interp=interp)
