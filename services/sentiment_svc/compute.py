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
import datetime as _dt
import logging
import math
import sys
import time

from repo_paths import SENTIMENT, SHARED
from services._parallel import parallel_map
from services.sentiment_svc import momentum_db

log = logging.getLogger(__name__)

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
from scoring import momentum  # noqa: E402
from scoring import momentum_regime  # noqa: E402
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


from scoring.put_call import pcr_from_chain  # noqa: E402  (the ONE copy)
from services import _degrade  # noqa: E402

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
        _degrade.degraded("sentiment.build_trend_dict")
        return None


# ── intraday directional Market Trend (Phase 3) ──────────────────────────────
# Cyclical vs defensive sector ETFs (SPDR symbols, as in sectors_ref rows) for
# the leadership spread that feeds ``score_sector_participation``.
_CYCLICAL = {"XLK", "XLY", "XLF", "XLI", "XLB", "XLE", "XLC"}
_DEFENSIVE = {"XLP", "XLU", "XLV", "XLRE"}

# %-spread that maps cyclical-vs-defensive leadership to the full ±1 of
# ``cyc_def_spread``. Intraday day-moves are small (~1% = decisive); month-moves
# are ~3× larger, so the structural gauge uses a wider scale. The week sits
# between: 3.0·√(5/21) ≈ 1.46, i.e. the same random-walk √t scaling the 30d
# constant already implies, rounded. UNMEASURED — anchoring off the intraday
# constant instead (1.0·√5 ≈ 2.2) argues for a wider scale, so treat 1.5 as a
# first-pass tunable, not a calibration.
_CYC_DEF_SCALE_INTRADAY = 1.0
_CYC_DEF_SCALE_7D = 1.5
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

# Sentinel so an OMITTED structural-trend arg (``compute_7d_trend`` /
# ``compute_30d_trend``) fetches internally, while an explicit ``None`` (caller
# has no data) stays neutral rather than re-fetching.
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


# The four DIRECTION terms of ``intraday_trend.score_price``, each as
# ``(neutral_substitute, direction_weight)``. The weights mirror that function's
# ``direction = 0.5*a + 0.2*v + 0.15*m + 0.15*r``; the substitutes are the input
# values that make each term contribute exactly zero. ``adx`` is deliberately
# absent — it is a MAGNITUDE scaler, not a direction term (see below).
_PRICE_DIRECTION_TERMS = (
    (0.0, 0.50),    # alignment_pct  -> a = 0
    (0.0, 0.20),    # price_vs_vwap_pct -> v = 0
    (0.0, 0.15),    # macd_hist      -> m = 0
    (50.0, 0.15),   # rsi            -> r = 0
)

# ``adx`` enters as ``adx_factor = _clamp(adx / 40, 0.3, 1.0)``. Substituting 0.0
# floors that factor at 0.3, i.e. it collapses the needle TOWARD 50 — which is
# the degradation a missing magnitude reading should cause. It carries no
# direction weight, so it does not scale the confidence.
_ADX_NEUTRAL = 0.0


def _finite_score_price(align_pct, vwap_pct, macd_hist, rsi, adx, n_timeframes):
    """``intraday_trend.score_price`` with non-finite indicators degraded to
    NEUTRAL instead of clamped to a BOUND.

    ``intraday_trend._clamp`` is ``max(lo, min(hi, v))``, and ``min(hi, nan)``
    returns ``hi`` — so every NaN indicator clamps to the HIGH bound. All five
    inputs NaN scored **92.50 at confidence 1.0** through ``compute_intraday_trend``
    (the live Day gauge) and **82.50 at the unchanged 0.333** through
    ``_structural_trend``: a data outage rendered as a confident buy signal, with
    nothing in the confidence to warn the reader. This is the same failure
    ``_finite_pcts`` fixes for the SECTOR sub-score, on the PRICE sub-score.

    A non-finite direction input is replaced by the value that zeroes its term
    and its weight is withheld from the confidence, so the sub-score moves toward
    50 AND says it knows less; all five non-finite gives ``TrendSub(50.0, 0.0)``,
    which drops the 45%-weighted price input out of ``blend_trend`` entirely. A
    non-finite ``adx`` floors the magnitude scaler (see ``_ADX_NEUTRAL``).

    **Finite input is bit-identical by construction**, not by arithmetic
    coincidence: an all-finite call returns the untouched ``score_price`` result
    from the early return below, never reaching the substitution branch. Both
    price-scoring call sites go through here — ``compute_intraday_trend`` and
    ``_structural_trend`` — so there is exactly ONE ``score_price`` call in this
    module and a later "consistency" edit cannot re-introduce the bug on one side.
    Never raises: a non-numeric input is treated as non-finite."""
    raw = (align_pct, vwap_pct, macd_hist, rsi, adx)
    finite = [_as_finite(v) for v in raw]
    if all(f is not None for f in finite):
        # Every input finite -> the original call, byte-for-byte untouched.
        return intraday_trend.score_price(align_pct, vwap_pct, macd_hist, rsi,
                                          adx, n_timeframes=n_timeframes)
    vals, kept = [], 0.0
    for f, (neutral, weight) in zip(finite[:4], _PRICE_DIRECTION_TERMS):
        if f is None:
            vals.append(neutral)
        else:
            vals.append(f)
            kept += weight
    sub = intraday_trend.score_price(
        vals[0], vals[1], vals[2], vals[3],
        finite[4] if finite[4] is not None else _ADX_NEUTRAL,
        n_timeframes=n_timeframes)
    scale = max(0.0, min(1.0, kept))
    return intraday_trend.TrendSub(sub.score,
                                   round(sub.confidence * scale, 3),
                                   sub.interp)


def _as_finite(v):
    """``float(v)`` if it is finite, else ``None``. Mirrors the per-value test in
    ``_finite_pcts``; factored out so both filters cannot drift apart."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


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
                price = _finite_score_price(
                    align_pct, vwap_pct, macd_hist, rsi, adx,
                    n_timeframes=len(frames))
        except Exception:  # noqa: BLE001
            _degrade.degraded("sentiment.compute_intraday_trend")
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
            _degrade.degraded("sentiment.compute_intraday_trend")
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
            _degrade.degraded("sentiment.compute_intraday_trend")
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
            _degrade.degraded("sentiment.compute_intraday_trend")
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
        _degrade.degraded("sentiment.compute_intraday_trend")
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

# Regime key -> display label. The words live in ``market_regime.REGIME_DISPLAY``
# (one source, beside the evidence that earns them); an unknown / cold-start /
# unclear commit with no committed regime reads "Unclear".
def _regime_label(key, direction=0, strong=False):
    return market_regime.regime_label(key, direction, strong)

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


def _direction_carry(prior):
    """The direction hysteresis state off a previous return, or a cold-start
    neutral. Carried independently of the smoothing carry so an ``unclear``
    sample (which has no bars, hence no slope) can preserve it rather than
    silently dropping a committed direction."""
    if isinstance(prior, dict):
        state = prior.get("_dir")
        if isinstance(state, market_regime.DirectionState):
            return state
    return market_regime.DirectionState()


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
        "evidence_detail": [],
        "direction": 0,
        "direction_strong": False,
        "version_info": {"model": _REGIME_MODEL},
        "_fast": fast,
        "_slow": slow,
        "_commit": commit,
        "_dir": _direction_carry(prior),
        "_sample_ts": sample_ts,
    }


def compute_market_regime(schwab, matrix=None, vix=None, prior=None,
                          now=None, trend_score=None) -> dict:
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

        # Direction is DISPLAY ONLY and is claimed only when this module's own
        # signed slope agrees with the Market Trend composite — see
        # ``market_regime.direction_sign``. ``trend_score`` absent (an older
        # caller, or a trend recompute that hasn't landed yet) reads neutral.
        slope = ev.get("ema_slope_atr")
        dir_state = market_regime.commit_direction(
            market_regime.direction_sign(slope, trend_score),
            _direction_carry(prior))
        strong = bool(dir_state.committed) and market_regime.direction_strong(slope)

        return {
            "ts": _regime_iso(now_ts),
            "as_of": _regime_iso(now_ts),
            "memberships": fast,          # the smoothed (crisis-attacked) vector
            "raw": scores.raw,
            "confidence": scores.confidence,
            "unclear": scores.unclear,
            "label": _regime_label(committed, dir_state.committed, strong),
            "committed_label": committed or "",
            "transition": transition,
            "evidence": list(scores.evidence),
            "evidence_detail": evidence_detail(scores),
            "direction": dir_state.committed,
            "direction_strong": strong,
            "version_info": {"model": _REGIME_MODEL},
            "_fast": fast,
            "_slow": slow,
            "_commit": commit,
            "_dir": dir_state,
            "_sample_ts": int(now_ts),
        }
    except Exception:  # noqa: BLE001 — never raise into the refresh path.
        _degrade.degraded("sentiment.compute_market_regime")
        return _unclear_shell(now_ts, prior)


def _neutral_structural_trend():
    """Neutral structural-trend dict — the catastrophic-failure fallback shared by
    both structural horizons, so the GUI always sees a valid, fully-shaped dict."""
    return {
        "score": 50.0,
        "state": "range",
        "label": trend_regime.STATE_LABELS["range"],
        "description": trend_regime.STATE_DESCRIPTIONS["range"],
        "confidence": 0.0,
        "sub_scores": {"price": 50.0, "sector": 50.0},
    }


def _finite_pcts(pcts):
    """Sector %-moves keyed by ETF, with missing / non-finite values DROPPED.

    A NaN must not survive into the leadership spread: ``intraday_trend._clamp``
    is ``max(lo, min(hi, v))``, which returns the HIGH bound for NaN — so one bad
    close (``float('nan')`` survives ``_fetch_closes``' conversion, and
    ``_pct_change_n`` propagates it) would render as MAXIMUM cyclical leadership
    at unchanged confidence. Dropping it lowers ``n_total``, and with it the
    sub-score's confidence, which is what a missing sector should do.

    This cannot change any FINITE-input result, which is why the pre-existing
    ``compute_30d_trend`` tests stayed green unmodified. Two incidental
    widenings, both unreachable from ``_fetch_closes`` (which emits floats or
    ``None``) and named here because the claim is behaviour preservation: a
    numeric STRING now coerces where the original raised, and a junk value now
    degrades only the SECTOR sub-score where the original raised out to the
    caller's neutral fallback and lost the price sub-score too."""
    out = {}
    for etf, p in (pcts or {}).items():
        v = _as_finite(p)
        if v is not None:
            out[etf] = v
    return out


def _structural_trend(spy_daily_df, sector_pcts, cyc_def_scale) -> dict:
    """Structural directional trend from daily SPY bars + sector %-moves.

    The shared body of ``compute_7d_trend`` and ``compute_30d_trend`` — they
    differ ONLY in which horizon's sector %-moves they pass and in
    ``cyc_def_scale``. Pure (no fetching, no caching) and it does NOT swallow
    exceptions: the two callers own the fetch, the TTL cache and the neutral
    fallback."""
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
        price = _finite_score_price(
            align_pct, 0.0, macd_hist, rsi, adx, n_timeframes=1)

    # SECTOR — participation + cyc/def leadership from this horizon's %-moves.
    pcts = _finite_pcts(sector_pcts)
    if not pcts:
        sector = intraday_trend.TrendSub(50.0, 0.0)
    else:
        n_green = sum(1 for p in pcts.values() if p > 0)
        n_total = len(pcts)
        cyc = [p for etf, p in pcts.items() if etf in _CYCLICAL]
        dfn = [p for etf, p in pcts.items() if etf in _DEFENSIVE]
        if cyc and dfn:
            cyc_def_spread = intraday_trend._clamp(
                (_mean(cyc) - _mean(dfn)) / cyc_def_scale, -1, 1)
        else:
            cyc_def_spread = None
        sector = intraday_trend.score_sector_participation(
            n_green, n_total, cyc_def_spread)

    scores = {"price": price.score, "sector": sector.score}
    confs = {"price": price.confidence, "sector": sector.confidence}
    score, agg = intraday_trend.blend_trend(scores, confs)
    state = intraday_trend.score_to_state(score)
    return {
        "score": score,
        "state": state,
        "label": trend_regime.STATE_LABELS[state],
        "description": trend_regime.STATE_DESCRIPTIONS[state],
        "confidence": agg,
        "sub_scores": {"price": price.score, "sector": sector.score},
    }


# Self-fetch cache for compute_7d_trend. Shorter than the 30d window because a
# WEEK horizon moves faster than a month — but the sector fan-out behind it is
# shared and hourly (``SECTOR_PCTS_TTL_SEC``), so the only thing this cadence
# actually re-pulls is SPY's daily frame. Applies ONLY to the fully-self-fetching
# (no-args) path; explicit-args calls (tests / offline) bypass it entirely.
TREND_7D_TTL_SEC = 1800  # recompute at most every 30 min
_TREND_7D_CACHE = {"ts": 0.0, "result": None}


def reset_trend_7d_cache():
    """Drop the cached 7d trend so the next self-fetch call recomputes (tests)."""
    _TREND_7D_CACHE.update(ts=0.0, result=None)


def compute_7d_trend(spy_daily_df=_FETCH, sector_week_pcts=_FETCH) -> dict:
    """~7-day *structural* directional trend (price structure + sector breadth).

    The weekly-horizon sibling of ``compute_30d_trend``, feeding the Market Trend
    ring's Week arc. Same argument semantics: an OMITTED argument is fetched
    internally (and that path is TTL-cached, see ``_TREND_7D_CACHE``), while an
    explicit ``None`` / ``{}`` means the caller has no data and that sub-score
    degrades to neutral. Defensive: a catastrophic failure returns a neutral dict.

    LIMITATION — the weekly and monthly structural trends share the SAME daily
    price sub-score. ``technical.calculate_ema_alignment``'s EMA periods are
    fixed, so handing it a shorter frame would not change the answer; the two
    horizons differ ONLY in the sector %-move horizon and ``cyc_def_scale``.
    Consequence: the Week and Month arcs track each other closely and diverge
    mainly on sector rotation. A genuinely weekly price read needs
    weekly-resampled SPY bars — a deliberate follow-up, not part of this change."""
    self_fetch = spy_daily_df is _FETCH and sector_week_pcts is _FETCH
    if self_fetch:
        cached = _TREND_7D_CACHE["result"]
        if (cached is not None
                and time.monotonic() - _TREND_7D_CACHE["ts"] < TREND_7D_TTL_SEC):
            return dict(cached)
    try:
        if spy_daily_df is _FETCH:
            from services import _proxy
            spy_daily_df = _safe_daily(_proxy.schwab_client, "SPY", 12)
        if sector_week_pcts is _FETCH:
            sector_week_pcts = _fetch_sector_week_pcts()
        result = _structural_trend(spy_daily_df, sector_week_pcts,
                                   _CYC_DEF_SCALE_7D)
    except Exception:  # noqa: BLE001
        return _neutral_structural_trend()
    if self_fetch:  # cache only the successful self-fetching path
        _TREND_7D_CACHE.update(ts=time.monotonic(), result=dict(result))
    return result


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
    neutral. Defensive: a catastrophic failure returns a neutral dict.

    NOTE the name is historical: this is a monthly-HORIZON structural read, not
    the trend as it stood 30 days ago and not a 30-day average."""
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
        result = _structural_trend(spy_daily_df, sector_month_pcts,
                                   _CYC_DEF_SCALE_30D)
    except Exception:  # noqa: BLE001
        return _neutral_structural_trend()
    if self_fetch:  # cache only the successful self-fetching path
        _TREND_30D_CACHE.update(ts=time.monotonic(), result=dict(result))
    return result


# One sector fan-out serves BOTH structural horizons. ``_fetch_closes`` already
# derives week AND month %-moves from the same daily frames, so the weekly gauge
# costs no extra Schwab calls — a second independent fetch would have doubled ~11
# proxy calls for data already on the wire. TTL-cached because both self-fetching
# gauges call this on the 15-min trend recompute while daily bars change ~daily.
SECTOR_PCTS_TTL_SEC = 3600  # refetch at most hourly
_SECTOR_PCTS_CACHE = {"ts": 0.0, "result": None}


def reset_sector_pcts_cache():
    """Drop the cached sector %-moves so the next call refetches (tests)."""
    _SECTOR_PCTS_CACHE.update(ts=0.0, result=None)


def _fetch_sector_pcts():
    """``{"week": {etf: pct}, "month": {etf: pct}}`` for the sector ETFs.

    Both horizons come off ONE ``_fetch_closes`` fan-out (see the note above) and
    are TTL-cached for ``SECTOR_PCTS_TTL_SEC``. An EMPTY result is deliberately
    not cached, so a transient proxy blip can't poison the window for an hour
    (same rule as ``live_composite``'s P/C cache and ``_fetch_spy_5m``). Returns
    copies, so a caller mutating its result can't corrupt the cache. Defensive:
    ``{"week": {}, "month": {}}`` on any failure."""
    cached = _SECTOR_PCTS_CACHE["result"]
    if (cached is not None
            and time.monotonic() - _SECTOR_PCTS_CACHE["ts"] < SECTOR_PCTS_TTL_SEC):
        return {"week": dict(cached["week"]), "month": dict(cached["month"])}
    try:
        import sectors_ref
        sd = sectors_ref.load_sectors_data()
        etfs = [r["etf"] for r in sd
                if r.get("kind") == "sector" and r.get("etf")]
        _closes, trends = _fetch_closes(etfs, months=3)
        result = {"week": {etf: t.get("week_pct") for etf, t in trends.items()},
                  "month": {etf: t.get("month_pct") for etf, t in trends.items()}}
    except Exception:  # noqa: BLE001
        return {"week": {}, "month": {}}
    if result["month"] or result["week"]:  # cache only a real fetch
        _SECTOR_PCTS_CACHE.update(ts=time.monotonic(), result=result)
    return {"week": dict(result["week"]), "month": dict(result["month"])}


def _fetch_sector_month_pcts():
    """``{etf: month_pct}`` — the month view of the shared sector fetch (used by
    ``compute_30d_trend`` when no override is passed)."""
    return _fetch_sector_pcts()["month"]


def _fetch_sector_week_pcts():
    """``{etf: week_pct}`` — the week view of the shared sector fetch (used by
    ``compute_7d_trend`` when no override is passed)."""
    return _fetch_sector_pcts()["week"]


# Shape of ``velocity.values`` when nothing could be computed — the three keys
# are ALWAYS present so a renderer never has to distinguish "key absent" from
# "value None"; only the latter means "not enough history".
_EMPTY_VELOCITY_VALUES = {"roc_3d": None, "roc_5d": None, "z_20d": None}

# Which regimes' evidence reads as ADVERSE. A line's severity is not a property
# of its wording but of the regime that produced it: "11 EMA whipsaws" is a
# choppy-scorer line and "ADX 36 rising" is a trending-scorer line, and no amount
# of pattern-matching the copy recovers that reliably. Emitted here rather than
# in the engine because severity is a display-facing semantic state — exactly the
# kind of thing CLAUDE.md wants Tier 2 to hand Tier 1 pre-decided.
_ADVERSE_REGIMES = frozenset({"choppy", "crisis"})


def evidence_detail(scores):
    """``[{"text", "regime", "severity"}, ...]`` from a ``RegimeScores``.

    Tolerates a scores object predating ``evidence_detail`` (falls back to the
    flat ``evidence`` list with an unknown regime and neutral severity), and any
    malformed entry, so a classifier change can never abort a refresh."""
    try:
        detail = list(getattr(scores, "evidence_detail", None) or [])
    except Exception:  # noqa: BLE001
        detail = []
    if not detail:
        try:
            return [{"text": str(t), "regime": "", "severity": "info"}
                    for t in (getattr(scores, "evidence", None) or [])]
        except Exception:  # noqa: BLE001
            return []
    out = []
    for d in detail:
        if not isinstance(d, dict):
            continue
        regime = str(d.get("regime") or "")
        out.append({"text": str(d.get("text") or ""), "regime": regime,
                    "severity": "warn" if regime in _ADVERSE_REGIMES else "info"})
    return out


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


def derive_composite_extras(live, snaps, spy, trend=None, trend_30d=None,
                            trend_7d=None):
    """Scoring-derived values for the GUI's composite view.

    Computes weights / size-bias-signal / velocity / divergence from the same
    inputs the page used to derive inline (now centralized in this process where
    ``scoring`` resolves to sentiment's package). The ``trend`` / ``trend_7d`` /
    ``trend_30d`` directional Market-Trend payloads are now computed on a 15-min
    cadence in ``handlers.refresh`` and threaded in here; when absent (None) a
    neutral placeholder is supplied so the GUI always has a fully-shaped trend
    for every horizon of the Market Trend ring. Defensive: any sub-failure yields
    a safe default without raising, so a partial compute never aborts the refresh.

    ``trend_7d`` is LAST so the existing positional call shape is unaffected.
    """
    latest = live or (snaps[-1] if snaps else None)
    # ``scored`` is the composite total ONLY when one was actually read; the
    # band below is derived from it rather than from ``total``, and that
    # distinction is the whole point of the two names. ``signal_band`` is a
    # TOTAL function over the reals (``>=9 / >=7 / >=5 / >=3 / else``), so both
    # of ``_safe_float``'s degrade paths — its 0.0 default, and a NaN, which
    # fails every ``>=`` — fall out of its last branch as the most bearish
    # reading in the vocabulary at the smallest position size. Absence has to
    # publish NO band instead: ``None``, which is the shape all three renderers
    # of these words already dash on. ``total`` stays byte-for-byte what it was
    # — ``velocity`` below has its own missing-input policy, which is not this
    # line's to change.
    raw_total = (latest or {}).get("composite", {}).get("total_score")
    scored = _as_finite(raw_total)
    total = _safe_float(raw_total)

    try:
        size, bias, signal = (signal_band(scored) if scored is not None
                              else (None, None, None))
    except Exception:  # noqa: BLE001
        size, bias, signal = None, None, None

    # Prior composite series: when showing live, today=live and the prior
    # series is the full backfill; when showing backfill, exclude the last
    # (it's "today"). Mirrors the page's L579 prior_scores derivation.
    try:
        prior_scores = (composite_series(snaps or [])[1] if live
                        else composite_series((snaps or [])[:-1])[1])
    except Exception:  # noqa: BLE001
        prior_scores = []

    velocity = {"text": "", "flag": "", "values": _EMPTY_VELOCITY_VALUES.copy()}
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
            # The SAME three numbers the text above formats, published raw so a
            # renderer can draw them (bipolar meters) without parsing prose.
            # None where the history is too short — a meter must then render "no
            # read", not a zero, which is a real position on a signed scale.
            "values": {"roc_3d": roc3, "roc_5d": roc5, "z_20d": z},
        }
    except Exception:  # noqa: BLE001
        _degrade.degraded("sentiment.derive_composite_extras")
        velocity = {"text": "", "flag": "", "values": _EMPTY_VELOCITY_VALUES.copy()}

    divergence = ""
    divergence_detail = None
    try:
        named = _divergence_named(latest)
        divergence = scoring_composite.divergence(named) or ""
        # Structured form of the SAME finding. Deliberately gated on the engine's
        # string being non-empty: the >=4-point rule stays the engine's to own,
        # and this only names the two components it fired on. Sorting mirrors
        # ``scoring.composite.divergence`` exactly (ascending; lo first, hi last).
        if divergence and len(named) >= 2:
            ordered = sorted(named, key=lambda pair: pair[1])
            (lo_name, lo_s), (hi_name, hi_s) = ordered[0], ordered[-1]
            divergence_detail = {"high": {"name": hi_name, "score": float(hi_s)},
                                 "low": {"name": lo_name, "score": float(lo_s)}}
    except Exception:  # noqa: BLE001
        divergence, divergence_detail = "", None

    return {
        "weights": dict(WEIGHTS),
        "size": size,
        "bias": bias,
        "signal": signal,
        "velocity": velocity,
        "divergence": divergence,
        "divergence_detail": divergence_detail,
        "trend": trend if trend is not None else _neutral_trend(),
        "trend_7d": trend_7d if trend_7d is not None else _neutral_trend(),
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
        _degrade.degraded("sentiment.build_and_write_bridge")
        pass


# ---------------------------------------------------------------------------
# Momentum cascade — nightly, on its own cache key. NOT a sentiment component:
# scoring/__init__.WEIGHTS is untouched and the bridge never sees any of this.
# ---------------------------------------------------------------------------

MOMENTUM_BENCHMARK = "SPY"
MOMENTUM_BARS = 252
MOMENTUM_MIN_BARS = 90                    # == scoring.momentum.TREND_WINDOW
MOMENTUM_LIQUIDITY_WINDOW = 20
# Two floors, because the question differs by role. For a STOCK it is "can I
# hold a position" — several small caps on the Stocks tab cannot support one and
# would otherwise top the leaderboard on a thin-volume pop. For an industry or
# sector ETF it is only "is this price series trustworthy": the ETF is a
# measurement instrument and the trade is expressed through its constituents, so
# a position-sized floor there silently deletes a third of the cross-section
# (measured: 23 of 70 industry ETFs on 2026-07-28).
MOMENTUM_MIN_DOLLAR_VOLUME = 5_000_000.0
MOMENTUM_MIN_ETF_DOLLAR_VOLUME = 250_000.0
MOMENTUM_TOP_QUARTILE = 75.0
MOMENTUM_DISPERSION_WINDOW = 5
MOMENTUM_FETCH_WORKERS = 6


def _momentum_universe():
    """The three levels, plus the industries that cannot have their own score.

    Four of the Stocks tab's 74 industries name an ETF that another industry
    already owns (MJ, XRT, BETZ and VEGI are each listed twice in the
    workbook). Scoring one price series under two labels would put duplicate
    rows in the cross-section and invent a "two industries agree" signal, so
    those four are reported as orphans instead — their constituents still roll
    up to the sector level and are still scored individually.
    """
    import sectors_ref

    by_industry = sectors_ref.constituents_by_industry()
    etf_by_key, sector_etf = {}, {}
    for row in sectors_ref.load_sectors_data():
        if row["kind"] == "industry":
            etf_by_key[(row["sector"], row["label"])] = row["etf"]
        elif row["etf"]:
            sector_etf[row["sector"]] = row["etf"]

    industries, orphans, members_by_sector = [], [], {}
    for (sector, industry), members in by_industry.items():
        members_by_sector.setdefault(sector, []).extend(members)
        etf = etf_by_key.get((sector, industry))
        if not etf:
            orphans.append({"sector": sector, "industry": industry,
                            "reason": "duplicate_etf"})
            continue
        industries.append({"sector": sector, "industry": industry,
                           "etf": etf, "members": list(members)})

    sectors = [{"sector": sector, "etf": sector_etf[sector],
                "members": sorted(set(members))}
               for sector, members in members_by_sector.items()
               if sector_etf.get(sector)]
    return {"stocks": sectors_ref.stock_symbols(), "industries": industries,
            "sectors": sectors, "orphans": orphans}


def _momentum_fetch_symbols(universe):
    """Every symbol needing bars, deduped, benchmark included."""
    out, seen = [], set()
    for symbol in ([MOMENTUM_BENCHMARK] + list(universe["stocks"])
                   + [i["etf"] for i in universe["industries"]]
                   + [s["etf"] for s in universe["sectors"]]):
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def _momentum_history_months(stored_max, session_date):
    """Smallest proxy window that still spans the gap.

    get_daily_history takes a period, not a start date, so a delta fetch means
    asking for the shortest period that covers what is missing.
    """
    if not stored_max:
        return 12
    try:
        gap = (_dt.date.fromisoformat(str(session_date))
               - _dt.date.fromisoformat(str(stored_max))).days
    except (TypeError, ValueError):
        return 12
    if gap <= 0:
        return 0                      # already current — no fetch at all
    if gap <= 25:
        return 1
    if gap <= 80:
        return 3
    if gap <= 170:
        return 6
    return 12


def _momentum_sync_bars(symbols, conn, session_date, client):
    """Delta-fetch each symbol's missing bars into the store. Returns failures."""
    def _one(symbol):
        try:
            months = _momentum_history_months(momentum_db.max_date(conn, symbol),
                                              session_date)
            if months == 0:
                return symbol, None, True
            df = client.get_daily_history(symbol, months=months)
        except Exception:
            log.exception("momentum: history fetch failed for %s", symbol)
            return symbol, None, False
        if df is None or getattr(df, "empty", True):
            return symbol, None, False
        rows = []
        for rec in df.to_dict("records"):
            stamp = rec.get("datetime")
            date = stamp.date() if hasattr(stamp, "date") else None
            if date is None:
                continue
            rows.append({"symbol": symbol, "date": date.isoformat(),
                         "open": rec.get("open"), "high": rec.get("high"),
                         "low": rec.get("low"), "close": rec.get("close"),
                         "volume": rec.get("volume")})
        return symbol, rows, bool(rows)

    failed = []
    for symbol, rows, ok in parallel_map(_one, symbols, MOMENTUM_FETCH_WORKERS):
        if rows:
            momentum_db.upsert_bars(conn, rows)
        if not ok:
            failed.append(symbol)
    return failed


def _momentum_series(conn, symbols):
    """{symbol: (closes, dollar_volumes)} from the store."""
    out = {}
    for symbol in symbols:
        rows = momentum_db.bars(conn, symbol, limit=MOMENTUM_BARS)
        closes = [r["close"] for r in rows if r["close"]]
        dollars = [(r["close"] or 0.0) * (r["volume"] or 0.0) for r in rows]
        out[symbol] = (closes, dollars)
    return out


def _momentum_admit(symbol, series, floor=MOMENTUM_MIN_DOLLAR_VOLUME):
    """(closes, reason) — reason is set when the symbol must be dropped.

    A dropped symbol leaves the z-score population entirely; it must never
    become a zero in the distribution.
    """
    closes, dollars = series.get(symbol, ([], []))
    if not closes:
        return None, "no_quote"
    if len(closes) < MOMENTUM_MIN_BARS:
        return None, "insufficient_bars"
    window = dollars[-MOMENTUM_LIQUIDITY_WINDOW:]
    if window and sum(window) / len(window) < floor:
        return None, "liquidity"
    return closes, None


def _momentum_score_level(entries, bench_closes):
    """PURE: raw components -> within-level z-scores -> blend -> rank.

    ``entries`` is [{symbol, closes, participation, **passthrough}]. Every
    component is z-scored within THIS level, so a stock is ranked against
    stocks and an industry against industries.
    """
    raw = []
    for entry in entries:
        closes = entry["closes"]
        excess, slope = momentum.relative_strength(closes, bench_closes)
        raw.append({
            "trend": momentum.trend_strength(closes),
            "excess": excess,
            "slope": slope,
            "accel": momentum.acceleration(closes),
            "path": momentum.path_quality(closes),
            "participation": entry.get("participation"),
        })

    z = {key: momentum.zscore_within_level([r[key] for r in raw])
         for key in ("trend", "excess", "slope", "accel", "path", "participation")}

    rows = []
    for i, entry in enumerate(entries):
        # Excess return and RS slope live on different scales, so each is
        # z-scored on its own and averaged into the single 'rs' component.
        parts = [v for v in (z["excess"][i], z["slope"][i]) if v is not None]
        rs = sum(parts) / len(parts) if parts else None
        components = {"trend": z["trend"][i], "rs": rs,
                      "accel": z["accel"][i], "path": z["path"][i]}
        if raw[i]["participation"] is not None:
            components["participation"] = z["participation"][i]
        row = {k: v for k, v in entry.items() if k != "closes"}
        row.update({
            "score": momentum.blend(components),
            "components": components,
            "raw": {k: raw[i][k] for k in ("trend", "excess", "slope",
                                           "accel", "path")},
            "participation": raw[i]["participation"],
        })
        rows.append(row)

    for row, pct in zip(rows, momentum.percentile_rank([r["score"] for r in rows])):
        row["percentile"] = pct
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0.0), r["symbol"]))
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def _momentum_dispersion(series, symbols, window=MOMENTUM_DISPERSION_WINDOW):
    """(current dispersion, its own history) from the stored bars.

    The history is recomputed from the bars already in hand rather than kept in
    its own table — 252 bars yield far more than the 60 observations the
    percentile needs, at no extra I/O.
    """
    aligned = [series[s][0] for s in symbols
               if series.get(s) and len(series[s][0]) > window]
    if len(aligned) < 2:
        return None, []
    depth = min(len(c) for c in aligned)
    history = []
    for offset in range(depth - window - 1, 0, -1):
        rets = {}
        for i, closes in enumerate(aligned):
            base = closes[-offset - 1 - window]
            if base:
                rets[i] = closes[-offset - 1] / base - 1.0
        value = momentum_regime.dispersion(rets)
        if value is not None:
            history.append(value)
    current = {i: (c[-1] / c[-1 - window] - 1.0)
               for i, c in enumerate(aligned) if c[-1 - window]}
    return momentum_regime.dispersion(current), history


def _momentum_vix_term(client):
    """Near/far VIX ratio (VIX9D/VIX); below 1.0 is contango. None if absent."""
    try:
        quotes = client.get_quotes(["$VIX", "$VIX9D"]) or {}
        near = _safe_float(_last(quotes.get("$VIX9D")), 0.0)
        far = _safe_float(_last(quotes.get("$VIX")), 0.0)
    except Exception:
        return None
    return near / far if near > 0 and far > 0 else None


def _momentum_prior_session(conn, level, session_date):
    """Most recent stored session for ``level`` before ``session_date``."""
    try:
        row = conn.execute(
            "SELECT MAX(session_date) FROM momentum_scores "
            "WHERE level = ? AND session_date < ?",
            (str(level), str(session_date))).fetchone()
    except Exception:
        return None
    return row[0] if row and row[0] else None


def _momentum_regime_block(verdict, dispersion_value=None):
    return {"state": verdict.state, "label": verdict.label,
            "description": verdict.description, "lookback": verdict.lookback,
            "crash_risk": verdict.crash_risk, "dispersion": dispersion_value,
            "dispersion_pct": verdict.dispersion_pct, "vol_pct": verdict.vol_pct,
            "reasons": list(verdict.reasons)}


def compute_momentum(session_date=None, conn=None, client=None):
    """Nightly momentum cascade -> the cache:sentiment:momentum payload.

    Defensive throughout: a dead proxy yields empty levels and a neutral
    regime, never a raise.
    """
    from datetime import datetime

    session_date = str(session_date or _dt.date.today().isoformat())
    payload = {"schema": 1,
               "computed_at": datetime.now().astimezone().isoformat(),
               "session_date": session_date, "regime": {},
               "levels": {"sector": [], "industry": [], "stock": []},
               "rank_history": {}, "excluded": []}
    owns_conn = conn is None
    try:
        if client is None:
            from services import _proxy
            client = _proxy.schwab_client
        if conn is None:
            conn = momentum_db.connect()

        universe = _momentum_universe()
        symbols = _momentum_fetch_symbols(universe)
        _momentum_sync_bars(symbols, conn, session_date, client)
        series = _momentum_series(conn, symbols)

        excluded = [dict(o, symbol=o["industry"]) for o in universe["orphans"]]
        etfs = ({i["etf"] for i in universe["industries"]}
                | {s["etf"] for s in universe["sectors"]} | {MOMENTUM_BENCHMARK})
        admitted = {}
        for symbol in symbols:
            floor = (MOMENTUM_MIN_ETF_DOLLAR_VOLUME if symbol in etfs
                     else MOMENTUM_MIN_DOLLAR_VOLUME)
            closes, reason = _momentum_admit(symbol, series, floor)
            if reason:
                excluded.append({"symbol": symbol, "reason": reason})
            else:
                admitted[symbol] = closes
        bench = admitted.get(MOMENTUM_BENCHMARK) or []

        industry_of = {}
        for ind in universe["industries"]:
            for member in ind["members"]:
                industry_of.setdefault(member, (ind["sector"], ind["industry"]))
        stock_entries = []
        for symbol in universe["stocks"]:
            if symbol not in admitted:
                continue
            sector, industry = industry_of.get(symbol, ("", ""))
            stock_entries.append({"symbol": symbol, "closes": admitted[symbol],
                                  "sector": sector, "industry": industry})

        def _participation(members):
            return momentum.participation([admitted[m] for m in members
                                           if m in admitted])

        industry_entries = [
            {"symbol": i["etf"], "label": i["industry"], "sector": i["sector"],
             "closes": admitted[i["etf"]],
             "participation": _participation(i["members"])}
            for i in universe["industries"] if i["etf"] in admitted]
        sector_entries = [
            {"symbol": s["etf"], "label": s["sector"], "closes": admitted[s["etf"]],
             "participation": _participation(s["members"])}
            for s in universe["sectors"] if s["etf"] in admitted]

        levels = {"sector": _momentum_score_level(sector_entries, bench),
                  "industry": _momentum_score_level(industry_entries, bench),
                  "stock": _momentum_score_level(stock_entries, bench)}

        top = MOMENTUM_TOP_QUARTILE
        sector_pct = {r["label"]: (r["percentile"] or 0.0) for r in levels["sector"]}
        industry_pct = {r["label"]: (r["percentile"] or 0.0)
                        for r in levels["industry"]}
        for row in levels["stock"]:
            row["alignment"] = [
                sector_pct.get(row.get("sector"), 0.0) >= top,
                industry_pct.get(row.get("industry"), 0.0) >= top,
                (row["percentile"] or 0.0) >= top,
            ]

        for level, rows in levels.items():
            prior = _momentum_prior_session(conn, level, session_date)
            prev = {r["symbol"]: r["rank"]
                    for r in momentum_db.scores(conn, prior, level)} if prior else {}
            for row in rows:
                row["rank_prev"] = prev.get(row["symbol"])

        current, history = _momentum_dispersion(series, list(admitted))
        verdict = momentum_regime.classify(
            bench, vix_term=_momentum_vix_term(client),
            dispersion_pct=momentum_regime.dispersion_percentile(current, history))

        payload["levels"] = levels
        payload["excluded"] = excluded
        payload["regime"] = _momentum_regime_block(verdict, current)
        # Persist BEFORE reading rank_history so the ribbon includes today's
        # session — otherwise it is empty on the very first run.
        for level, rows in levels.items():
            momentum_db.write_scores(conn, session_date, level, rows)
        payload["rank_history"] = {
            level: momentum_db.rank_history(conn, level) for level in levels}
        momentum_db.prune(conn)
    except Exception:
        log.exception("momentum: compute failed")
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    if not payload["regime"]:
        payload["regime"] = _momentum_regime_block(momentum_regime.classify([]))
    return payload


# --- Bull / Bear Map (cache:sentiment:bullbear) -------------------------------
# The nightly cascade above already scores all three levels with their parent
# links. This adds only the live layer: ONE batched /quotes call for every
# distinct symbol, merged onto a COPY of the cached rows.
#
# The relative axis is measured against MOMENTUM_BENCHMARK — the same SPY the
# nightly cascade scores excess against, deliberately not a second spelling of
# it.
# See docs/plans/2026-08-19-bull-bear-map-design.md.

BULLBEAR_LEVELS = ("sector", "industry", "stock")


def bullbear_symbols(levels):
    """Every distinct symbol across the three levels, plus the benchmark.

    Deduped because an industry ETF is usually a scored stock as well, and
    MOMENTUM_BENCHMARK can itself be a scored row. Order preserved; the
    benchmark goes last, and only when no row already carries it.
    """
    out, seen = [], set()
    for name in BULLBEAR_LEVELS:
        for row in (levels or {}).get(name) or []:
            symbol = (row or {}).get("symbol")
            if symbol and symbol not in seen:
                seen.add(symbol)
                out.append(symbol)
    # One more symbol, not one more request: 374 came back in a single call
    # (measured 2026-08-19), so a live relative axis can be computed against the
    # benchmark's own day move for no new proxy round-trip and no new schedule.
    if MOMENTUM_BENCHMARK not in seen:
        out.append(MOMENTUM_BENCHMARK)
    return out


def _quoted_day_pct(quotes, symbol):
    """``change_pct`` for one symbol, or None when there is no usable number.

    ONE spelling of this extraction, called by both the per-row merge and the
    benchmark lookup: the mapping, the missing-symbol fallthrough and the
    coercion have to agree, and this repo has a history of the same read
    existing in several spellings that then drift.

    None means OMITTED, never "unchanged" — ``_extract_change_pct``
    (schwab-proxy/proxy_client.py) falls through to a literal 0.0 for a symbol
    it does return, so 0.0 is a reading and absence is not one.

    Deliberately STRICTER than ``_as_finite`` alone on both ends, matching the
    Tier-1 ``webgui/pages/fmt.num`` contract:

    * ``bool`` is rejected. ``float(True)`` is a finite 1.0, so ``_as_finite``
      would pass it through — and a True in this field is a shape error, not a
      1% move.
    * a numeric STRING is rejected. ``_as_finite("1.5")`` returns 1.5, but this
      reads one specific producer — the flattened ``get_quotes`` mapping, whose
      ``change_pct`` is already a float or a literal 0.0 — so a string means
      that shape changed, and coercing it would hide the change behind a
      plausible number.

    ``_as_finite`` owns the finiteness half, so NaN/±inf cannot reach the
    payload: a NaN PASSES the downstream ``is None`` usability gate, then loses
    every ``> 0`` quadrant test and renders as a confident falling/lagging row,
    and — being unequal to itself — permanently defeats ``skip_unchanged``.
    """
    pct = ((quotes or {}).get(symbol) or {}).get("change_pct")
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        return None
    return _as_finite(pct)


def merge_live(levels, quotes):
    """Attach ``day_pct`` and ``day_excess`` to a COPY of every row.

    Copies rather than mutates: ``levels`` comes from the cached momentum
    payload, which /sentiment/momentum renders too — a live field written into
    it would leak into that page and into the next merge. A shallow copy is
    enough because nothing here writes below the top level.

    ``quotes`` is the flattened mapping ``SchwabProxyClient.get_quotes`` returns
    (schwab-proxy/proxy_client.py:284) — ``{symbol: {"change_pct": ..., ...}}``,
    no nested ``quote`` envelope. A symbol the proxy left out leaves ``day_pct``
    None, which renders as a dash; defaulting to 0.0 would render as
    "unchanged", a different and false claim.

    ``day_excess`` is the relative axis: today's move less MOMENTUM_BENCHMARK's
    today, resolved once per merge, from the same batched quotes the rows use.
    """
    bench = _quoted_day_pct(quotes, MOMENTUM_BENCHMARK)
    merged = {}
    for name in BULLBEAR_LEVELS:
        rows = []
        for row in (levels or {}).get(name) or []:
            out_row = dict(row or {})
            day = _quoted_day_pct(quotes, out_row.get("symbol"))
            out_row["day_pct"] = day
            # None when EITHER side is absent.
            out_row["day_excess"] = (
                (day - bench) if (day is not None and bench is not None) else None)
            rows.append(out_row)
        merged[name] = rows
    return merged


def _bullbear_quotes(symbols):
    """One batched ``/quotes`` call for every symbol.

    Measured 2026-08-19: the tree's 374 came back in a SINGLE call, so asking
    for those plus the benchmark is one request per poll and not one per name.
    """
    from services import _proxy

    if not symbols:
        return {}
    return _proxy.schwab_client.get_quotes(list(symbols)) or {}


def bullbear_view(momentum) -> dict:
    """Merge the nightly cascade with live quotes -> cache:sentiment:bullbear.

    TWO clocks on purpose: ``session_date``/``computed_at`` date the SCORES
    (last night's cascade), ``quoted_at`` dates the day-moves (now).

    ``momentum`` is handed in rather than read here because this module holds no
    bus at all — every cache key and every ``cache_set`` lives in handlers.py.
    ``merge_live``'s shallow copy is safe whatever the caller hands over, cached
    or freshly computed, because nothing here writes below the top level.

    ONLY the quote call degrades: a dead proxy costs the day-move column, and
    ``quoted_at`` None is the tell that the moves are absent rather than flat —
    the nightly tree is still worth publishing. A malformed tree deliberately
    raises instead, because an all-None day-move column would hide it.
    """
    momentum = momentum or {}
    # Not normalized to the three level names here: bullbear_symbols and
    # merge_live each iterate BULLBEAR_LEVELS themselves, so a cold or partial
    # cache already yields all three keys, empty. The one input that WOULD need
    # it is a level held as a generator — bullbear_symbols would exhaust it and
    # merge_live would then see zero rows — which cannot arrive through
    # Bus.cache_get, since JSON yields lists.
    levels = momentum.get("levels") or {}
    symbols = bullbear_symbols(levels)
    quoted_at = None
    try:
        quotes = _bullbear_quotes(symbols)
        quoted_at = _dt.datetime.now().astimezone().isoformat()
    except Exception as exc:  # noqa: BLE001 — best-effort; the tree still ships.
        # One line, not a traceback: at the design's ~30 s RTH cadence
        # (~780 calls/day, throttled off-hours) a sustained proxy outage would
        # otherwise put hundreds of stacks a day into errors.log.
        log.warning("bullbear: live quotes failed, nightly tree only (%r)", exc)
        quotes = {}
    return {
        "session_date": momentum.get("session_date"),
        "computed_at": momentum.get("computed_at"),
        "quoted_at": quoted_at,
        "regime": momentum.get("regime"),
        # The axis every row's day_excess is measured against. Tier 1 reads it
        # as a LIVENESS sentinel, not to label: None here is the only in-band
        # tell that the quote call came back without the benchmark, which the
        # calendar cannot see.
        "benchmark_day_pct": _quoted_day_pct(quotes, MOMENTUM_BENCHMARK),
        "levels": merge_live(levels, quotes),
    }
