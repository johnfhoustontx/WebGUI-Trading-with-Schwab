"""Live intraday sentiment composite + bridge payload.

Shared by the GEX collector (headless 5-min publish) and the webgui Sentiment
page. Reuses the pure scoring modules with CURRENT quotes (the live analog of
history_backfill._score_one_day). No tk imports.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from scoring import WEIGHTS
from scoring import composite as scoring_composite
from scoring import vix as _vix
from scoring import put_call as _pc
from scoring import breadth as _breadth
from scoring import rotation as _rotation
from scoring import sector_perf as _sector
from scoring.types import ScoreResult

logger = logging.getLogger(__name__)


def _pmap(fn, items, workers=6):
    # Concurrent, order-preserving map for the I/O-bound per-sector proxy fan-out
    # (the proxy rate-limiter only spaces calls, so concurrency overlaps them).
    items = list(items)
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))


# Component display order + back-compat: which keys go in the bridge.
_BRIDGE_COMPONENTS = ("vix_complex", "put_call", "breadth", "rotation", "sector_perf")

_VIX_SYMS = ["$VIX1D", "$VIX9D"]
_BREADTH = {"advn": ["$ADVN"], "decn": ["$DECN"],
            "nyhgh": ["$NYHGH", "$NYHGH.X", "$NEWH"],
            "nylow": ["$NYLOW", "$NYLOW.X", "$NEWL"],
            "pct50": ["$SPXA50R", "$NYA50R", "$MMFI"]}


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def cap_weighted_pcr(pcr, weights):
    """Cap-weighted RAW cross-sector Put/Call ratio (NOT the 1-10 contrarian score).

    Only sectors that have BOTH a pcr and a non-zero cap weight contribute.
    Higher = more puts = more downside hedging. Returns None when no usable
    sector is present.
    """
    pw = [(pcr[e], weights.get(e, 0.0)) for e in pcr if weights.get(e)]
    if not pw:
        return None
    return sum(r * w for r, w in pw) / sum(w for _, w in pw)


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
            "trend_score": trend.get("trend_score"),
            "sub_scores": trend.get("sub_scores"),
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


def _breadth_ratio(advn, decn):
    """Advance/decline ratio, or None when there is no reading.

    ⚠ ABSENT and ZERO are different facts here, and conflating them fabricated
    the most bullish possible breadth: the old
    ``(advn / decn) if (advn and decn) else (inf if advn else None)`` handed
    ``inf`` to ``breadth.score`` — which maps inf to score 10 — whenever $DECN
    was simply MISSING from the batched quote (a per-symbol failure the batch
    can produce), indistinguishable from a genuine no-decliners tape. ``inf`` is
    now reserved for ``decn == 0`` with advancers present; a missing symbol on
    either side reads None. ``advn == 0`` with decliners is a real, extreme
    bearish tape (ratio 0.0), not an absence (fixed 2026-08-20).
    """
    if advn is None or decn is None:
        return None
    if decn == 0:
        return float("inf") if advn > 0 else None
    return advn / decn


def _last(qd):
    if not isinstance(qd, dict):
        return None
    for k in ("last", "lastPrice", "mark"):
        if qd.get(k) is not None:
            return _safe_float(qd.get(k))
    return None


# Sector P/C TTL cache (see compute_live's P/C block for the why).
PCR_TTL_SEC = 900  # 15 min — the P/C is a slow-moving cumulative volume ratio
_PCR_CACHE = {"ts": 0.0, "pcr": None}


def reset_pcr_cache():
    """Drop the cached sector P/C so the next compute_live refetches (test helper)."""
    _PCR_CACHE.update(ts=0.0, pcr=None)


from scoring.put_call import pcr_from_chain as _pcr_from_chain  # noqa: E402

def compute_live(schwab, sector_data, prior_vix1d=0.0, prior_sector_trends=None):
    """Compute the live intraday composite snapshot (backfill-snapshot shaped)."""
    sectors = [r["etf"] for r in sector_data if r.get("kind") == "sector" and r.get("etf")]
    sp_weights = {r["etf"]: r.get("sp_weight", 0.0)
                  for r in sector_data if r.get("kind") == "sector" and r.get("etf")}

    # --- quotes (batched) ---
    def _q(syms):
        try:
            return schwab.get_quotes(list(syms)) or {}
        except Exception:
            return {}
    vix_q = {}
    try:
        vix_q = {"$VIX": schwab.get_quote("$VIX") or {}}
    except Exception:
        pass
    vq = _q(_VIX_SYMS)
    bq = _q([s for v in _BREADTH.values() for s in v])
    sector_q = _q(sectors)
    try:
        irx = _last(schwab.get_quote("$IRX"))
    except Exception:
        irx = None

    vix = _last(vix_q.get("$VIX")) or 0.0
    # VIX 10d MA
    vix_ma = 0.0
    try:
        df = schwab.get_daily_history("$VIX", months=1)
        if df is not None:
            cl = [float(c) for c in df["close"].tolist()][-10:]
            vix_ma = sum(cl) / len(cl) if cl else 0.0
    except Exception:
        pass
    v1d = _last(vq.get("$VIX1D")) or 0.0
    v9d = _last(vq.get("$VIX9D")) or 0.0
    term = _vix.score_term(vix, vix_ma)
    v1d_r = _vix.score_vix1d(v1d, vix, _safe_float(prior_vix1d))
    slope = _vix.score_term_slope(v9d, vix)
    vix_complex = _vix.score_complex(term, v1d_r, slope)

    # --- breadth ---
    def _first(group):
        for s in _BREADTH[group]:
            v = _last(bq.get(s))
            if v is not None:
                return v
        return None
    advn, decn = _first("advn"), _first("decn")
    highs, lows = _first("nyhgh") or 0, _first("nylow") or 0
    pct50 = _first("pct50")
    ratio = _breadth_ratio(advn, decn)
    net = ((advn or 0) - (decn or 0)) if (advn is not None or decn is not None) else None
    br = _breadth.score(pct_above_50=(f"{pct50:.1f}" if pct50 is not None else ""),
                        nyse_ad="Neutral", new_highs=float(highs), new_lows=float(lows),
                        breadth_ratio=ratio, breadth_numeric=net)

    # --- sector quotes -> last_quotes (day%) ---
    last_quotes = {}
    for etf in sectors:
        pct = (sector_q.get(etf) or {}).get("change_pct")
        if pct is not None:
            last_quotes[etf] = {"change_pct": pct}

    # --- per-sector P/C from /chains -> put_call (fetched concurrently) ---
    # TTL-cached: the composite refreshes every 120 s but the cap-weighted P/C is
    # a slow-moving cumulative VOLUME ratio — refetching 11 NTM chains per refresh
    # was ~3,300 Schwab calls/day for a value that barely moves in 15 minutes.
    # An empty result (off-hours: no volume) is NOT cached, so the first refresh
    # after the open picks up real volume immediately.
    from datetime import date, timedelta
    now_mono = time.monotonic()
    if (_PCR_CACHE["pcr"] is not None
            and now_mono - _PCR_CACHE["ts"] < PCR_TTL_SEC):
        pcr = dict(_PCR_CACHE["pcr"])
    else:
        today_iso = date.today().isoformat()
        to_iso = (date.today() + timedelta(days=30)).isoformat()

        def _chain(etf):
            try:
                chain = schwab._request("/chains", params={
                    "symbol": etf, "contractType": "ALL", "range": "NTM",
                    "strikeCount": 50, "fromDate": today_iso, "toDate": to_iso})
            except Exception:
                chain = None
            return etf, _pcr_from_chain(chain)

        pcr = {etf: v for etf, v in _pmap(_chain, sectors) if v is not None}
        if pcr:  # cache only a usable (non-empty) result
            _PCR_CACHE["pcr"] = dict(pcr)
            _PCR_CACHE["ts"] = now_mono
    pc_res = _pc.score_sector_weighted(pcr, sp_weights)
    # Additive: RAW cap-weighted cross-sector Put/Call ratio (options-flow
    # direction). Higher = more downside hedging. None when no usable sector.
    sector_pcr = cap_weighted_pcr(pcr, sp_weights)

    # --- sector close history -> dual momentum (rotation) + sector_perf ---
    def _hist(etf):
        try:
            return etf, schwab.get_daily_history(etf, months=12)
        except Exception:
            return etf, None

    closes = {}
    for etf, df in _pmap(_hist, sectors):
        if df is not None:
            cl = [float(c) for c in df["close"].tolist()]
            if len(cl) >= 64:
                closes[etf] = cl
    dual = _rotation.compute_dual_momentum(
        closes, sp_weights, (irx / 10.0) if irx else None, lookback_days=63)
    rot_score = float(dual.get("score", 0) or 0)
    rot_conf = float(dual.get("confidence", 0.0) or 0.0)
    sec_score = _sector.sectors_score(sector_data, last_quotes)
    n_sec = sum(1 for e in sectors if e in last_quotes)
    sec_conf = (n_sec / 11.0) ** 0.5 if n_sec else 0.0

    scores = {"vix_complex": float(vix_complex.score), "put_call": float(pc_res.score),
              "breadth": float(br.score), "rotation": rot_score, "sector_perf": float(sec_score)}
    confs = {"vix_complex": float(vix_complex.confidence), "put_call": float(pc_res.confidence),
             "breadth": float(br.confidence), "rotation": rot_conf, "sector_perf": sec_conf}
    composite, agg = scoring_composite.blend(scores, confs, WEIGHTS)
    modifier, bias, _sig = signal_band(composite)
    return {
        "date": date.today().isoformat(),
        "source": "live",
        "composite": {"total_score": f"{composite:.2f}", "bias": bias,
                      "size_modifier": modifier, "aggregate_confidence": round(agg, 3)},
        "component_scores": {**scores, "credit_pulse": 0.0},
        "component_confidence": {**{k: round(v, 3) for k, v in confs.items()}, "credit_pulse": 0.0},
        "volatility": {"interpretation":
                       f"T{int(term.score)}-1D{int(v1d_r.score)}-S{int(slope.score)}"},
        "options": {"pc_equity": "", "interpretation": pc_res.interp},
        "sector_pcr": sector_pcr,
        "breadth": {"interpretation": br.interp},
        "rotation": {"interpretation": dual.get("interp", "")},
        "_sector_runtime": {"sector_data": sector_data, "quotes": last_quotes, "dual": dual, "closes": closes},
        "_vix1d": v1d,
    }


def _resolve_proxy_client():
    import sys, pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from repo_paths import SCHWAB_PROXY, PROXY_URL
    if str(SCHWAB_PROXY) not in sys.path:
        sys.path.insert(0, str(SCHWAB_PROXY))
    from proxy_client import SchwabProxyClient
    return SchwabProxyClient(PROXY_URL)


def publish_bridge(schwab=None):
    """Compute the live composite and write shared/sentiment_bridge.json.
    Self-contained + defensive; returns the payload or None on failure."""
    try:
        import sectors_ref
        from scoring import trend_regime as _tr
        import bridge
        if schwab is None:
            schwab = _resolve_proxy_client()
        sd = sectors_ref.load_sectors_data()
        snap = compute_live(schwab, sd)
        sector_rt = snap.pop("_sector_runtime", None)
        # trend regime from SPY 12mo
        spy_closes = []
        try:
            df = schwab.get_daily_history("SPY", months=12)
            spy_closes = [float(c) for c in df["close"].tolist()] if df is not None else []
        except Exception:
            pass
        trend = None
        if spy_closes:
            r = _tr.classify(spy_closes)
            trend = {"state": r.state, "label": r.label, "description": r.description,
                     "raw_state": r.state, "spy_close": round(r.spy_close, 4),
                     "sma_50": round(r.sma_50, 4), "sma_200": round(r.sma_200, 4),
                     "sma_200_slope_pct": round(r.sma_200_slope_pct, 4),
                     "drawdown_pct": round(r.drawdown_pct, 4), "confidence": round(r.confidence, 3)}
        gen = datetime.now(timezone.utc).isoformat()
        payload = build_bridge_payload(snap, history_scores=[], spy_closes=spy_closes,
                                       generated_at=gen, sector=sector_rt, trend=trend)
        bridge.write_bridge(payload)
        logger.info("sentiment bridge published: score=%s regime=%s",
                    payload.get("composite_score"), payload.get("regime"))
        return payload
    except Exception:
        logger.exception("publish_bridge failed")
        return None
