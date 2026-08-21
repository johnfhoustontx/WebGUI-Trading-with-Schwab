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


def _finite(x):
    """A usable number, else None. A NaN survives `is not None` and `not x`, and
    `_clamp(nan, lo, hi)` returns the HIGH bound -- so without this an absent
    reading renders as a confident MAXIMUM one (a NaN VIX scored 70/conf 1.0, a
    NaN advance-decline scored 100). Missing means missing."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (v != v or v in (float("inf"), float("-inf"))) else v


def _clamp(v, lo, hi):
    # Always float so TrendSub.score is uniformly float (it gets JSON-serialized).
    return float(max(lo, min(hi, v)))


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
    net_ad, pct_above_50 = _finite(net_ad), _finite(pct_above_50)
    if net_ad is not None:
        comps.append(_clamp(net_ad, -1, 1)); weights.append(0.4)
    if pct_above_50 is not None:
        comps.append(_clamp((pct_above_50 - 50.0) / 50.0, -1, 1)); weights.append(0.4)
    hl_total = (_finite(new_highs) or 0) + (_finite(new_lows) or 0)
    if hl_total > 0:
        comps.append(_clamp(((_finite(new_highs) or 0) - (_finite(new_lows) or 0))
                            / hl_total, -1, 1))
        weights.append(0.2)
    if not weights:
        return TrendSub(score=50.0, confidence=0.0)
    direction = sum(c * w for c, w in zip(comps, weights)) / sum(weights)
    confidence = _clamp(sum(weights), 0.0, 1.0)
    return TrendSub(score=round(_clamp(50 + 50 * direction, 0, 100), 2),
                    confidence=round(confidence, 3))


def score_sector_participation(n_green, n_total, cyc_def_spread) -> TrendSub:
    """Breadth of sector participation + cyclical-vs-defensive leadership.
    cyc_def_spread in ~[-1,1] (cyclicals leading positive)."""
    if not n_total:
        return TrendSub(score=50.0, confidence=0.0)
    participation = (n_green / n_total - 0.5) * 2.0
    cyc_def_spread = _finite(cyc_def_spread)
    lead = _clamp(cyc_def_spread, -1, 1) if cyc_def_spread is not None else 0.0
    direction = 0.6 * participation + 0.4 * lead
    return TrendSub(score=round(_clamp(50 + 50 * direction, 0, 100), 2),
                    confidence=round(_clamp(n_total / 11.0, 0, 1), 3))


def score_vix_context(vix, vix_change_pct, vix1d, vix9d) -> TrendSub:
    vix, vix_change_pct = _finite(vix), _finite(vix_change_pct)
    vix1d = _finite(vix1d)
    if not vix or vix <= 0 or vix_change_pct is None:
        return TrendSub(score=50.0, confidence=0.0)
    lvl = _clamp((20.0 - vix) / 10.0, -1, 1)
    chg = _clamp(-vix_change_pct / 5.0, -1, 1)
    term = _clamp((vix - vix1d) / 2.0, -1, 1) if vix1d else 0.0
    direction = 0.4 * lvl + 0.4 * chg + 0.2 * term
    return TrendSub(score=round(_clamp(50 + 50 * direction, 0, 100), 2),
                    confidence=1.0)


def vol_confidence_factor(vix_change_pct) -> float:
    """Global confidence multiplier: a sharp VIX *spike* makes any trend read less
    reliable. Falling/flat VIX -> 1.0. -0.04 per % of spike, floored at 0.4."""
    if vix_change_pct <= 0:
        return 1.0
    return round(_clamp(1.0 - 0.04 * vix_change_pct, 0.4, 1.0), 3)


TREND_WEIGHTS = {"price": 0.45, "breadth": 0.25, "sector": 0.20, "vix": 0.10}


def blend_trend(scores, confs, weights=None):
    """Confidence-weighted blend -> (score_0_100, aggregate_confidence).
    Mirrors scoring/composite.blend. den==0 -> neutral 50.0, conf 0.0."""
    weights = weights or TREND_WEIGHTS
    num = den = 0.0
    for k, w in weights.items():
        c = _finite(confs.get(k)) or 0.0
        # Only ABSENCE means neutral. `scores.get(k, 50.0) or 50.0` used to
        # replace a score of exactly 0.0 with 50 -- and 0.0 is the entire
        # saturated crash-tape region of score_price (clamped to [0,100]), so
        # the most bearish possible tape blended 22.5 points BULLISH of one
        # tick off the floor, at unchanged confidence (fixed 2026-08-20).
        s = _finite(scores.get(k))
        s = 50.0 if s is None else s
        num += w * s * c
        den += w * c
    if den <= 0:
        return 50.0, 0.0
    return round(num / den, 2), round(den, 3)


def score_to_state(score):
    if score >= 80:
        return "bull_trend"
    if score >= 70:
        return "pullback_in_bull"
    if score >= 30:
        return "range"
    if score >= 20:
        return "bear_rally"
    return "bear_trend"


def ema_smooth(prev, new, span=3):
    """EMA-smooth the published needle (~2-3 fifteen-min reads). prev None ->
    passthrough."""
    if prev is None:
        return round(float(new), 2)
    alpha = 2.0 / (span + 1.0)
    return round(alpha * float(new) + (1 - alpha) * float(prev), 2)
