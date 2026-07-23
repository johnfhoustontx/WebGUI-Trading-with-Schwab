"""Sentiment compute module — NiceGUI-free engine-call layer.

Extracted from ``webgui/pages/sentiment.py`` so the backend sentiment service
owns the heavy engine calls (the GUI tier will later consume this instead of
holding its own copies). This module must NOT import ``nicegui`` or anything
from ``webgui/`` — it depends only on the shared ``services._proxy`` accessor
and the copied sentiment-dashboard engines.

The module-top ``sys.path`` glue + eager engine imports mirror the page's. Now
that these run inside the (process-isolated) sentiment service, the
``scoring`` package-vs-module collision documented in the root CLAUDE.md can no
longer occur — there is no options ``scoring.py`` on this process's path, so
``from scoring import ...`` binds the sentiment ``scoring`` package once and
stays bound.

The engine-call functions are defensive (catch exceptions, return ``None`` /
empty) exactly as in the page — that behavior is preserved verbatim.
"""
import math
import sys
import time

from repo_paths import SENTIMENT, SHARED
from services._parallel import parallel_map

if str(SENTIMENT) not in sys.path:
    sys.path.insert(0, str(SENTIMENT))

# ``technical`` (the shared indicator lib) is imported standalone — its dir on
# ``sys.path`` — to dodge the ``shared.analysis_lib`` package ``__init__`` (which
# eagerly imports a broken ``schwab_client``). Safe here because the sentiment
# service runs in its own process (the same isolation ``trade_svc`` relies on).
_ANALYSIS_LIB = SHARED / "analysis_lib"
if str(_ANALYSIS_LIB) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_LIB))

from scoring import WEIGHTS  # noqa: E402,F401
from scoring import composite as scoring_composite  # noqa: E402,F401
from scoring import trend_regime as trend_regime  # noqa: E402
from scoring import sector_perf as scoring_sector  # noqa: E402,F401
from scoring import rotation as scoring_rotation    # noqa: E402
from scoring import intraday_trend  # noqa: E402
from scoring import effort as effort_mod  # noqa: E402
from scoring import aggression as aggression_mod  # noqa: E402
from scoring import order_flow as order_flow_mod  # noqa: E402
from scoring import market_state  # noqa: E402
from scoring import session_structure as session_structure_mod  # noqa: E402
from scoring import rejection_defense as rejection_mod  # noqa: E402
from scoring import profile_shape as profile_mod  # noqa: E402
from scoring import market_regime  # noqa: E402
from scoring import regime_evidence  # noqa: E402
from scoring import volatility as _regime_vol  # noqa: E402
import live_composite  # noqa: E402,F401  (eager: pins module; never lazy)
from live_composite import (  # noqa: E402
    signal_band, compute_live, build_bridge_payload,
    _BREADTH, _last, _VIX_SYMS)  # noqa: F401
import technical  # noqa: E402  (shared indicator lib, standalone import)

# (component_scores key, display name) — mirrors the page's COMPONENTS minus
# the weight (weights now flow to the GUI via the derived payload). Used to
# build divergence-named pairs in the same order the page formats them.
COMPONENTS = [
    ("vix_complex", "VIX Complex"),
    ("put_call", "Put/Call (sectors)"),
    ("breadth", "Market Breadth"),
    ("rotation", "Rotation"),
    ("sector_perf", "Sector Performance"),
    ("credit_pulse", "Credit Pulse"),
]


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def composite_series(snapshots):
    """(dates, scores) for snapshots with a positive composite total."""
    dates, scores = [], []
    for s in snapshots:
        v = _safe_float((s.get("composite") or {}).get("total_score"))
        if v > 0:
            dates.append(s.get("date"))
            scores.append(v)
    return dates, scores


def commit_trend_regime(spy_closes, lookback_days=trend_regime.HYSTERESIS_DAYS + 1):
    """Replay classify + commit_state over the last sessions for faithful
    hysteresis without persisted state. Returns (result, committed, days)."""
    closes = list(spy_closes)
    result = trend_regime.classify(closes)
    committed = None
    history = []
    n = len(closes)
    span = min(lookback_days, max(1, n - trend_regime.MIN_BARS_PARTIAL))
    for back in range(span - 1, -1, -1):
        sub = closes[: n - back] if back else closes
        raw = trend_regime.classify(sub).state
        committed, history = trend_regime.commit_state(raw, history, committed)
    days = 1
    return result, (committed or result.state), days


def pcr_from_chain(chain):
    """Sum put vs call totalVolume from a Schwab /chains payload -> ratio.
    Returns None when no chain or zero call volume. Ported from source
    sentiment_dashboard.py:2939-2953."""
    if not chain:
        return None
    pv = cv = 0
    for strikes in (chain.get("putExpDateMap") or {}).values():
        for contracts in strikes.values():
            for c in contracts:
                v = c.get("totalVolume", 0) or 0
                if v > 0:
                    pv += v
    for strikes in (chain.get("callExpDateMap") or {}).values():
        for contracts in strikes.values():
            for c in contracts:
                v = c.get("totalVolume", 0) or 0
                if v > 0:
                    cv += v
    return round(pv / cv, 3) if cv > 0 else None


def _pct_change_n(closes, n):
    """%-change from n sessions ago to last close, or None. Mirrors source
    _pct_change_n (uses close[-(n+1)])."""
    if not closes or len(closes) < n + 1:
        return None
    prev = float(closes[-(n + 1)])
    last = float(closes[-1])
    if prev == 0:
        return None
    return (last - prev) / prev * 100.0


def week_month_from_closes(closes):
    """(day3_pct, week_pct, month_pct) from a daily-close list (n=3/5/21)."""
    return (_pct_change_n(closes, 3),
            _pct_change_n(closes, 5),
            _pct_change_n(closes, 21))


def load_live():
    """Off-thread: live intraday composite snapshot (or None on failure)."""
    from services import _proxy
    import sectors_ref
    try:
        sd = sectors_ref.load_sectors_data()
        return compute_live(_proxy.schwab_client, sd)
    except Exception:
        return None


def load_snapshots(days=35):
    """Off-thread: full scoring path via the copied backfill engine.
    Returns (snapshots, spy_closes)."""
    from services import _proxy
    import sectors_ref
    from history_backfill import backfill_history

    sector_data = sectors_ref.load_sectors_data()
    snaps, _stats = backfill_history(_proxy.schwab_client, sector_data, [], days=days)
    spy_df = _proxy.schwab_client.get_daily_history("SPY", months=12)
    spy_closes = (
        [float(c) for c in spy_df["close"].tolist()]
        if spy_df is not None else []
    )
    return snaps, spy_closes


def proxy_up():
    """Best-effort proxy reachability (run off-thread)."""
    from services import _proxy
    try:
        return bool(_proxy.health().get("up"))
    except Exception:
        return False


def _fetch_closes(etfs, months):
    """Concurrent per-ETF daily-history fetch → ({etf: closes}, {etf: trends}).

    The per-ETF history pulls are independent, I/O-bound proxy calls, so they run
    in a thread pool instead of serializing one round-trip per ETF. Each fetch is
    defensive (a failed/empty ETF is simply omitted), matching the prior serial
    loop's per-ETF ``try/except`` + ``continue``. Order of completion doesn't
    matter — results are keyed by ETF."""
    from services import _proxy

    def _one(etf):
        try:
            return etf, _proxy.schwab_client.get_daily_history(etf, months=months)
        except Exception:
            return etf, None

    closes, trends = {}, {}
    for etf, df in parallel_map(_one, list(etfs)):
        if df is None:
            continue
        cl = [float(c) for c in df["close"].tolist()]
        closes[etf] = cl
        d3, wk, mo = week_month_from_closes(cl)
        trends[etf] = {"day3_pct": d3, "week_pct": wk, "month_pct": mo}
    return closes, trends


def _fetch_pcr(etfs):
    """Concurrent per-ETF ``/chains`` fetch → {etf: put/call ratio}.

    Independent, I/O-bound proxy calls run in a thread pool; each is defensive
    (a failed chain / missing ratio is omitted), matching the prior serial loop."""
    from services import _proxy
    from datetime import date, timedelta
    today_iso = date.today().isoformat()
    to_iso = (date.today() + timedelta(days=30)).isoformat()

    def _one(etf):
        try:
            chain = _proxy.schwab_client._request("/chains", params={
                "symbol": etf, "contractType": "ALL", "range": "NTM",
                "strikeCount": 50, "fromDate": today_iso, "toDate": to_iso})
        except Exception:
            chain = None
        return etf, pcr_from_chain(chain)

    return {etf: v for etf, v in parallel_map(_one, list(etfs)) if v is not None}


def load_industries(etfs, spy_closes):
    """Off-thread: quotes + week/month trends + P/C + RRG for industry ETFs."""
    from services import _proxy
    try:
        quotes = _proxy.schwab_client.get_quotes(list(etfs)) or {}
    except Exception:
        quotes = {}
    closes, trends = _fetch_closes(etfs, months=3)
    pcr = _fetch_pcr(etfs)
    quads = scoring_rotation.compute_rrg_quadrants(closes, spy_closes or [],
                                                   rs_window=50, mom_window=20)
    return {"quotes": quotes, "trends": trends, "pcr": pcr, "quadrants": quads}


def load_sector_perf(spy_closes):
    """Off-thread: fetch sector quotes + history + P/C, compute rotation/RRG.
    Returns a dict the page renders. spy_closes reused from the composite load."""
    from services import _proxy
    import sectors_ref

    sd = sectors_ref.load_sectors_data()
    etfs = [r["etf"] for r in sd if r.get("kind") == "sector" and r.get("etf")]

    try:
        quotes = _proxy.schwab_client.get_quotes(etfs) or {}
    except Exception:
        quotes = {}

    # Per-ETF history + /chains both fan out concurrently (was 11+11 serial calls).
    closes, trends = _fetch_closes(etfs, months=3)
    pcr = _fetch_pcr(etfs)

    try:
        irx_q = _proxy.schwab_client.get_quote("$IRX") or {}
        irx = irx_q.get("last") if isinstance(irx_q, dict) else None
    except Exception:
        irx = None

    quads = scoring_rotation.compute_rrg_quadrants(closes, spy_closes or [],
                                                   rs_window=50, mom_window=20)
    sp_weights = {r["etf"]: r.get("sp_weight", 0.0)
                  for r in sd if r.get("kind") == "sector" and r.get("etf")}
    try:
        dual = scoring_rotation.compute_dual_momentum(closes, sp_weights, irx,
                                                      lookback_days=63)
    except Exception:
        dual = {}
    rot = scoring_rotation.compute_rotation(sd, trends, quotes)
    return {"sector_data": sd, "quotes": quotes, "trends": trends,
            "pcr": pcr, "quadrants": quads, "dual": dual, "rotation": rot}


def build_trend_dict(spy):
    """Trend-regime dict from SPY closes, or None. Shared by the bridge
    dual-write and the GUI-facing derived payload (single source of truth so
    the two never drift). Defensive: any failure (or empty ``spy``) -> None."""
    try:
        if not spy:
            return None
        tr, committed, days = commit_trend_regime(spy)
        return {
            "state": committed,
            "label": trend_regime.STATE_LABELS[committed],
            "description": trend_regime.STATE_DESCRIPTIONS[committed],
            "raw_state": tr.state,
            "spy_close": round(tr.spy_close, 4),
            "sma_50": round(tr.sma_50, 4),
            "sma_200": round(tr.sma_200, 4),
            "sma_200_slope_pct": round(tr.sma_200_slope_pct, 4),
            "drawdown_pct": round(tr.drawdown_pct, 4),
            "confidence": round(tr.confidence, 3),
            "days": days,
        }
    except Exception:  # noqa: BLE001
        return None


# ── intraday directional Market Trend (Phase 3) ──────────────────────────────
# Cyclical vs defensive sector ETFs (SPDR symbols, as in sectors_ref rows) for
# the leadership spread that feeds ``score_sector_participation``.
_CYCLICAL = {"XLK", "XLY", "XLF", "XLI", "XLB", "XLE", "XLC"}
_DEFENSIVE = {"XLP", "XLU", "XLV", "XLRE"}

# %-spread that maps cyclical-vs-defensive leadership to the full ±1 of
# ``cyc_def_spread``. Intraday day-moves are small (~1% = decisive); month-moves
# are ~3× larger, so the structural gauge uses a wider scale.
_CYC_DEF_SCALE_INTRADAY = 1.0
_CYC_DEF_SCALE_30D = 3.0

# Aggression-axis normalization scales (first-pass tunables). SKEW is in 25-delta
# risk-reversal IV points; PC is the 5-day cap-weighted cross-sector Put/Call
# change. The raw signal / scale is clamped to ±1, so each is "the input value
# that saturates that sub-signal to its extreme".
SKEW_DELTA_SCALE = 5.0   # first-pass tunable: IV points to saturate put-skew Δ
PC_DELTA_SCALE = 0.3     # first-pass tunable: P/C change to saturate flow

# Micro-structure refinements folded into the five-state classifier.
SESSION_BLEND = 0.20     # session structure's share of the price sub-score
PROFILE_DAMP = 0.5       # max aggression damping from a fully-balanced profile

# Sentinel so an OMITTED ``compute_30d_trend`` arg fetches internally, while an
# explicit ``None`` (caller has no data) stays neutral rather than re-fetching.
_FETCH = object()


def _neutral_trend():
    """Neutral directional-trend dict (score 50, conf 0, ``range`` state).

    Returned when there is no trend computed yet, or as the catastrophic-failure
    fallback of ``compute_intraday_trend`` — so the GUI always sees a valid,
    fully-shaped trend payload. Uses the five-state (direction × aggression)
    vocabulary: a neutral direction with zero aggression."""
    return {
        "score": 50.0,
        "smoothed_score": 50.0,
        "state": "neutral",
        "raw_state": "neutral",
        "label": market_state.STATE_LABELS["neutral"],
        "description": market_state.STATE_DESCRIPTIONS["neutral"],
        "confidence": 0.0,
        "sub_scores": {"price": 50.0, "breadth": 50.0, "sector": 50.0, "vix": 50.0},
        "sub_confidence": {"price": 0.0, "breadth": 0.0, "sector": 0.0, "vix": 0.0},
        "state_history": [],
        "aggression": 0.0,
        "aggression_confidence": 0.0,
        "evidence": [],
    }


def _mean(seq):
    seq = [v for v in seq if v is not None]
    return (sum(seq) / len(seq)) if seq else None


def compute_intraday_trend(schwab, sector_data=None, prior_history=None,
                           prior_committed=None, prev_smoothed=None,
                           flow_skew=None, sector_pc_delta=None,
                           order_flow=None) -> dict:
    """Live five-state Market State from direction × aggression.

    Blends four sub-scores (price MTF/VWAP/MACD/RSI, breadth, sector
    participation, VIX context) into an EMA-smoothed 0-100 *direction* score,
    then crosses it with a signed *aggression* axis (volume-effort + 25-delta
    put-skew Δ + cross-sector P/C Δ) via ``market_state.classify_market_state``
    to pick one of five states (``bullish`` / ``lack_of_bullishness`` /
    ``neutral`` / ``lack_of_bearishness`` / ``bearish``). The 2-day hysteresis
    state machine (``trend_regime.commit_state``) keeps the published state
    sticky, with a migration guard that treats a stale OLD-vocab
    ``prior_committed`` as a cold start (adopt the new vocabulary immediately).

    ``flow_skew`` is the ``cache:options:flow_skew`` payload
    (``{symbol: {rr_delta, ...}}``); ``sector_pc_delta`` is
    ``compute.sector_pc_delta()`` (a float or None); ``order_flow`` is the
    ``cache:sentiment:order_flow`` payload (``{symbol: {aggressor_ratio, n, ...}}``,
    SPY used — positive = net buying = ALIGNED, no sign flip). All default None so
    the aggression axis degrades gracefully to effort-only / neutral.

    Each sub-block is defensive: on failure that sub-score becomes
    ``TrendSub(50, 0)`` and drops out of the confidence-weighted blend. An
    outer guard returns a fully-shaped neutral dict on catastrophic failure."""
    try:
        # sector ETF universe
        try:
            if sector_data is None:
                import sectors_ref
                sector_data = sectors_ref.load_sectors_data()
            sector_etfs = [r["etf"] for r in (sector_data or [])
                           if r.get("kind") == "sector" and r.get("etf")]
        except Exception:  # noqa: BLE001
            sector_etfs = []

        # 1) PRICE — MTF EMA alignment + VWAP + MACD + RSI/ADX on intraday frames.
        try:
            frames = {}
            for key, df in (("5min", _safe_intraday(schwab, "SPY", 5, 10)),
                            ("15min", _safe_intraday(schwab, "SPY", 15, 10)),
                            ("1day", _safe_daily(schwab, "SPY", 12))):
                if df is not None and len(df) >= 50:
                    frames[key] = df
            if not frames:
                price = intraday_trend.TrendSub(50.0, 0.0)
            else:
                # Explicit membership — a DataFrame in a boolean `or` raises
                # "truth value is ambiguous" (silently zeroed the 45% price input).
                ref = frames["15min"] if "15min" in frames \
                    else next(iter(frames.values()))
                price_now = float(ref["close"].iloc[-1])
                align = technical.calculate_ema_alignment(frames, price_now)
                align_pct = float(align.get("alignment_percentage", 0.0))
                df15 = frames["15min"] if "15min" in frames else ref
                vwap = technical.calculate_vwap(df15)
                vwap_pct = ((price_now - vwap) / vwap * 100.0) if vwap else 0.0
                hist = technical.macd_histogram_series(df15)
                macd_hist = (float(hist.iloc[-1])
                             if hist is not None and len(hist) else 0.0)
                rsi = float(technical.calculate_rsi(df15))
                adx = float(technical.calculate_adx(df15))
                price = intraday_trend.score_price(
                    align_pct, vwap_pct, macd_hist, rsi, adx,
                    n_timeframes=len(frames))
        except Exception:  # noqa: BLE001
            price = intraday_trend.TrendSub(50.0, 0.0)

        # 1b) SESSION STRUCTURE — VWAP-hold + opening-range break blended INTO the
        #     price sub-score (DIRECTION). A bullish structure (holding above VWAP,
        #     clearing the OR) nudges the direction up. Defensive -> drops out.
        sess = session_structure_mod.SessionStructure(0.0, 0.0)
        try:
            # Prefer the 15-min frame; DataFrame-in-``or`` raises, so branch by None.
            sframe = frames.get("15min")
            if sframe is None:
                sframe = frames.get("5min")
            if sframe is not None and len(sframe):
                srows = [{"high": float(r["high"]), "low": float(r["low"]),
                          "close": float(r["close"]), "volume": float(r["volume"])}
                         for _, r in sframe.iterrows()]
                sess = session_structure_mod.score_session_structure(srows)
                if sess.confidence > 0:
                    sess_0_100 = 50.0 + 50.0 * sess.score
                    blended_price = ((1 - SESSION_BLEND) * price.score
                                     + SESSION_BLEND * sess_0_100)
                    # keep the price sub-score's own confidence + interp.
                    price = intraday_trend.TrendSub(
                        blended_price, price.confidence, price.interp)
        except Exception:  # noqa: BLE001 — session structure simply drops out.
            sess = session_structure_mod.SessionStructure(0.0, 0.0)

        # 2) BREADTH — A/D, % above 50DMA, new highs/lows.
        try:
            bq = schwab.get_quotes(
                [s for v in _BREADTH.values() for s in v]) or {}

            def _first(group):
                for sym in _BREADTH[group]:
                    v = _last(bq.get(sym))
                    if v is not None:
                        return v
                return None
            advn, decn = _first("advn"), _first("decn")
            net_ad = ((advn - decn) / (advn + decn)
                      if (advn and decn and (advn + decn) > 0) else None)
            pct50 = _first("pct50")
            highs = _first("nyhgh") or 0
            lows = _first("nylow") or 0
            breadth = intraday_trend.score_breadth_dir(net_ad, pct50, highs, lows)
        except Exception:  # noqa: BLE001
            breadth = intraday_trend.TrendSub(50.0, 0.0)

        # 3) SECTOR — participation + cyclical/defensive leadership.
        try:
            sq = schwab.get_quotes(sector_etfs) or {}
            pcts = {etf: (sq.get(etf) or {}).get("change_pct")
                    for etf in sector_etfs}
            n_green = sum(1 for p in pcts.values() if p is not None and p > 0)
            n_total = sum(1 for p in pcts.values() if p is not None)
            cyc = [p for etf, p in pcts.items()
                   if etf in _CYCLICAL and p is not None]
            dfn = [p for etf, p in pcts.items()
                   if etf in _DEFENSIVE and p is not None]
            if cyc and dfn:
                cyc_def_spread = intraday_trend._clamp(
                    (_mean(cyc) - _mean(dfn)) / _CYC_DEF_SCALE_INTRADAY, -1, 1)
            else:
                cyc_def_spread = None
            sector = intraday_trend.score_sector_participation(
                n_green, n_total, cyc_def_spread)
        except Exception:  # noqa: BLE001
            sector = intraday_trend.TrendSub(50.0, 0.0)

        # 4) VIX context.
        vix_change_pct = 0.0
        try:
            vq = schwab.get_quotes(_VIX_SYMS) or {}
            vix = _last(schwab.get_quote("$VIX")) or 0.0
            try:
                vdf = _safe_daily(schwab, "$VIX", 1)
                if vdf is not None and len(vdf) >= 2:
                    prev = float(vdf["close"].iloc[-2])
                    vix_change_pct = ((vix - prev) / prev * 100.0) if prev else 0.0
            except Exception:  # noqa: BLE001
                vix_change_pct = 0.0
            v1d = _last(vq.get("$VIX1D")) or 0.0
            v9d = _last(vq.get("$VIX9D")) or 0.0
            vix_sub = intraday_trend.score_vix_context(
                vix, vix_change_pct, v1d, v9d)
        except Exception:  # noqa: BLE001
            vix_sub = intraday_trend.TrendSub(50.0, 0.0)

        # 5) BLEND + volatility damper.
        scores = {"price": price.score, "breadth": breadth.score,
                  "sector": sector.score, "vix": vix_sub.score}
        confs = {"price": price.confidence, "breadth": breadth.confidence,
                 "sector": sector.confidence, "vix": vix_sub.confidence}
        raw_score, agg = intraday_trend.blend_trend(scores, confs)
        agg = round(agg * intraday_trend.vol_confidence_factor(vix_change_pct), 3)

        # 6) SMOOTH the directional needle.
        smoothed = intraday_trend.ema_smooth(prev_smoothed, raw_score, span=3)

        # 7) AGGRESSION axis — signed effort/urgency behind the move.
        #    (a) effort — SPY DAILY volume-vs-result confirmation (signed).
        effort_score, effort_conf = 0.0, 0.0
        try:
            daily = frames.get("1day")
            if daily is not None and len(daily):
                tail = daily.tail(30)
                ohlcv = [{"open": float(r["open"]), "high": float(r["high"]),
                          "low": float(r["low"]), "close": float(r["close"]),
                          "volume": float(r["volume"])}
                         for _, r in tail.iterrows()]
                er = effort_mod.score_effort(ohlcv)
                effort_score, effort_conf = float(er.score), float(er.confidence)
        except Exception:  # noqa: BLE001 — effort simply drops out (conf 0).
            effort_score, effort_conf = 0.0, 0.0

        #    (a2) rejection/defense — daily upper-wick exhaustion vs dip resilience.
        #         Sign already aligns with aggression (positive = no supply); NO flip.
        rej_score, rej_conf = 0.0, 0.0
        try:
            daily = frames.get("1day")
            if daily is not None and len(daily):
                tail = daily.tail(20)
                ohlc = [{"open": float(r["open"]), "high": float(r["high"]),
                         "low": float(r["low"]), "close": float(r["close"])}
                        for _, r in tail.iterrows()]
                rr = rejection_mod.score_rejection_defense(ohlc)
                rej_score, rej_conf = float(rr.score), float(rr.confidence)
        except Exception:  # noqa: BLE001 — rejection simply drops out (conf 0).
            rej_score, rej_conf = 0.0, 0.0

        #    (b) skew — 25-delta risk-reversal CHANGE; rising put fear -> NEGATIVE.
        rr_delta = None
        try:
            fs = flow_skew or {}
            entry = (fs.get("SPY") or fs.get("$SPX") or {})
            rr_delta = entry.get("rr_delta")
        except Exception:  # noqa: BLE001
            rr_delta = None
        if isinstance(rr_delta, (int, float)) and not isinstance(rr_delta, bool):
            skew_comp = -intraday_trend._clamp(rr_delta / SKEW_DELTA_SCALE, -1, 1)
            skew_conf = 1.0
        else:
            skew_comp, skew_conf = 0.0, 0.0
            rr_delta = None  # normalize non-numeric to None for the evidence line

        #    (c) flow — 5-day cap-weighted sector P/C change; rising put demand
        #        -> NEGATIVE.
        if sector_pc_delta is not None:
            flow_comp = -intraday_trend._clamp(sector_pc_delta / PC_DELTA_SCALE, -1, 1)
            flow_conf = 1.0
        else:
            flow_comp, flow_conf = 0.0, 0.0

        #    (d0) order_flow — streamed aggressor ratio (SPY). Positive = net
        #         buying = ALIGNED with aggression (NO sign flip). Missing/malformed
        #         → drops out (conf 0). Read via the pure flow_aggression_component.
        of_score, of_conf = 0.0, 0.0
        try:
            of = order_flow or {}
            spy_flow = of.get("SPY")
            if isinstance(spy_flow, dict):
                of_score, of_conf = order_flow_mod.flow_aggression_component(spy_flow)
        except Exception:  # noqa: BLE001 — order-flow simply drops out.
            of_score, of_conf = 0.0, 0.0

        #    (d0b) option_flow — streamed near-ATM SPY/QQQ option aggressor
        #          pressure (put vs call). Positive = net call-buying / put-selling
        #          = ALIGNED (NO flip). Missing/malformed → drops out (conf 0).
        opt_score, opt_conf = 0.0, 0.0
        try:
            of = order_flow or {}
            opt_flow = of.get("options")
            if isinstance(opt_flow, dict):
                opt_score, opt_conf = order_flow_mod.option_flow_component(opt_flow)
        except Exception:  # noqa: BLE001 — option-flow simply drops out.
            opt_score, opt_conf = 0.0, 0.0

        aggression, agg_conf = aggression_mod.blend_aggression(
            {"effort": effort_score, "skew": skew_comp, "flow": flow_comp,
             "rejection": rej_score, "order_flow": of_score,
             "option_flow": opt_score},
            {"effort": effort_conf, "skew": skew_conf, "flow": flow_conf,
             "rejection": rej_conf, "order_flow": of_conf,
             "option_flow": opt_conf})

        #    (d) profile shape — a balanced single-HVN session DAMPENS aggression
        #        (rotational balance -> more likely Neutral, sharpening that state).
        prof_shape, prof_bal = None, 0.0
        try:
            pframe = frames.get("15min")
            if pframe is None:
                pframe = frames.get("5min")
            if pframe is not None and len(pframe):
                prows = [{"high": float(r["high"]), "low": float(r["low"]),
                          "close": float(r["close"]), "volume": float(r["volume"])}
                         for _, r in pframe.iterrows()]
                ps = profile_mod.classify_profile_shape(prows)
                prof_shape, prof_bal = ps.shape, float(ps.balance_strength)
                if prof_bal > 0:
                    aggression = round(intraday_trend._clamp(
                        aggression * (1 - PROFILE_DAMP * prof_bal), -1.0, 1.0), 3)
        except Exception:  # noqa: BLE001 — profile simply drops out (no damping).
            prof_shape, prof_bal = None, 0.0

        # 8) EVIDENCE (dimensions that HAVE data) + five-state classification.
        evidence = [f"direction {smoothed:.0f}/100"]
        if sess.confidence > 0:
            evidence.append(f"session {sess.score:+.2f}")
        if effort_conf > 0:
            evidence.append(f"effort {effort_score:+.2f}")
        if rej_conf > 0:
            evidence.append(f"rejection/defense {rej_score:+.2f}")
        if rr_delta is not None:
            evidence.append(f"put-skew Δ {rr_delta:+.1f}")
        if sector_pc_delta is not None:
            evidence.append(f"sector P/C Δ {sector_pc_delta:+.2f}")
        if of_conf > 0:
            evidence.append(f"order-flow {of_score:+.2f}")
        if opt_conf > 0:
            evidence.append(f"option-flow {opt_score:+.2f}")
        if prof_shape is not None:
            evidence.append(f"profile {prof_shape} ({prof_bal:.2f})")
        evidence.append(f"aggression {aggression:+.2f}")

        ms = market_state.classify_market_state(smoothed, aggression,
                                                evidence=evidence)
        raw_state = ms.state

        # 9) Hysteresis, with a MIGRATION guard: a ``prior_committed`` that is not
        #    in the new-vocab ``STATE_LABELS`` is a stale OLD-vocab value from
        #    before this change — treat it as a cold start so the new-vocab state
        #    publishes immediately (no 2-read transient emitting an old string).
        prior_valid = (prior_committed
                       if prior_committed in market_state.STATE_LABELS else None)
        committed, hist = trend_regime.commit_state(
            raw_state, prior_history or [], prior_valid)

        return {
            "score": raw_score,
            "smoothed_score": smoothed,
            "state": committed,
            "raw_state": raw_state,
            "label": market_state.STATE_LABELS[committed],
            "description": market_state.STATE_DESCRIPTIONS[committed],
            "confidence": agg,
            "sub_scores": {"price": price.score, "breadth": breadth.score,
                           "sector": sector.score, "vix": vix_sub.score},
            "sub_confidence": {"price": price.confidence,
                               "breadth": breadth.confidence,
                               "sector": sector.confidence,
                               "vix": vix_sub.confidence},
            "state_history": hist,
            "aggression": aggression,
            "aggression_confidence": agg_conf,
            "evidence": list(ms.evidence),
        }
    except Exception:  # noqa: BLE001 — never raise into the refresh path.
        return _neutral_trend()


def _safe_intraday(schwab, symbol, minutes, days):
    try:
        return schwab.get_intraday_history(symbol, minutes=minutes, days=days)
    except Exception:  # noqa: BLE001
        return None


def _safe_daily(schwab, symbol, months):
    try:
        return schwab.get_daily_history(symbol, months=months)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Market-regime blended classifier (design:
# docs/plans/2026-07-23-market-regime-blended-classifier-design.md). The service
# function that fetches SPY bars + VIX + the options matrix, runs the pure
# regime modules, threads the temporal smoothing/commit state off the previous
# return, and returns a RegimeState-shaped dict. Never raises.
# ---------------------------------------------------------------------------

# Regime key -> display label (the "" / None fallbacks cover a cold-start /
# unclear commit that has no committed regime yet).
_REGIME_LABELS = {
    "mean_reversion": "Mean Reversion",
    "trending": "Trending",
    "breakout": "Breakout",
    "choppy": "Choppy",
    "crisis": "Crisis",
    "": "Unclear",
    None: "Unclear",
}

_REGIME_MODEL = "regime-v1"
_MATRIX_STALE_SEC = 300          # a matrix cache older than this is treated as absent
_BELOW_FLIP_FULL_FRAC = 0.01     # spot 1%+ below flip = full below-flip depth (1.0)

# TTL-shared SPY 5-min + daily fetch. The 15-min trend recompute (Task 8) reuses
# this within the window instead of re-fetching. The TTL keys off the PASSED
# ``now`` (tests supply a fixed ``now``), never wall-clock.
_SPY_5M_TTL_SEC = 240
_SPY_5M_CACHE: dict = {"ts": None, "bars": None, "daily": None}


def reset_spy_5m_cache():
    """Drop the cached SPY bars so the next fetch re-hits the proxy (tests)."""
    _SPY_5M_CACHE.update(ts=None, bars=None, daily=None)


# Lookback (CALENDAR days) for the multi-session 5-min frame that today's session
# is sliced from and whose trailing sessions form the Bollinger-width percentile
# basis. MUST be one of Schwab's allowed periodType=day values — the API rejects
# anything else with a 400 ("valid values for period are: [1, 2, 3, 4, 5, 10]"),
# which silently starved the whole classifier to "Unclear" (live-caught
# 2026-07-23; every unit test used fakes and never saw the 400). 10 calendar days
# ≈ 7 trading sessions — a comfortable basis for the percentile.
_SPY_PERIOD_DAYS_ALLOWED = (1, 2, 3, 4, 5, 10)
_SPY_5M_SESSIONS = 10


def _fetch_spy_5m(schwab, now_ts):
    """(bars_5m, daily) for SPY, memoized for ``_SPY_5M_TTL_SEC`` off ``now_ts``.

    ``bars_5m`` is the MULTI-session 5-min frame (~5 sessions) — the caller
    splits out today's session and uses the trailing sessions as the Bollinger-
    width percentile basis (same 5-min timescale as today's value). Cache is
    written ONLY on a good fetch (``bars is not None``), so a transient proxy
    blip can't poison the TTL window (mirrors ``compute_30d_trend``)."""
    cached_ts = _SPY_5M_CACHE["ts"]
    if cached_ts is not None and (now_ts - cached_ts) <= _SPY_5M_TTL_SEC:
        return _SPY_5M_CACHE["bars"], _SPY_5M_CACHE["daily"]
    bars = _safe_intraday(schwab, "SPY", 5, _SPY_5M_SESSIONS)
    daily = _safe_daily(schwab, "SPY", 3)
    if bars is not None:
        _SPY_5M_CACHE.update(ts=now_ts, bars=bars, daily=daily)
    return bars, daily


def _today_session(frame):
    """The rows of a multi-session 5-min frame belonging to its LATEST local date
    (today's session), reset-indexed. The session-scoped evidence helpers (opening
    range, VWAP-hold, whipsaw, wicks) MUST see one session only, not ~5 days.

    Falls back to the whole frame when the timestamps can't be grouped (defensive —
    a degraded read is safer than raising)."""
    if frame is None:
        return None
    try:
        dates = frame["datetime"].dt.date
    except Exception:  # noqa: BLE001 — no usable timestamps -> use the frame as-is.
        return frame
    try:
        latest = dates.max()
        session = frame[dates == latest]
        if len(session) == 0:
            return frame
        return session.reset_index(drop=True)
    except Exception:  # noqa: BLE001
        return frame


# $VIX1D prior-session close — the denominator of the day-over-day spike, the
# most direct crisis tell. It changes ONCE per session, so it is memoized per
# LOCAL DATE: at most ONE daily-history probe per session, success or failure.
# A failed probe is deliberately NOT retried on a timer: Schwab serves no
# $VIX1D history at all (see _VIX1D_OBS), so a retry cadence would burn ~144
# guaranteed-failing proxy calls a day for a source the session latch already
# covers. One probe per day still picks it up automatically if Schwab ever
# starts serving it.
_VIX1D_PREV_CACHE: dict = {"date": None, "close": None}


# In-process session latch — the FALLBACK when the daily history is unavailable.
# LIVE-VERIFIED 2026-07-23: the proxy's /pricehistory returns ``{"empty": true,
# "candles": []}`` for $VIX1D (and VIX1D) while $VIX and $VIX3M return candles —
# Schwab simply does not serve 1-day-VIX history. The service already QUOTES
# $VIX1D on every refresh, so the last value observed on the PREVIOUS local date
# is the honest stand-in for its close, at zero extra fetches. It is process
# state: after a restart there is no prior observation and the spike tell reads
# None (never a fabricated 0%) until the next session rollover.
_VIX1D_OBS: dict = {"date": None, "last": None, "prev_date": None,
                    "prev_close": None}
# ...and it EXPIRES. A latched observation is only a "prior session close" if it
# is recent: 4 calendar days spans a Friday->Tuesday long weekend (weekend +
# holiday), while a longer gap means the service was down and the last thing it
# saw is NOT the prior session — serving that would manufacture a bogus spike.
_VIX1D_OBS_MAX_AGE_DAYS = 4


def reset_vix1d_prev_cache():
    """Drop the cached $VIX1D prior close + session latch (tests)."""
    _VIX1D_PREV_CACHE.update(date=None, close=None)
    _VIX1D_OBS.update(date=None, last=None, prev_date=None, prev_close=None)


def _observe_vix1d(value, today):
    """Roll the in-process session latch; return the PRIOR session's last
    observed $VIX1D, or None. See ``_VIX1D_OBS``. Never raises."""
    obs = _VIX1D_OBS
    if obs["date"] != today:
        if obs["date"] is not None and obs["last"] is not None:
            obs["prev_date"], obs["prev_close"] = obs["date"], obs["last"]
        obs.update(date=today, last=None)
    try:
        v = float(value)
        if math.isfinite(v) and v > 0:
            obs["last"] = v
    except (TypeError, ValueError):
        pass
    prev_date, prev_close = obs["prev_date"], obs["prev_close"]
    if prev_date is None or prev_close is None or prev_date >= today:
        return None
    if _date_gap_days(prev_date, today) > _VIX1D_OBS_MAX_AGE_DAYS:
        return None      # stale (a service gap) — degrade to None, never guess.
    return prev_close


def _date_gap_days(earlier_iso, later_iso):
    """Calendar days between two ISO dates; a huge number when unparseable (so
    an unreadable stamp fails CLOSED — treated as stale)."""
    from datetime import date
    try:
        a = date.fromisoformat(str(earlier_iso))
        b = date.fromisoformat(str(later_iso))
    except (TypeError, ValueError):
        return 10 ** 6
    return (b - a).days


def _local_date_iso():
    from datetime import datetime
    return datetime.now().date().isoformat()


def _prior_daily_close(frame, today_iso):
    """The most recent daily close STRICTLY BEFORE ``today_iso``, or None.

    During RTH the last daily row is today's UNFINISHED bar, so the prior
    session's close is the row before it; off-hours the frame may already end
    on the prior session. Keying off the row DATE handles both without guessing.
    Falls back to the second-to-last close when the frame carries no usable
    ``datetime`` column. Never raises."""
    try:
        closes = [float(c) for c in frame["close"].tolist()]
    except Exception:  # noqa: BLE001 — no usable frame.
        return None
    if len(closes) < 2:
        return None
    try:
        dates = [str(d) for d in frame["datetime"].dt.date.tolist()]
    except Exception:  # noqa: BLE001 — no date column; fall back below.
        dates = None
    if dates is not None and len(dates) == len(closes):
        for close, day in zip(reversed(closes), reversed(dates)):
            if day < today_iso:
                return close if math.isfinite(close) and close > 0 else None
        return None
    close = closes[-2]
    return close if math.isfinite(close) and close > 0 else None


def _vix1d_prev_close(schwab):
    """Prior session's $VIX1D close, memoized per local date, or None.
    Fully defensive — any failure returns None (so ``vix1d_spike_pct`` degrades
    to None rather than fabricating a spike)."""
    today = _local_date_iso()
    cached = _VIX1D_PREV_CACHE
    if cached["date"] == today:
        # Probed already today — return the result (a None means "Schwab didn't
        # serve it", which is the normal case; the latch covers us).
        return cached["close"]
    close = None
    try:
        frame = _safe_daily(schwab, "$VIX1D", 1)
        if frame is not None and len(frame) >= 2:
            close = _prior_daily_close(frame, today)
    except Exception:  # noqa: BLE001 — degrade to None, never raise.
        close = None
    _VIX1D_PREV_CACHE.update(date=today, close=close)
    return close


def _fetch_vix(schwab):
    """{"vix","vix1d","vix3m"[,"vix1d_prev"]} from the proxy, or None.

    ``vix1d_prev`` (the prior session's $VIX1D close) is what lets
    ``regime_evidence._vix1d_spike_pct`` fire the day-over-day crisis tell — the
    most direct crisis input. Two sources, in order: the daily history (memoized
    per local date, so the 120 s crisis check costs only the quote call), then the
    in-process session latch (Schwab serves no $VIX1D history — see
    ``_VIX1D_OBS``). The key is OMITTED when neither has a value, so the spike
    degrades to None rather than to a fabricated 0%."""
    try:
        q = schwab.get_quotes(["$VIX", "$VIX1D", "$VIX3M"]) or {}
        vix = _last(q.get("$VIX"))
        v1 = _last(q.get("$VIX1D"))
        v3 = _last(q.get("$VIX3M"))
        if vix is None and v1 is None and v3 is None:
            return None
        out = {"vix": vix, "vix1d": v1, "vix3m": v3}
        prev = _vix1d_prev_close(schwab)
        latched = _observe_vix1d(v1, _local_date_iso())
        if prev is None:
            prev = latched
        if prev is not None:
            out["vix1d_prev"] = prev
        return out
    except Exception:  # noqa: BLE001
        return None


def _parse_ts_unix(ts):
    """Best-effort unix seconds from a matrix ``ts`` (ISO-8601 string or number),
    else None."""
    if isinstance(ts, bool):
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            from datetime import datetime
            return datetime.fromisoformat(ts).timestamp()
        except (ValueError, TypeError):
            return None
    return None


def _below_flip_depth(row):
    """0..1 below-flip depth when the row is ``below`` its gamma flip with a
    usable spot/flip, else None. A spot ``_BELOW_FLIP_FULL_FRAC`` (1%) or more
    below flip reads full depth (1.0)."""
    if not isinstance(row, dict) or row.get("gex_regime") != "below":
        return None
    try:
        spot = float(row.get("spot"))
        flip = float(row.get("flip"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(spot) and math.isfinite(flip)) or spot <= 0:
        return None
    # clamp at 0 too: a contradictory below-flip row (spot>flip from a racing
    # matrix) must not emit a negative depth.
    return max(0.0, min(1.0, ((flip - spot) / spot) / _BELOW_FLIP_FULL_FRAC))


def _matrix_row(matrix, now_ts):
    """The SPY (fallback $SPX) row from the options matrix as an evidence-shaped
    ``{gex_regime, below_flip_deep}``, or None. Staleness-gated: a matrix ``ts``
    parsing older than ``_MATRIX_STALE_SEC`` (or unparseable/missing) is treated
    as absent, as is a missing row or a ``gex_regime`` of ``na``."""
    if not isinstance(matrix, dict):
        return None
    age_from = _parse_ts_unix(matrix.get("ts"))
    if age_from is None or (now_ts - age_from) > _MATRIX_STALE_SEC:
        return None
    rows = matrix.get("rows")
    if not isinstance(rows, list):
        return None
    row = None
    for want in ("SPY", "$SPX"):
        for r in rows:
            if isinstance(r, dict) and r.get("symbol") == want:
                row = r
                break
        if row is not None:
            break
    if row is None:
        return None
    regime = row.get("gex_regime")
    if regime not in ("above", "below"):
        return None
    return {"gex_regime": regime, "below_flip_deep": _below_flip_depth(row)}


_PRIOR_WIDTH_STRIDE = 6   # step ~30 min between rolling 20-close width samples


def _prior_widths(frame_5m, exclude_last=0):
    """Rolling 20-close Bollinger widths (20, 2σ) stepped across the MULTI-session
    5-min frame — the SAME 5-min timescale as today's ``bb_width_pctile`` value,
    so the percentile is meaningful (a daily-window basis would dwarf a ~100-min
    intraday width, pinning every session at pctile ~0). ``exclude_last`` drops
    today's session bars so today's value isn't ranked against itself. Returns a
    plain list, or None when the trailing history is too thin."""
    try:
        closes = frame_5m["close"].astype(float).tolist()
    except Exception:  # noqa: BLE001
        return None
    if exclude_last > 0:
        closes = closes[:max(0, len(closes) - exclude_last)]
    n = _regime_vol.BOLL_PERIOD
    if len(closes) < n + _PRIOR_WIDTH_STRIDE:
        return None
    widths = []
    for end in range(n, len(closes) + 1, _PRIOR_WIDTH_STRIDE):
        w = _regime_vol.bollinger_width_pct(closes[:end])
        if w is not None:
            widths.append(w)
    return widths or None


def _regime_iso(now_ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(now_ts), tz=timezone.utc).isoformat()


def _regime_carry(prior):
    """(fast, slow, commit, sample_ts) pulled from a previous return dict, or the
    cold-start defaults when ``prior`` carries no smoothing state."""
    if isinstance(prior, dict) and prior.get("_fast") is not None:
        commit = prior.get("_commit")
        if not isinstance(commit, market_regime.CommitState):
            commit = market_regime.CommitState()
        return prior.get("_fast"), prior.get("_slow"), commit, prior.get("_sample_ts")
    return None, None, market_regime.CommitState(), None


def _unclear_shell(now_ts, prior):
    """A fully-shaped ``unclear`` RegimeState dict. Preserves the prior smoothing
    carry when present so a transient fetch failure doesn't reset the EMAs."""
    fast, slow, commit, sample_ts = _regime_carry(prior)
    if sample_ts is None:
        sample_ts = int(now_ts)
    return {
        "ts": _regime_iso(now_ts),
        "as_of": _regime_iso(now_ts),
        "memberships": {r: 1.0 / len(market_regime.REGIMES)
                        for r in market_regime.REGIMES},
        "raw": {r: 0.0 for r in market_regime.REGIMES},
        "confidence": 0.0,
        "unclear": True,
        "label": "Unclear",
        "committed_label": "",
        "transition": None,
        "evidence": [],
        "version_info": {"model": _REGIME_MODEL},
        "_fast": fast,
        "_slow": slow,
        "_commit": commit,
        "_sample_ts": sample_ts,
    }


def compute_market_regime(schwab, matrix=None, vix=None, prior=None,
                          now=None) -> dict:
    """Assemble the blended market-regime state from live SPY bars + VIX + the
    options matrix, threading the temporal smoothing/commit state off ``prior``.

    ``schwab`` proxy client; ``matrix`` the ``cache:options:matrix`` payload
    (``{"rows":[...], "ts":...}``) or None; ``vix`` a
    ``{"vix","vix1d","vix3m"[,"vix1d_prev"]}`` dict or None (fetched via
    ``get_quotes`` when None); ``prior`` the previous return dict (carries
    ``_fast``/``_slow``/``_commit``/``_sample_ts``) or None on cold start;
    ``now`` unix seconds (defaults to ``time.time()``).

    Returns the publishable RegimeState fields PLUS the private carry fields
    (``_fast``/``_slow``/``_commit``/``_sample_ts``, held by the handler and
    passed back as ``prior`` — NOT cached). Defensive: any failure returns a
    fully-shaped ``unclear`` dict, never raises."""
    now_ts = now if now is not None else time.time()
    try:
        frame_5m, daily = _fetch_spy_5m(schwab, now_ts)
        # No usable bars (proxy down / premarket) -> the unclear shell, which
        # preserves the prior smoothing carry (a transient miss can't reset it).
        if frame_5m is None or len(frame_5m) == 0:
            return _unclear_shell(now_ts, prior)

        # Today's session ONLY for the session-scoped evidence; the trailing
        # sessions of the SAME 5-min frame are the Bollinger-width pctile basis.
        bars_5m = _today_session(frame_5m)
        if bars_5m is None or len(bars_5m) == 0:
            return _unclear_shell(now_ts, prior)

        if vix is None:
            vix = _fetch_vix(schwab)
        matrix_row = _matrix_row(matrix, now_ts)
        prior_widths = _prior_widths(frame_5m, exclude_last=len(bars_5m))

        ev = regime_evidence.evidence_from_bars(
            bars_5m, daily, vix, matrix_row, prior_widths)
        scores = market_regime.score_regimes(ev)

        # Thread the temporal state off the previous return.
        pf, ps, pc, pts = _regime_carry(prior)
        dt_sec = (now_ts - pts) if pts else 0
        fast, slow = market_regime.smooth(pf, ps, scores.memberships, dt_sec)

        raw_crisis = scores.raw["crisis"]
        attacked = market_regime.crisis_attacked(raw_crisis)
        if attacked:
            fast = market_regime.apply_crisis_attack(fast, raw_crisis)

        commit = market_regime.commit_label(fast, pc)
        if attacked:
            # Crisis force-commit — bypass the hysteresis streak entirely.
            commit = market_regime.CommitState(committed="crisis", streak=0)

        transition = market_regime.detect_transition(fast, slow)
        committed = commit.committed

        return {
            "ts": _regime_iso(now_ts),
            "as_of": _regime_iso(now_ts),
            "memberships": fast,          # the smoothed (crisis-attacked) vector
            "raw": scores.raw,
            "confidence": scores.confidence,
            "unclear": scores.unclear,
            "label": _REGIME_LABELS.get(committed, "Unclear"),
            "committed_label": committed or "",
            "transition": transition,
            "evidence": list(scores.evidence),
            "version_info": {"model": _REGIME_MODEL},
            "_fast": fast,
            "_slow": slow,
            "_commit": commit,
            "_sample_ts": int(now_ts),
        }
    except Exception:  # noqa: BLE001 — never raise into the refresh path.
        return _unclear_shell(now_ts, prior)


# Self-fetch cache for compute_30d_trend: the 15-min trend recompute calls it
# every cycle, but a ~30-DAY structural gauge changes ~daily — refetching SPY
# 12-mo + 11 sector histories 26×/day (~1,150 proxy calls) bought nothing.
# Applies ONLY to the fully-self-fetching (no-args) path; explicit-args calls
# (tests / offline) bypass it entirely.
TREND_30D_TTL_SEC = 3600  # recompute at most hourly
_TREND_30D_CACHE = {"ts": 0.0, "result": None}


def reset_trend_30d_cache():
    """Drop the cached 30d trend so the next self-fetch call recomputes (tests)."""
    _TREND_30D_CACHE.update(ts=0.0, result=None)


def compute_30d_trend(spy_daily_df=_FETCH, sector_month_pcts=_FETCH) -> dict:
    """~30-day *structural* directional trend (price structure + sector breadth).

    The daily-horizon analog of ``compute_intraday_trend`` for the GUI's "30-Day
    Avg" Market-Trend gauge: no intraday VWAP, no breadth/VIX, no smoothing or
    hysteresis. When an argument is OMITTED it is fetched internally (so the
    function is self-contained) — and the self-fetching path is TTL-cached
    ~hourly (see ``_TREND_30D_CACHE``); passing an explicit ``None`` / ``{}``
    means the caller has no data and the corresponding sub-score degrades to
    neutral. Defensive: a catastrophic failure returns a neutral dict."""
    self_fetch = spy_daily_df is _FETCH and sector_month_pcts is _FETCH
    if self_fetch:
        cached = _TREND_30D_CACHE["result"]
        if (cached is not None
                and time.monotonic() - _TREND_30D_CACHE["ts"] < TREND_30D_TTL_SEC):
            return dict(cached)
    try:
        if spy_daily_df is _FETCH:
            from services import _proxy
            spy_daily_df = _safe_daily(_proxy.schwab_client, "SPY", 12)
        if sector_month_pcts is _FETCH:
            sector_month_pcts = _fetch_sector_month_pcts()

        # PRICE — daily structural alignment + RSI/ADX/MACD (no VWAP at this horizon).
        if spy_daily_df is None or len(spy_daily_df) < 50:
            price = intraday_trend.TrendSub(50.0, 0.0)
        else:
            frames = {"1day": spy_daily_df}
            price_now = float(spy_daily_df["close"].iloc[-1])
            align = technical.calculate_ema_alignment(frames, price_now)
            align_pct = float(align.get("alignment_percentage", 0.0))
            hist = technical.macd_histogram_series(spy_daily_df)
            macd_hist = (float(hist.iloc[-1])
                         if hist is not None and len(hist) else 0.0)
            rsi = float(technical.calculate_rsi(spy_daily_df))
            adx = float(technical.calculate_adx(spy_daily_df))
            price = intraday_trend.score_price(
                align_pct, 0.0, macd_hist, rsi, adx, n_timeframes=1)

        # SECTOR — participation + cyc/def leadership from month-% moves.
        pcts = sector_month_pcts or {}
        if not pcts:
            sector = intraday_trend.TrendSub(50.0, 0.0)
        else:
            n_green = sum(1 for p in pcts.values() if p is not None and p > 0)
            n_total = sum(1 for p in pcts.values() if p is not None)
            cyc = [p for etf, p in pcts.items()
                   if etf in _CYCLICAL and p is not None]
            dfn = [p for etf, p in pcts.items()
                   if etf in _DEFENSIVE and p is not None]
            if cyc and dfn:
                cyc_def_spread = intraday_trend._clamp(
                    (_mean(cyc) - _mean(dfn)) / _CYC_DEF_SCALE_30D, -1, 1)
            else:
                cyc_def_spread = None
            sector = intraday_trend.score_sector_participation(
                n_green, n_total, cyc_def_spread)

        scores = {"price": price.score, "sector": sector.score}
        confs = {"price": price.confidence, "sector": sector.confidence}
        score, agg = intraday_trend.blend_trend(scores, confs)
        state = intraday_trend.score_to_state(score)
        result = {
            "score": score,
            "state": state,
            "label": trend_regime.STATE_LABELS[state],
            "description": trend_regime.STATE_DESCRIPTIONS[state],
            "confidence": agg,
            "sub_scores": {"price": price.score, "sector": sector.score},
        }
        if self_fetch:  # cache only the successful self-fetching path
            _TREND_30D_CACHE.update(ts=time.monotonic(), result=dict(result))
        return result
    except Exception:  # noqa: BLE001
        return {
            "score": 50.0,
            "state": "range",
            "label": trend_regime.STATE_LABELS["range"],
            "description": trend_regime.STATE_DESCRIPTIONS["range"],
            "confidence": 0.0,
            "sub_scores": {"price": 50.0, "sector": 50.0},
        }


def _fetch_sector_month_pcts():
    """``{etf: month_pct}`` for the sector ETFs (used by compute_30d_trend when
    no override is passed). Defensive: returns ``{}`` on any failure."""
    try:
        import sectors_ref
        sd = sectors_ref.load_sectors_data()
        etfs = [r["etf"] for r in sd
                if r.get("kind") == "sector" and r.get("etf")]
        _closes, trends = _fetch_closes(etfs, months=3)
        return {etf: t.get("month_pct") for etf, t in trends.items()}
    except Exception:  # noqa: BLE001
        return {}


def _divergence_named(snapshot):
    """[(display_name, score)] for confident, scored components — mirrors the
    page's ``divergence_named`` (in-composite + credit_pulse, name order)."""
    scores = (snapshot or {}).get("component_scores") or {}
    confs = (snapshot or {}).get("component_confidence") or {}
    out = []
    for key, name in COMPONENTS:
        s = _safe_float(scores.get(key))
        if s > 0 and _safe_float(confs.get(key)) > 0:
            out.append((name, s))
    return out


def derive_composite_extras(live, snaps, spy, trend=None, trend_30d=None):
    """Scoring-derived values for the GUI's composite view.

    Computes weights / size-bias-signal / velocity / divergence from the same
    inputs the page used to derive inline (now centralized in this process where
    ``scoring`` resolves to sentiment's package). The ``trend`` / ``trend_30d``
    directional Market-Trend payloads are now computed on a 15-min cadence in
    ``handlers.refresh`` and threaded in here; when absent (None) a neutral
    placeholder is supplied so the GUI always has a fully-shaped trend. Defensive:
    any sub-failure yields a safe default without raising, so a partial compute
    never aborts the refresh.
    """
    latest = live or (snaps[-1] if snaps else None)
    total = _safe_float((latest or {}).get("composite", {}).get("total_score"))

    try:
        size, bias, signal = signal_band(total)
    except Exception:  # noqa: BLE001
        size, bias, signal = "—", "", ""

    # Prior composite series: when showing live, today=live and the prior
    # series is the full backfill; when showing backfill, exclude the last
    # (it's "today"). Mirrors the page's L579 prior_scores derivation.
    try:
        prior_scores = (composite_series(snaps or [])[1] if live
                        else composite_series((snaps or [])[:-1])[1])
    except Exception:  # noqa: BLE001
        prior_scores = []

    velocity = {"text": "", "flag": ""}
    try:
        v = scoring_composite.velocity(list(prior_scores), total)
        roc3, roc5, z = v["roc_3d"], v["roc_5d"], v["z_20d"]
        parts = [
            f"3d ROC: {roc3:+.2f}" if roc3 is not None else "3d ROC: —",
            f"5d ROC: {roc5:+.2f}" if roc5 is not None else "5d ROC: —",
            f"20d Z: {z:+.2f}" if z is not None else "20d Z: —",
        ]
        velocity = {
            "text": " | ".join(parts),
            "flag": (f"REGIME BREAK: {z:+.2f}σ from 20d mean"
                     if v["regime_break"] else ""),
        }
    except Exception:  # noqa: BLE001
        velocity = {"text": "", "flag": ""}

    divergence = ""
    try:
        divergence = scoring_composite.divergence(_divergence_named(latest)) or ""
    except Exception:  # noqa: BLE001
        divergence = ""

    return {
        "weights": dict(WEIGHTS),
        "size": size,
        "bias": bias,
        "signal": signal,
        "velocity": velocity,
        "divergence": divergence,
        "trend": trend if trend is not None else _neutral_trend(),
        "trend_30d_ago": trend_30d if trend_30d is not None else _neutral_trend(),
    }


def derive_sector_summary(sector):
    """Cap-weighted-pct + sector score for the GUI's sector summary line.
    Defensive: missing sector / scoring failure -> safe defaults."""
    if not sector:
        return {"wpct": None, "score": 0.0}
    sd = sector.get("sector_data")
    quotes = sector.get("quotes")
    try:
        wpct, _ = scoring_sector.weighted_sector_pct(sd, quotes)
    except Exception:  # noqa: BLE001
        wpct = None
    try:
        score = scoring_sector.sectors_score(sd, quotes)
    except Exception:  # noqa: BLE001
        score = 0.0
    return {"wpct": wpct, "score": score}


def rotation_assessment():
    """Off-thread: RRG-vs-SPY sector-rotation assessment via the copied engine.

    Ported verbatim from the page's former ``_compute()``. Returns
    ``(assessment|None, error_str|None)``. ``sector_rotation_assessment`` is
    imported lazily here (it does heavy frame work) — in the sentiment service
    process ``import scoring`` already resolves to sentiment's package, so this
    carries no cross-app-collision risk."""
    import sector_rotation_assessment as rotation_tool
    from datetime import date
    symbols = [rotation_tool.BENCHMARK] + list(rotation_tool.SECTOR_ETFS)
    frame, _missing = rotation_tool.build_aligned_frame(symbols)
    if frame is None:
        return None, "No data from proxy (is schwab-proxy running?)"
    a = rotation_tool.build_assessment(frame, date.today().isoformat())
    if a is None or not a.get("sectors"):
        return None, (f"Insufficient daily history (need {rotation_tool.MIN_BARS} "
                      f"aligned bars).")
    return a, None


def rotation_weights():
    """``{etf: sp_weight}`` for sector rows — ported from the page's
    ``_sector_weights()`` (reuses the module's ``sectors_ref`` import)."""
    import sectors_ref
    return {r["etf"]: r.get("sp_weight", 0.0)
            for r in sectors_ref.load_sectors_data()
            if r.get("kind") == "sector" and r.get("etf")}


def rotation_risk_threshold():
    """The engine's ``RISK_THRESHOLD`` so the GUI can render the headline
    detail without importing the engine."""
    import sector_rotation_assessment as rotation_tool
    return rotation_tool.RISK_THRESHOLD


def _bridge_trend(intraday, spy):
    """Bridge trend_regime dict: the intraday model's directional state/confidence
    + trend_score/sub_scores, merged onto the DAILY classify dict's structural
    sma_*/drawdown/spy_close (kept for the additive-only bridge contract).

    When no intraday trend is available yet (the cold-start window before the
    first 15-min recompute after a restart), keep the daily dict's structural
    back-compat fields but OVERRIDE the published ``state``/``label``/
    ``description``/``raw_state`` to the new-vocab NEUTRAL — regime_filter is
    rekeyed to the five-state vocabulary and would fail-open (run ungated) on the
    daily dict's OLD-vocab ``state``. So the bridge ALWAYS carries a recognized
    new-vocab ``state`` (cold start -> neutral, the conservative default)."""
    daily = build_trend_dict(spy) or {}
    if not intraday or not intraday.get("state"):
        if not daily:
            return None
        neutral = dict(daily)
        neutral.update({
            "state": "neutral",
            "label": market_state.STATE_LABELS["neutral"],
            "description": market_state.STATE_DESCRIPTIONS["neutral"],
            "raw_state": "neutral",
            "confidence": 0.0,
            "trend_score": 50.0,
            "sub_scores": {},
        })
        return neutral
    merged = dict(daily)
    merged.update({
        "state": intraday.get("state"),
        "label": intraday.get("label"),
        "description": intraday.get("description"),
        "raw_state": intraday.get("raw_state"),
        "confidence": intraday.get("confidence"),
        "trend_score": intraday.get("smoothed_score", intraday.get("score")),
        "sub_scores": intraday.get("sub_scores"),
    })
    return merged


def sector_pc_delta():
    """5-trading-day change in the cap-weighted cross-sector Put/Call ratio.

    Reads the daily ``sector_pcr`` store (written by handlers each RTH refresh)
    and returns ``latest - (5-trading-days-ago)``, or ``None`` if the store has
    too few dates / a None endpoint / any failure. This is the internal reader
    Phase 2's market-state classifier calls — there is no cache view."""
    conn = None
    try:
        from services.sentiment_svc import sector_pcr_history_db as _db
        conn = _db.connect()
        return _db.sector_pc_delta(conn)
    except Exception:  # noqa: BLE001 — defensive: never raise into a caller.
        return None
    finally:
        # A fresh connection is opened every 15-min recompute — close it or the
        # service leaks ~26 SQLite handles/day.
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def build_and_write_bridge(snaps, spy, live, sector, trend=None):
    """Build the bridge payload from cache/state data and write it. Defensive."""
    try:
        import bridge
        from datetime import datetime, timezone
        latest = live or (snaps[-1] if snaps else None)
        if not latest:
            return
        prior = composite_series(snaps or [])[1]
        trend = _bridge_trend(trend, spy)
        sec_arg = None
        if sector:
            sec_arg = {"sector_data": sector.get("sector_data"),
                       "quotes": sector.get("quotes"), "dual": sector.get("dual")}
        payload = build_bridge_payload(latest, prior, spy or [],
                                       datetime.now(timezone.utc).isoformat(),
                                       sector=sec_arg, trend=trend)
        bridge.write_bridge(payload)
    except Exception:
        pass
