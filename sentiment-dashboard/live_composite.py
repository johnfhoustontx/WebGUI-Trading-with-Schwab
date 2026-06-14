"""Live intraday sentiment composite + bridge payload.

Shared by the GEX collector (headless 5-min publish) and the webgui Sentiment
page. Reuses the pure scoring modules with CURRENT quotes (the live analog of
history_backfill._score_one_day). No tk imports.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from scoring import WEIGHTS
from scoring import composite as scoring_composite

logger = logging.getLogger(__name__)

# Component display order + back-compat: which keys go in the bridge.
_BRIDGE_COMPONENTS = ("vix_complex", "put_call", "breadth", "rotation", "sector_perf")


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def signal_band(total):
    """(size_modifier, bias, signal) — mirrors source _update_position_modifier."""
    if total >= 9:
        return "1.25x", "Long", "Strong Bull"
    if total >= 7:
        return "1.10x", "Long", "Bullish"
    if total >= 5:
        return "1.00x", "Neutral", "Neutral"
    if total >= 3:
        return "0.85x", "Cautious", "Bearish"
    return "0.70x", "Short", "Strong Bear"


def build_bridge_payload(snapshot, history_scores, spy_closes, generated_at,
                         sector=None, trend=None):
    """Faithful bridge dict (mirrors source _build_bridge_payload), built from a
    live (or backfill) snapshot. `history_scores` = prior composite totals (for
    rolling avgs + velocity). `trend` = optional dict
    {state,label,description,raw_state,spy_close,sma_50,sma_200,sma_200_slope_pct,
    drawdown_pct,confidence}. `sector` = optional dict with quotes/dual for
    sector_breakdown + rotation_detail."""
    comp = snapshot.get("composite") or {}
    total = _safe_float(comp.get("total_score"))
    cs = snapshot.get("component_scores") or {}
    cc = snapshot.get("component_confidence") or {}
    scores = [s for s in (list(history_scores) + [total]) if s and s > 0]
    a5 = sum(scores[-5:]) / max(1, len(scores[-5:])) if scores else 0
    a20 = sum(scores[-20:]) / max(1, len(scores[-20:])) if scores else 0
    regime = ("strong_bullish" if total >= 8 else "bullish" if total >= 6.5
              else "neutral" if total >= 5 else "bearish" if total >= 3.5
              else "strong_bearish")
    momentum = ("rising" if a5 > a20 + 0.3 else "falling" if a5 < a20 - 0.3 else "stable")
    modifier, bias, signal = signal_band(total)
    vel = scoring_composite.velocity(list(history_scores), total)
    div = scoring_composite.divergence([
        (k, _safe_float(cs.get(k))) for k in _BRIDGE_COMPONENTS
        if _safe_float(cs.get(k)) > 0 and _safe_float(cc.get(k)) > 0])
    vix_c = _safe_float(cs.get("vix_complex"))
    sec_s = _safe_float(cs.get("sector_perf"))
    payload = {
        "source": "WebGUI-Sentiment",
        "generated_at": generated_at,
        "date": snapshot.get("date"),
        "composite_score": round(total, 2),
        "regime": regime,
        "bias": bias.lower(),
        "position_size_modifier": modifier,
        "contrarian_signal": signal,
        "momentum": momentum,
        "rolling_averages": {"5d": round(a5, 2), "20d": round(a20, 2)},
        "component_scores": {
            "vix_complex": vix_c, "vix": vix_c, "vix_term": vix_c,
            "vix1d": _safe_float(cs.get("vix1d")), "term_slope": _safe_float(cs.get("term_slope")),
            "put_call": _safe_float(cs.get("put_call")),
            "breadth": _safe_float(cs.get("breadth")),
            "flow": 0.0,
            "rotation": _safe_float(cs.get("rotation")),
            "sector": sec_s, "sector_perf": sec_s,
            "credit_pulse": _safe_float(cs.get("credit_pulse")),
        },
        "component_confidence": {k: round(_safe_float(v), 3) for k, v in cc.items()},
        "aggregate_confidence": round(
            sum(WEIGHTS[k] * _safe_float(cc.get(k)) for k in WEIGHTS), 3),
        "weights": dict(WEIGHTS),
        "velocity": {
            "roc_3d": (round(vel["roc_3d"], 2) if vel.get("roc_3d") is not None else None),
            "roc_5d": (round(vel["roc_5d"], 2) if vel.get("roc_5d") is not None else None),
            "z_20d": (round(vel["z_20d"], 2) if vel.get("z_20d") is not None else None),
            "regime_break": bool(vel.get("regime_break")),
        },
        "divergence_flag": div or None,
    }
    if trend:
        payload["trend_regime"] = {
            "state": trend.get("state"), "label": trend.get("label"),
            "description": trend.get("description"), "raw_state": trend.get("raw_state"),
            "spy_close": trend.get("spy_close"), "sma_50": trend.get("sma_50"),
            "sma_200": trend.get("sma_200"), "sma_200_slope_pct": trend.get("sma_200_slope_pct"),
            "drawdown_pct": trend.get("drawdown_pct"), "confidence": trend.get("confidence"),
        }
    if sector:
        bd = []
        for r in sector.get("sector_data", []):
            if r.get("kind") != "sector" or not r.get("etf"):
                continue
            pct = ((sector.get("quotes") or {}).get(r["etf"]) or {}).get("change_pct")
            if pct is not None:
                bd.append({"sector": r.get("sector"), "etf": r["etf"], "day_pct": round(float(pct), 4)})
        if bd:
            payload["sector_breakdown"] = bd
        dual = sector.get("dual") or {}
        if dual:
            payload["rotation_detail"] = {
                "method": "dual_momentum_v1",
                "crash_active": bool(dual.get("crash_active", False)),
                "cyc_avg_rank": dual.get("cyc_avg_rank"), "def_avg_rank": dual.get("def_avg_rank"),
                "rank_spread": dual.get("rank_spread"), "top_etf": dual.get("top_etf"),
                "ranks": dual.get("ranks", {}), "returns_63d": dual.get("returns", {}),
            }
    return payload
