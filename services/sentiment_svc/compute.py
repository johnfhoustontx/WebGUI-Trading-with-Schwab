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

from repo_paths import SENTIMENT

if str(SENTIMENT) not in sys.path:
    sys.path.insert(0, str(SENTIMENT))

from scoring import WEIGHTS  # noqa: E402,F401
from scoring import composite as scoring_composite  # noqa: E402,F401
from scoring import trend_regime as trend_regime  # noqa: E402
from scoring import sector_perf as scoring_sector  # noqa: E402,F401
from scoring import rotation as scoring_rotation    # noqa: E402
import live_composite  # noqa: E402,F401  (eager: pins module; never lazy)
from live_composite import (  # noqa: E402
    signal_band, compute_live, build_bridge_payload)  # noqa: F401


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


def load_industries(etfs, spy_closes):
    """Off-thread: quotes + week/month trends + P/C + RRG for industry ETFs."""
    from services import _proxy
    from datetime import date, timedelta
    try:
        quotes = _proxy.schwab_client.get_quotes(list(etfs)) or {}
    except Exception:
        quotes = {}
    trends, closes = {}, {}
    for etf in etfs:
        try:
            df = _proxy.schwab_client.get_daily_history(etf, months=3)
        except Exception:
            df = None
        if df is None:
            continue
        cl = [float(c) for c in df["close"].tolist()]
        closes[etf] = cl
        d3, wk, mo = week_month_from_closes(cl)
        trends[etf] = {"day3_pct": d3, "week_pct": wk, "month_pct": mo}
    pcr = {}
    today_iso = date.today().isoformat()
    to_iso = (date.today() + timedelta(days=30)).isoformat()
    for etf in etfs:
        try:
            chain = _proxy.schwab_client._request("/chains", params={
                "symbol": etf, "contractType": "ALL", "range": "NTM",
                "strikeCount": 50, "fromDate": today_iso, "toDate": to_iso})
        except Exception:
            chain = None
        v = pcr_from_chain(chain)
        if v is not None:
            pcr[etf] = v
    quads = scoring_rotation.compute_rrg_quadrants(closes, spy_closes or [],
                                                   rs_window=50, mom_window=20)
    return {"quotes": quotes, "trends": trends, "pcr": pcr, "quadrants": quads}


def load_sector_perf(spy_closes):
    """Off-thread: fetch sector quotes + history + P/C, compute rotation/RRG.
    Returns a dict the page renders. spy_closes reused from the composite load."""
    from services import _proxy
    import sectors_ref
    from datetime import date, timedelta

    sd = sectors_ref.load_sectors_data()
    etfs = [r["etf"] for r in sd if r.get("kind") == "sector" and r.get("etf")]

    try:
        quotes = _proxy.schwab_client.get_quotes(etfs) or {}
    except Exception:
        quotes = {}

    trends, closes = {}, {}
    for etf in etfs:
        try:
            df = _proxy.schwab_client.get_daily_history(etf, months=3)
        except Exception:
            df = None
        if df is None:
            continue
        cl = [float(c) for c in df["close"].tolist()]
        closes[etf] = cl
        d3, wk, mo = week_month_from_closes(cl)
        trends[etf] = {"day3_pct": d3, "week_pct": wk, "month_pct": mo}

    pcr = {}
    today_iso = date.today().isoformat()
    to_iso = (date.today() + timedelta(days=30)).isoformat()
    for etf in etfs:
        try:
            chain = _proxy.schwab_client._request("/chains", params={
                "symbol": etf, "contractType": "ALL", "range": "NTM",
                "strikeCount": 50, "fromDate": today_iso, "toDate": to_iso})
        except Exception:
            chain = None
        v = pcr_from_chain(chain)
        if v is not None:
            pcr[etf] = v

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


def build_and_write_bridge(snaps, spy, live, sector):
    """Build the bridge payload from cache/state data and write it. Defensive."""
    try:
        import bridge
        from datetime import datetime, timezone
        latest = live or (snaps[-1] if snaps else None)
        if not latest:
            return
        prior = composite_series(snaps or [])[1]
        trend = None
        if spy:
            tr, committed, _d = commit_trend_regime(spy)
            trend = {"state": committed, "label": trend_regime.STATE_LABELS[committed],
                     "description": trend_regime.STATE_DESCRIPTIONS[committed],
                     "raw_state": tr.state, "spy_close": round(tr.spy_close, 4),
                     "sma_50": round(tr.sma_50, 4), "sma_200": round(tr.sma_200, 4),
                     "sma_200_slope_pct": round(tr.sma_200_slope_pct, 4),
                     "drawdown_pct": round(tr.drawdown_pct, 4), "confidence": round(tr.confidence, 3)}
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
