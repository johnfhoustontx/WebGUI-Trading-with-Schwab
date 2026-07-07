"""Five-state market classifier — direction × aggression grid.

Pure function (no tk, no I/O). Crosses a DIRECTION score (0-100, the
directional intraday trend score of ``scoring/intraday_trend.py``) with a
signed AGGRESSION score (-1..1, effort/urgency behind the move) and maps the
pair to one of five trader states. The two middle states capture the
effort-vs-result asymmetry a single directional axis can't express: price up
but hollow -> lack of bullishness; price down but no follow-through -> lack of
bearishness. Mirrors the vocabulary/dataclass idiom of
``scoring/trend_regime.py``.
"""
from __future__ import annotations
from dataclasses import dataclass


# Tunable band thresholds. Module-level so a backtest can sweep them.
DIR_BULL_MIN = 60.0     # direction_score >= -> bullish band
DIR_BEAR_MAX = 40.0     # direction_score <= -> bearish band; between -> neutral
AGG_POS = 0.2           # aggression >= -> strong positive
AGG_NEG = -0.2          # aggression <= -> strong negative; between -> weak/absent


STATE_LABELS = {
    "bullish":              "Bullish",
    "lack_of_bullishness":  "Lack of Bullishness",
    "neutral":              "Neutral",
    "lack_of_bearishness":  "Lack of Bearishness",
    "bearish":              "Bearish",
}

STATE_DESCRIPTIONS = {
    "bullish":              "Motivated buying absorbing supply — favor long/PCS, full size.",
    "lack_of_bullishness":  "Buyers exhausted at highs — favor CCS, trim longs.",
    "neutral":              "Balance — sell premium (iron condors/straddles), fade extremes.",
    "lack_of_bearishness":  "Refuses to drop, puts cheap/undefended — favor PCS.",
    "bearish":              "Urgent selling/liquidation — favor debit puts, stand down on credit.",
}


@dataclass(frozen=True)
class MarketState:
    state: str
    label: str
    description: str
    evidence: tuple


# (direction_band, aggression_sign) -> state. The centerpiece 9-cell grid.
_GRID = {
    ("bullish", "pos"):  "bullish",
    ("bullish", "weak"): "lack_of_bullishness",
    ("bullish", "neg"):  "lack_of_bullishness",
    ("neutral", "pos"):  "lack_of_bearishness",
    ("neutral", "weak"): "neutral",
    ("neutral", "neg"):  "lack_of_bullishness",
    ("bearish", "pos"):  "lack_of_bearishness",
    ("bearish", "weak"): "lack_of_bearishness",
    ("bearish", "neg"):  "bearish",
}


def _direction_band(direction_score) -> str:
    try:
        d = float(direction_score)
    except (TypeError, ValueError):
        return "neutral"
    if d >= DIR_BULL_MIN:
        return "bullish"
    if d <= DIR_BEAR_MAX:
        return "bearish"
    return "neutral"


def _aggression_sign(aggression) -> str:
    try:
        a = float(aggression)
    except (TypeError, ValueError):
        return "weak"
    if a >= AGG_POS:
        return "pos"
    if a <= AGG_NEG:
        return "neg"
    return "weak"


def classify_market_state(direction_score, aggression, evidence=None) -> MarketState:
    """Cross the direction band with the aggression sign into a market state.

    ``direction_score`` is 0-100 (50 neutral); ``aggression`` is signed -1..1.
    Non-numeric / None inputs are treated as neutral / weak (never raises).
    ``evidence`` (optional iterable of strings) passes through unchanged.
    """
    band = _direction_band(direction_score)
    sign = _aggression_sign(aggression)
    state = _GRID[(band, sign)]
    return MarketState(
        state=state,
        label=STATE_LABELS[state],
        description=STATE_DESCRIPTIONS[state],
        evidence=tuple(evidence or ()),
    )
