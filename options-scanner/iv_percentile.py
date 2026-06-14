"""
iv_percentile.py - IV-percentile + realized-vol-trend helpers
Version: 1.0.0
Last Updated: 2026-06-09

Pure helpers feeding the Dealer Pinch detector's IV-elevated (C3a) and
realized-vol-dropping (C3b) conditions. No network/IO — callers pass in candle
series (e.g. from load_price_history on the vol index $VIX/$VXN, and on the
underlying for realized vol).

Version 1.0.0 Changes:
- Initial implementation: percentile_rank, realized_vol_trend.
"""

from iv_analysis import calc_historical_vol_series


def percentile_rank(closes, value):
    """Percentile of ``value`` within ``closes`` (0-100).

    % of entries <= value. Used on a vol-index close series (e.g. last 30
    sessions of $VIX) to see how elevated current IV is. Returns None when the
    series is empty.
    """
    if not closes:
        return None
    n = len(closes)
    below_or_equal = sum(1 for c in closes if c <= value)
    return 100.0 * below_or_equal / n


def realized_vol_trend(candles, window=5, lookback=3):
    """Short-term realized-vol level + whether it is falling.

    Computes a rolling close-to-close HV series (``window`` sessions) via
    ``iv_analysis.calc_historical_vol_series`` and compares the latest value to
    the value ``lookback`` sessions earlier.

    Returns ``{"value": float|None, "falling": bool|None}`` — both None when
    there aren't enough candles to form the comparison.
    """
    none = {"value": None, "falling": None}
    series = calc_historical_vol_series(candles, window=window)
    if len(series) <= lookback:
        return none
    latest = series[-1][1]
    prior = series[-1 - lookback][1]
    if latest is None or prior is None:
        return none
    return {"value": latest, "falling": latest < prior}
