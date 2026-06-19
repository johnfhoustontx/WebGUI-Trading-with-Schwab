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
import sys

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

# Sentinel so an OMITTED ``compute_30d_trend`` arg fetches internally, while an
# explicit ``None`` (caller has no data) stays neutral rather than re-fetching.
_FETCH = object()


def _neutral_trend():
    """Neutral directional-trend dict (score 50, conf 0, ``range`` state).

    Returned when there is no trend computed yet, or as the catastrophic-failure
    fallback of ``compute_intraday_trend`` — so the GUI always sees a valid,
    fully-shaped trend payload."""
    return {
        "score": 50.0,
        "smoothed_score": 50.0,
        "state": "range",
        "raw_state": "range",
        "label": trend_regime.STATE_LABELS["range"],
        "description": trend_regime.STATE_DESCRIPTIONS["range"],
        "confidence": 0.0,
        "sub_scores": {"price": 50.0, "breadth": 50.0, "sector": 50.0, "vix": 50.0},
        "sub_confidence": {"price": 0.0, "breadth": 0.0, "sector": 0.0, "vix": 0.0},
        "state_history": [],
    }


def _mean(seq):
    seq = [v for v in seq if v is not None]
    return (sum(seq) / len(seq)) if seq else None


def compute_intraday_trend(schwab, sector_data=None, prior_history=None,
                           prior_committed=None, prev_smoothed=None) -> dict:
    """Live 0-100 *directional* Market Trend from intraday proxy data.

    Blends four sub-scores (price MTF/VWAP/MACD/RSI, breadth, sector
    participation, VIX context) via ``intraday_trend.blend_trend``, EMA-smooths
    the needle, and runs the 2-day hysteresis state machine (reusing
    ``trend_regime.commit_state``) so the published state is sticky.

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
                ref = frames.get("15min") or next(iter(frames.values()))
                price_now = float(ref["close"].iloc[-1])
                align = technical.calculate_ema_alignment(frames, price_now)
                align_pct = float(align.get("alignment_percentage", 0.0))
                df15 = frames.get("15min") or ref
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

        # 6) SMOOTH + hysteresis state.
        smoothed = intraday_trend.ema_smooth(prev_smoothed, raw_score, span=3)
        raw_state = intraday_trend.score_to_state(smoothed)
        committed, hist = trend_regime.commit_state(
            raw_state, prior_history or [], prior_committed)

        return {
            "score": raw_score,
            "smoothed_score": smoothed,
            "state": committed,
            "raw_state": raw_state,
            "label": trend_regime.STATE_LABELS[committed],
            "description": trend_regime.STATE_DESCRIPTIONS[committed],
            "confidence": agg,
            "sub_scores": {"price": price.score, "breadth": breadth.score,
                           "sector": sector.score, "vix": vix_sub.score},
            "sub_confidence": {"price": price.confidence,
                               "breadth": breadth.confidence,
                               "sector": sector.confidence,
                               "vix": vix_sub.confidence},
            "state_history": hist,
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


def compute_30d_trend(spy_daily_df=_FETCH, sector_month_pcts=_FETCH) -> dict:
    """~30-day *structural* directional trend (price structure + sector breadth).

    The daily-horizon analog of ``compute_intraday_trend`` for the GUI's "30-Day
    Avg" Market-Trend gauge: no intraday VWAP, no breadth/VIX, no smoothing or
    hysteresis. When an argument is OMITTED it is fetched internally (so the
    function is self-contained); passing an explicit ``None`` / ``{}`` means the
    caller has no data and the corresponding sub-score degrades to neutral.
    Defensive: a catastrophic failure returns a neutral dict."""
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
        return {
            "score": score,
            "state": state,
            "label": trend_regime.STATE_LABELS[state],
            "description": trend_regime.STATE_DESCRIPTIONS[state],
            "confidence": agg,
            "sub_scores": {"price": price.score, "sector": sector.score},
        }
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
        from services import _proxy
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


def build_and_write_bridge(snaps, spy, live, sector):
    """Build the bridge payload from cache/state data and write it. Defensive."""
    try:
        import bridge
        from datetime import datetime, timezone
        latest = live or (snaps[-1] if snaps else None)
        if not latest:
            return
        prior = composite_series(snaps or [])[1]
        trend = build_trend_dict(spy)
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
