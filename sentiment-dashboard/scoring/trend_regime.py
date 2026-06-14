"""Market trend regime classifier.

Classifies the prevailing equity-market trend into one of five states
from SPY daily closes. Pure function — the hysteresis state machine
that smooths transitions lives in the app shell, not here.
"""
from dataclasses import dataclass
from typing import Sequence


# Decision-tree thresholds. Module-level so a backtest can sweep them.
BULL_DD_MAX = -5.0
PULLBACK_DD_MAX = -12.0
BEAR_RALLY_DD_MIN = -10.0
SLOPE_BULL_MIN = 0.05
SLOPE_BEAR_MAX = -0.05
SLOPE_WINDOW = 20

# Data sufficiency tiers.
MIN_BARS_FULL = 200
MIN_BARS_PARTIAL = 50

STATE_LABELS = {
    "bull_trend":       "Bull Trend",
    "pullback_in_bull": "Pullback in Bull",
    "range":            "Range",
    "bear_rally":       "Bear Rally",
    "bear_trend":       "Bear Trend",
}

STATE_DESCRIPTIONS = {
    "bull_trend":       "Healthy uptrend — favor long entries, full size.",
    "pullback_in_bull": "Buyable dip in an uptrend — long setups on bounce confirmation.",
    "range":            "Sideways market — fade extremes, sell premium.",
    "bear_rally":       "Counter-trend bounce in a downtrend — fade rallies, avoid chasing longs.",
    "bear_trend":       "Confirmed downtrend — reduce exposure, favor defensive/short setups.",
}


@dataclass(frozen=True)
class TrendRegimeResult:
    state: str
    label: str
    description: str
    spy_close: float
    sma_50: float
    sma_200: float
    sma_200_slope_pct: float
    drawdown_pct: float
    confidence: float


def _empty_result(spy_close: float = 0.0, confidence: float = 0.0) -> TrendRegimeResult:
    return TrendRegimeResult(
        state="range",
        label=STATE_LABELS["range"],
        description=STATE_DESCRIPTIONS["range"],
        spy_close=spy_close,
        sma_50=0.0,
        sma_200=0.0,
        sma_200_slope_pct=0.0,
        drawdown_pct=0.0,
        confidence=confidence,
    )


def classify(spy_closes: Sequence[float]) -> TrendRegimeResult:
    """Classify the prevailing trend regime from SPY daily closes.

    ``spy_closes`` is chronological (oldest first, latest last). Must
    contain at least ``MIN_BARS_FULL`` entries for a full-confidence
    call; ``MIN_BARS_PARTIAL`` to ``MIN_BARS_FULL`` returns ``range``
    with 0.5 confidence; anything below that returns 0.0.
    """
    closes = list(spy_closes)
    n = len(closes)
    if n < MIN_BARS_PARTIAL:
        return _empty_result(
            spy_close=closes[-1] if closes else 0.0,
            confidence=0.0,
        )
    if n < MIN_BARS_FULL:
        return _empty_result(spy_close=closes[-1], confidence=0.5)

    close = closes[-1]
    sma50 = sum(closes[-50:]) / 50.0
    sma200 = sum(closes[-200:]) / 200.0
    sma200_prev = sum(closes[-200 - SLOPE_WINDOW:-SLOPE_WINDOW]) / 200.0 \
        if n >= 200 + SLOPE_WINDOW else sma200
    slope_pct = ((sma200 - sma200_prev) / sma200_prev * 100.0) \
        if sma200_prev else 0.0
    window_252 = closes[-252:] if n >= 252 else closes
    peak = max(window_252)
    dd_pct = ((close - peak) / peak * 100.0) if peak else 0.0

    if close > sma50 > sma200 and slope_pct > SLOPE_BULL_MIN \
            and dd_pct > BULL_DD_MAX:
        state = "bull_trend"
    elif close <= sma50 and sma50 > sma200 and slope_pct > 0 \
            and dd_pct > PULLBACK_DD_MAX:
        state = "pullback_in_bull"
    elif close < sma50 < sma200 and slope_pct < SLOPE_BEAR_MAX:
        state = "bear_trend"
    elif close > sma50 and slope_pct < 0 and dd_pct < BEAR_RALLY_DD_MIN:
        state = "bear_rally"
    else:
        state = "range"

    return TrendRegimeResult(
        state=state,
        label=STATE_LABELS[state],
        description=STATE_DESCRIPTIONS[state],
        spy_close=close,
        sma_50=sma50,
        sma_200=sma200,
        sma_200_slope_pct=slope_pct,
        drawdown_pct=dd_pct,
        confidence=1.0,
    )


HYSTERESIS_DAYS = 2


def commit_state(raw: str, history: Sequence[str],
                 prev_committed: str | None) -> tuple[str, list[str]]:
    """Decide whether to flip the committed regime state.

    Parameters
    ----------
    raw
        The classifier's verdict for the current session.
    history
        Prior raw classifications, oldest first, at most
        ``HYSTERESIS_DAYS`` long.
    prev_committed
        The most recently committed state, or ``None`` on cold start.

    Returns
    -------
    (committed, new_history)
        ``committed`` is the state to publish. ``new_history`` is the
        updated rolling list (already trimmed to ``HYSTERESIS_DAYS``).
    """
    new_history = (list(history) + [raw])[-HYSTERESIS_DAYS:]
    if prev_committed is None:
        return raw, new_history
    if len(new_history) == HYSTERESIS_DAYS \
            and all(s == raw for s in new_history) \
            and raw != prev_committed:
        return raw, new_history
    return prev_committed, new_history
