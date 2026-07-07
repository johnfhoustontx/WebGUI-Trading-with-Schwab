"""Daily direction-score proxy + committed-state reconstruction + forward-return
metrics for the offline five-state-classifier validation study.

Pure functions (no tk, no pandas, no I/O, no shared.analysis_lib). Self-contained
math over a list of ``{open, high, low, close, volume}`` dicts (oldest first),
mirroring the frozen-result / ``_clamp`` / JSON-safe-float idiom of
``scoring/effort.py``.

The live five-state classifier crosses an intraday 0-100 DIRECTION score with a
signed AGGRESSION score. For a historical backtest over daily SPY bars there is
no intraday tape, so ``daily_direction_score`` is a DAILY PROXY for that live
intraday direction blend — an EMA-stack / slope / RSI composite mapped to the
same 0-100 scale. ``reconstruct_state_series`` reuses the REAL sibling scoring
modules (effort, rejection/defense, aggression blend, market-state grid, trend
hysteresis) to rebuild the daily committed state per bar; the metrics helpers
score how those states stratify forward returns.
"""
from __future__ import annotations

from scoring import effort, rejection_defense, aggression, market_state, trend_regime


# --- direction-score tunables -------------------------------------------------
MIN_BARS = 200         # below this -> neutral 50.0 (EMA200 needs a full window)
EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200
SLOPE_WINDOW = 20      # trailing bars for the signed slope sub-signal
RSI_PERIOD = 14
ALIGN_SCALE = 0.05     # EMA-stack spread that saturates alignment to +/-1
SLOPE_SCALE = 0.05     # 20-bar return that saturates slope to +/-1


def _clamp(v, lo, hi):
    return float(max(lo, min(hi, v)))


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _closes(daily_bars):
    out = []
    for b in daily_bars or ():
        if not isinstance(b, dict):
            continue
        c = _num(b.get("close"))
        if c is not None:
            out.append(c)
    return out


def _ema(values, period):
    """Final EMA value: SMA seed over the first ``period`` bars, then the
    ``alpha = 2/(period+1)`` recurrence. None if fewer than ``period`` bars."""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def _rsi(closes, period=RSI_PERIOD):
    """Simple RSI (documented choice): the last ``period`` deltas averaged with a
    plain mean, NOT Wilder's smoothing. The historical proxy doesn't need to
    match a charting platform tick-for-tick — a simple RSI is deterministic,
    dependency-free, and monotone in the same direction. Returns 50.0 (neutral)
    when there is too little data."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1]
              for i in range(len(closes) - period, len(closes))]
    avg_gain = sum(d for d in deltas if d > 0) / period
    avg_loss = sum(-d for d in deltas if d < 0) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def daily_direction_score(daily_bars) -> float:
    """0-100 directional score (50 neutral, 100 max bull) from the close list.

    A DAILY PROXY for the live intraday direction blend used in the historical
    validation study. Blends three signed sub-signals into a direction in
    [-1, 1] then maps ``50 + 50*direction``:
      (a) EMA20/50/200 stack alignment (signed EMA20-vs-EMA200 spread),
      (b) the 20-bar signed close slope,
      (c) a daily simple RSI(14) recentred to [-1, 1].
    Fewer than ``MIN_BARS`` (~200) bars -> 50.0. Never raises.
    """
    closes = _closes(daily_bars)
    n = len(closes)
    if n < MIN_BARS:
        return 50.0

    ema_fast = _ema(closes, EMA_FAST)
    ema_slow = _ema(closes, EMA_SLOW)
    if ema_slow and ema_slow > 0 and ema_fast is not None:
        spread = (ema_fast - ema_slow) / ema_slow
    else:
        spread = 0.0
    align = _clamp(spread / ALIGN_SCALE, -1.0, 1.0)

    ref = closes[-SLOPE_WINDOW - 1]
    slope = _clamp(((closes[-1] - ref) / ref) / SLOPE_SCALE, -1.0, 1.0) \
        if ref else 0.0

    rsi_signed = _clamp((_rsi(closes) - 50.0) / 50.0, -1.0, 1.0)

    direction = _clamp((align + slope + rsi_signed) / 3.0, -1.0, 1.0)
    return _clamp(50.0 + 50.0 * direction, 0.0, 100.0)


# --- reconstruction -----------------------------------------------------------
def reconstruct_state_series(daily_bars, direction_lookback=220,
                             agg_lookback=30):
    """Per-bar committed five-state series over ``daily_bars`` (oldest first).

    For each index ``t >= direction_lookback`` the DIRECTION axis is
    ``daily_direction_score`` over the trailing ``direction_lookback`` window and
    the AGGRESSION axis is the confidence-weighted blend of the REAL
    ``effort`` + ``rejection_defense`` sub-scores over the trailing
    ``agg_lookback`` window; the two are crossed via
    ``market_state.classify_market_state`` and threaded through
    ``trend_regime.commit_state``'s 2-day hysteresis (history + prev-committed
    carried across the loop). Days before warmup, or any day whose slice fails,
    yield ``None``. Pure + deterministic — never raises.
    """
    bars = list(daily_bars or ())
    n = len(bars)
    states = [None] * n

    history: list[str] = []
    prev_committed = None

    for t in range(n):
        if t < direction_lookback:
            continue
        try:
            dir_slice = bars[t - direction_lookback:t + 1]
            agg_slice = bars[max(0, t - agg_lookback):t + 1]

            direction = daily_direction_score(dir_slice)
            eff = effort.score_effort(agg_slice)
            rej = rejection_defense.score_rejection_defense(agg_slice)
            agg_score, _agg_conf = aggression.blend_aggression(
                {"effort": eff.score, "rejection": rej.score},
                {"effort": eff.confidence, "rejection": rej.confidence},
            )
            raw = market_state.classify_market_state(direction, agg_score).state
            committed, history = trend_regime.commit_state(
                raw, history, prev_committed)
            prev_committed = committed
            states[t] = committed
        except Exception:
            states[t] = None
    return states
