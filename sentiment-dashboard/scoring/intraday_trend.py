"""Intraday directional Market Trend score (0-100, 50 = neutral).

Pure functions — scalar in, scalar out (no pandas, no tk, no I/O). The sentiment
service extracts scalars from proxy data and calls these; the webgui renders the
result. Distinct from the 1-10 *contrarian* composite: this is *directional*
(100 = max bull, 0 = max bear). Reuses the confidence-weighted blend idiom of
scoring/composite.py and the state vocabulary of scoring/trend_regime.py.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class TrendSub:
    score: float          # 0-100 directional
    confidence: float     # [0.0, 1.0]
    interp: str = ""


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def score_price(alignment_pct, price_vs_vwap_pct, macd_hist, rsi, adx,
                n_timeframes) -> TrendSub:
    """0-100 from MTF EMA alignment (dominant), VWAP, MACD sign, RSI; ADX scales
    how far the needle leaves 50 (strong trend -> extremes, chop -> ~50)."""
    a = _clamp(alignment_pct / 100.0, -1.0, 1.0)
    v = _clamp(price_vs_vwap_pct / 0.5, -1.0, 1.0)
    m = 1.0 if macd_hist > 0 else -1.0 if macd_hist < 0 else 0.0
    r = _clamp((rsi - 50.0) / 20.0, -1.0, 1.0)
    direction = 0.5 * a + 0.2 * v + 0.15 * m + 0.15 * r
    adx_factor = _clamp(adx / 40.0, 0.3, 1.0)
    score = _clamp(50.0 + 50.0 * direction * adx_factor, 0.0, 100.0)
    confidence = _clamp(n_timeframes / 3.0, 0.0, 1.0)
    return TrendSub(score=round(score, 2), confidence=round(confidence, 3))


def score_breadth_dir(net_ad, pct_above_50, new_highs, new_lows) -> TrendSub:
    """net_ad = (advn-decn)/(advn+decn) in [-1,1]; pct_above_50 in [0,100];
    H/L counts. Missing inputs lower confidence; all-missing -> neutral/0 conf."""
    comps, weights = [], []
    if net_ad is not None:
        comps.append(_clamp(net_ad, -1, 1)); weights.append(0.4)
    if pct_above_50 is not None:
        comps.append(_clamp((pct_above_50 - 50.0) / 50.0, -1, 1)); weights.append(0.4)
    hl_total = (new_highs or 0) + (new_lows or 0)
    if hl_total > 0:
        comps.append(_clamp(((new_highs or 0) - (new_lows or 0)) / hl_total, -1, 1))
        weights.append(0.2)
    if not weights:
        return TrendSub(score=50.0, confidence=0.0)
    direction = sum(c * w for c, w in zip(comps, weights)) / sum(weights)
    confidence = _clamp(sum(weights), 0.0, 1.0)
    return TrendSub(score=round(_clamp(50 + 50 * direction, 0, 100), 2),
                    confidence=round(confidence, 3))
