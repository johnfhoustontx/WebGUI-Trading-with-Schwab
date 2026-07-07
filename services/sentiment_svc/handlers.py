"""Sentiment service refresh handler (Tier-2 → Tier-3 write path).

The service-side analog of ``webgui/pages/sentiment.py:_refresh_cache_sync``.
Instead of mutating an in-process ``_CACHE`` dict, it computes via
``compute`` and writes three cache views into the Redis bus (Tier 3),
publishes change events for the GUI to react to, and dual-writes the legacy
``shared/sentiment_bridge.json`` so ``options-scanner/regime_filter`` keeps
working.

Cache views (split by concern, mirroring the page's single ``_CACHE``):

* ``cache:sentiment:composite`` → ``{"live", "composite_at", "proxy_up"}``
* ``cache:sentiment:history``   → ``{"snaps", "spy"}``
* ``cache:sentiment:sectors``   → ``{"sector", "industries", "sector_at"}``
  (with_sectors only; ``industries`` maps each sector name → its precomputed
  industry sub-rows so the GUI renders them on expand without a proxy call)

Kept synchronous: it calls blocking ``compute`` functions and the scaffold's
consumer loop awaits the result only if it is awaitable.
"""
import datetime as _dt
import logging
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo as _ZI

from services.sentiment_svc import (
    compute, intraday_history_db, market_state_history_db, order_flow_consumer,
    scheduler, sector_pcr_history_db, state_alert)
from shared.contracts.sentiment import CompositeSnapshot

log = logging.getLogger(__name__)

# Persisted directional Market-Trend state. ``last_ts`` is a monotonic timestamp
# gating the 15-min recompute; ``history`` / ``committed`` / ``smoothed`` thread
# the hysteresis + EMA state across reads; ``trend`` / ``trend_30d`` are the latest
# computed payloads reused on gated (non-recompute) refreshes so every composite
# write carries a trend. ``refresh`` has two entry points (the scheduler loop and
# ``handle_command``) that the scaffold runs in a multi-worker executor, so the
# read-modify-write below is serialized by ``_TREND_LOCK`` — without it a manual
# Refresh racing the scheduled one could double-recompute or tear the hysteresis
# thread.
_TREND = {"last_ts": None, "history": [], "committed": None, "smoothed": None,
          "trend": None, "trend_30d": None}
_TREND_LOCK = threading.Lock()

# Cached 30-day backfill. ``compute.load_snapshots`` re-runs the full 35-day
# scoring path (re-fetching ~6 months of VIX/VIX1D/VIX9D/CPCE + sector/SPY
# histories and rescoring 35 days) — ~24 proxy calls — but the 30-day history
# changes at most once per session-day. So it is computed at most ONCE per local
# date here and reused on the intervening 120 s ticks (the LIVE composite +
# intraday recording still refresh every gated tick). ``refresh`` runs from two
# entry points across a multi-worker executor, so the read-modify-write is
# serialized by ``_SNAPSHOTS_LOCK``.
_SNAPSHOTS = {"date": None, "snaps": None, "spy": None}
_SNAPSHOTS_LOCK = threading.Lock()


def _session_date() -> str:
    """Current local (CT) date as an ISO string — the backfill cache key. Split
    out so tests can freeze the session day without touching the wall clock."""
    return _dt.datetime.now(_ZI("America/Chicago")).date().isoformat()


def _load_snapshots_cached():
    """(snaps, spy) for the 30-day history — computed at most once per session-day.

    Returns the cached (snaps, spy) when it was already computed for today's local
    date; otherwise runs the heavy ``compute.load_snapshots`` once and caches it.
    Serialized by ``_SNAPSHOTS_LOCK`` (two refresh entry points, multi-worker
    executor). Defensive: if a recompute raises but a prior day's result is cached,
    the stale-but-valid cached history is reused rather than aborting the refresh."""
    today = _session_date()
    with _SNAPSHOTS_LOCK:
        if _SNAPSHOTS["date"] == today and _SNAPSHOTS["snaps"] is not None:
            return _SNAPSHOTS["snaps"], _SNAPSHOTS["spy"]
        try:
            snaps, spy = compute.load_snapshots()
        except Exception:  # noqa: BLE001 — keep the last good history if any.
            log.exception("backfill load_snapshots failed")
            if _SNAPSHOTS["snaps"] is not None:
                return _SNAPSHOTS["snaps"], _SNAPSHOTS["spy"]
            return [], []
        _SNAPSHOTS.update(date=today, snaps=snaps, spy=spy)
        return snaps, spy


def _maybe_recompute_trend(bus):
    """Recompute the directional Market Trend if the 15-min gate is due.

    Threads persisted hysteresis/smoothing state through ``compute_intraday_trend``
    and refreshes the cached payloads in ``_TREND``. Also feeds the aggression axis:
    the cross-service ``cache:options:flow_skew`` view (25-delta put-skew Δ) read
    from the bus, ``compute.sector_pc_delta()`` (5-day cap-weighted sector P/C Δ),
    and ``cache:sentiment:order_flow`` (streamed SPY aggressor ratio) read from the
    bus. All reads are defensive — a bus/cache failure degrades to None, never
    aborts. Defensive overall — a recompute failure logs and leaves the prior
    cached trend in place (never aborts refresh).
    """
    from services import _proxy
    with _TREND_LOCK:
        now = time.monotonic()
        if not scheduler.trend_due(now, _TREND["last_ts"]):
            return

        # Aggression inputs — cross-service put-skew + sector P/C change. Read
        # defensively so a missing/locked cache never aborts the trend recompute.
        flow_skew = None
        try:
            env = bus.cache_get(CACHE_OPTIONS_FLOW_SKEW)
            if env is not None and isinstance(env.payload, dict):
                flow_skew = env.payload
        except Exception:  # noqa: BLE001 — degrade to None.
            log.debug("flow_skew read failed", exc_info=True)
        try:
            pc_delta = compute.sector_pc_delta()
        except Exception:  # noqa: BLE001 — degrade to None.
            log.debug("sector_pc_delta read failed", exc_info=True)
            pc_delta = None
        # Streamed aggressor order-flow (cache:sentiment:order_flow), published by
        # the service's own SSE consumer. Defensive read → None drops out.
        order_flow = None
        try:
            env = bus.cache_get(order_flow_consumer.CACHE_ORDER_FLOW)
            if env is not None and isinstance(env.payload, dict):
                order_flow = env.payload
        except Exception:  # noqa: BLE001 — degrade to None.
            log.debug("order_flow read failed", exc_info=True)

        try:
            t = compute.compute_intraday_trend(
                _proxy.schwab_client,
                prior_history=_TREND["history"],
                prior_committed=_TREND["committed"],
                prev_smoothed=_TREND["smoothed"],
                flow_skew=flow_skew,
                sector_pc_delta=pc_delta,
                order_flow=order_flow)
            t30 = compute.compute_30d_trend()
            prev_committed = _TREND["committed"]
            _TREND.update(
                last_ts=now,
                history=t.get("state_history", []),
                committed=t.get("state"),
                smoothed=t.get("smoothed_score"),
                trend=t,
                trend_30d=t30)
            new_committed = _TREND["committed"]
            # Record the freshly-committed market-state for later validation.
            # Nested guard so a recorder failure can't abort the recompute.
            try:
                _record_market_state(_TREND["trend"])
            except Exception:  # noqa: BLE001
                log.exception("market state record failed (recompute)")
            # Push a phone alert when the committed state FLIPS. The gate in
            # ``send_state_transition`` handles enabled/market-hours/valid-vocab
            # filtering (incl. the cold-start old→new-vocab first cycle) — the
            # handler just detects "committed changed" and delegates. Best-effort:
            # a notify failure must NOT abort the recompute.
            if new_committed != prev_committed:
                try:
                    state_alert.send_state_transition(
                        prev_committed, new_committed, _TREND["trend"])
                except Exception:  # noqa: BLE001
                    log.exception("state transition notify failed")
        except Exception:  # noqa: BLE001 — recompute failure must not abort refresh.
            log.exception("intraday trend recompute failed")


# Cross-service view (published by options_svc) feeding the aggression axis.
CACHE_OPTIONS_FLOW_SKEW = "cache:options:flow_skew"

CACHE_COMPOSITE = "cache:sentiment:composite"
CACHE_HISTORY = "cache:sentiment:history"
CACHE_SECTORS = "cache:sentiment:sectors"
CACHE_ROTATION = "cache:sentiment:rotation"
CACHE_INTRADAY = "cache:sentiment:intraday_history"

EVENT_COMPOSITE = "events:sentiment:composite"
EVENT_SECTORS = "events:sentiment:sectors"
EVENT_ROTATION = "events:sentiment:rotation"
EVENT_INTRADAY = "events:sentiment:intraday_history"


# --- 2-min intraday sentiment+trend series ------------------------------------
# A lazily-opened SQLite connection (the on-disk store from Task 1). ``refresh``
# records one RTH-only point per cycle, prunes to 5 trading days, and publishes
# ``cache:sentiment:intraday_history`` for the page's two intraday graphs. The
# connection is shared across executor threads (``refresh`` is dispatched from
# both the scheduler loop and the command consumer), so every access — including
# the lazy init — is serialized by ``_INTRADAY_LOCK``.
_intraday_conn = None
_INTRADAY_LOCK = threading.Lock()


def _get_intraday_conn():
    global _intraday_conn
    if _intraday_conn is None:
        _intraday_conn = intraday_history_db.connect()
    return _intraday_conn


# --- daily cap-weighted sector Put/Call ratio (options-flow direction) --------
# A second lazily-opened SQLite store: one row per LOCAL date (today's row is
# REPLACE-updated each RTH refresh, so the latest read wins), used for the
# 5-trading-day P/C delta the market-state classifier reads. Shares the same
# ``_INTRADAY_LOCK`` as the intraday store — both are check_same_thread=False
# connections touched from the multi-worker executor, and one lock serializing
# both is simplest and contention-free (the recorders run back-to-back in the
# same refresh, off-loop).
_sector_pcr_conn = None


def _get_sector_pcr_conn():
    global _sector_pcr_conn
    if _sector_pcr_conn is None:
        _sector_pcr_conn = sector_pcr_history_db.connect()
    return _sector_pcr_conn


def _record_sector_pcr(live):
    """Record today's cap-weighted sector P/C ratio (RTH-only), prune the window.
    Defensive — never aborts the core refresh. Skips when ``sector_pcr`` is
    None."""
    try:
        if not _is_rth_now():
            return
        pcr = (live or {}).get("sector_pcr")
        if pcr is None:
            return
        with _INTRADAY_LOCK:
            conn = _get_sector_pcr_conn()
            date_iso = _dt.datetime.now(_ZI("America/Chicago")).date().isoformat()
            sector_pcr_history_db.record(conn, date_iso, pcr)
            sector_pcr_history_db.prune(conn, keep=10)
    except Exception:  # noqa: BLE001
        log.exception("sector pcr record failed")


# --- daily committed market-state (validation record) -------------------------
# A third lazily-opened SQLite store: one row per LOCAL date (today's row is
# REPLACE-updated each RTH recompute), recording the five-state classifier's
# committed state + direction/aggression + a components JSON, so a later task can
# backtest whether the states stratify forward returns. Shares ``_INTRADAY_LOCK``
# with the other two on-disk stores (all check_same_thread=False, touched off-loop
# from the multi-worker executor).
_market_state_conn = None


def _get_market_state_conn():
    global _market_state_conn
    if _market_state_conn is None:
        _market_state_conn = market_state_history_db.connect()
    return _market_state_conn


def _record_market_state(trend):
    """Record today's committed market-state (RTH-only), prune the window.
    Defensive — never aborts the trend recompute. Skips when the committed
    ``state`` is falsy."""
    try:
        if not _is_rth_now():
            return
        t = trend or {}
        committed_state = t.get("state")
        if not committed_state:
            return
        components = {"evidence": t.get("evidence"),
                      "sub_scores": t.get("sub_scores"),
                      "aggression_confidence": t.get("aggression_confidence")}
        with _INTRADAY_LOCK:
            conn = _get_market_state_conn()
            date_iso = _dt.datetime.now(_ZI("America/Chicago")).date().isoformat()
            market_state_history_db.record(
                conn, date_iso, committed_state,
                t.get("smoothed_score"), t.get("aggression"), components)
            market_state_history_db.prune(conn, keep=90)
    except Exception:  # noqa: BLE001
        log.exception("market state record failed")


def _is_rth_now() -> bool:
    """Mon-Fri 08:30-15:00 CT (mirrors the page's is_rth)."""
    now = _dt.datetime.now(_ZI("America/Chicago"))
    if now.weekday() >= 5:
        return False
    return (8, 30) <= (now.hour, now.minute) < (15, 0)


def _intraday_values(live, trend):
    """(sentiment 0-10, trend 0-100) from the live snapshot + trend dict, or
    None when there is no live composite to record. Records the SMOOTHED trend
    score (``smoothed_score``, the EMA value the gauge displays), falling back to
    the raw ``score`` so the Daily Market Trend graph's latest point matches the
    gauge needle above it."""
    if not live:
        return None
    try:
        t = trend or {}
        sentiment = float((live.get("composite") or {})["total_score"])
        tscore = float(t.get("smoothed_score", t.get("score")))
    except (KeyError, TypeError, ValueError):
        return None
    return sentiment, tscore


def _record_intraday(bus, live, trend):
    """Record one 2-min point (RTH-only), prune to 5 trading days, publish the
    view. Defensive — never aborts the core refresh."""
    try:
        if not _is_rth_now():
            return
        vals = _intraday_values(live, trend)
        if vals is None:
            return
        # Serialize the lazy init + DB ops + publish: the shared connection is
        # check_same_thread=False, so concurrent refresh threads must not touch
        # it (or the lazy init) at once.
        with _INTRADAY_LOCK:
            conn = _get_intraday_conn()
            ts = int(_dt.datetime.now().timestamp())
            intraday_history_db.insert_point(conn, ts, vals[0], vals[1])
            intraday_history_db.prune(conn, n_days=5)
            rows = intraday_history_db.load_recent(conn, n_days=5)
            points = [{"ts": r[0], "sentiment": r[1], "trend": r[2]} for r in rows]
            version = bus.cache_set(CACHE_INTRADAY, {"points": points})
            bus.publish(EVENT_INTRADAY, {"version": version})
    except Exception:  # noqa: BLE001
        log.exception("intraday history record failed")


def _sector_industry_etfs(sector_data, sector_name):
    """Industry ETF symbols under one sector — mirrors the GUI's
    ``webgui/pages/sentiment.py:sector_industry_etfs`` (kind=='industry',
    matching sector, valid etf: not 'n/a', <=6 chars)."""
    out = []
    for r in sector_data or []:
        if r.get("kind") != "industry" or r.get("sector") != sector_name:
            continue
        etf = r.get("etf")
        if etf and etf != "n/a" and len(str(etf)) <= 6:
            out.append(etf)
    return out


def _load_all_industries(sector, spy):
    """Precompute every sector's industry sub-rows so the GUI can render them
    on expand without ever calling the proxy. Enumerates sector names + their
    industry ETFs from ``sector['sector_data']`` (the same shape the GUI's
    expand handler reads), then ``compute.load_industries`` once per sector.

    Returns ``{sector_name: rows}``. Resilience is **per-sector**: a failure
    computing one sector's industries logs and skips THAT sector only (its
    entry is omitted), while the other sectors still populate. An outer guard
    protects the enumeration of sector names itself, so a malformed ``sector``
    dict yields ``{}`` rather than raising.
    """
    industries = {}
    try:
        sector_data = (sector or {}).get("sector_data") or []
        names = []
        for r in sector_data:
            if r.get("kind") != "sector":
                continue
            name = r.get("sector") or r.get("label")
            if name:
                names.append(name)
    except Exception:  # noqa: BLE001 — malformed sector dict -> empty view.
        log.exception("industry precompute enumeration failed")
        return {}

    for name in names:
        try:
            etfs = _sector_industry_etfs(sector_data, name)
            industries[name] = compute.load_industries(etfs, spy)
        except Exception:  # noqa: BLE001 — one sector's failure must not zero the rest.
            log.exception("industry precompute failed for sector %s", name)
            continue
    return industries


def _composite_gate(live, snaps):
    """Validate the composite shape by building a typed CompositeSnapshot.

    Raises if a live (or latest backfill) snapshot is present but its
    composite total/bias/components fields are missing or malformed — fails
    loudly so any shape drift in ``compute_live`` is caught here rather than
    silently corrupting the cache. Skips (returns None) when there is no
    snapshot to validate at all.
    """
    snap = live or (snaps[-1] if snaps else None)
    if not snap:
        return None
    comp = snap.get("composite") or {}
    total = float(comp["total_score"])  # KeyError/ValueError -> drift caught
    bias = str(comp.get("bias", ""))
    components = dict(snap.get("component_scores") or {})
    return CompositeSnapshot(total=total, bias=bias, components=components)


def refresh(bus, with_sectors: bool = False) -> None:
    """Compute sentiment, write the cache views, publish events, dual-write bridge."""
    # 30-day backfill is cached per session-day (see _load_snapshots_cached) —
    # only the LIVE composite (+ intraday recording) recompute every tick.
    snaps, spy = _load_snapshots_cached()
    live = compute.load_live()

    # Validation gate — fail loudly if the composite shape drifts.
    _composite_gate(live, snaps)

    # 15-min directional Market-Trend recompute (gated + state persisted).
    _maybe_recompute_trend(bus)

    now_iso = datetime.now(timezone.utc).isoformat()

    version = bus.cache_set(CACHE_COMPOSITE, {
        "live": live,
        "composite_at": now_iso,
        "proxy_up": compute.proxy_up(),
        "derived": compute.derive_composite_extras(
            live, snaps, spy,
            trend=_TREND["trend"], trend_30d=_TREND["trend_30d"]),
    })
    bus.publish(EVENT_COMPOSITE, {"version": version})

    # skip_unchanged: the 30-day history is cached per session-day, so most ticks
    # write a byte-identical payload — don't bump the version (no GUI repaint).
    bus.cache_set(CACHE_HISTORY, {"snaps": snaps or [], "spy": spy or []},
                  skip_unchanged=True)

    _record_intraday(bus, live, _TREND["trend"])
    _record_sector_pcr(live)

    sector = None
    if with_sectors:
        try:
            sector = compute.load_sector_perf(spy)
            industries = _load_all_industries(sector, spy)
            v = bus.cache_set(CACHE_SECTORS, {
                "sector": sector,
                "industries": industries,
                "sector_at": now_iso,
                "summary": compute.derive_sector_summary(sector),
            })
            bus.publish(EVENT_SECTORS, {"version": v})
        except Exception:  # noqa: BLE001 — sector failure must not abort refresh.
            log.exception("sector perf refresh failed")
            sector = None

    # Always dual-write the legacy bridge (defensive — never abort on failure).
    try:
        compute.build_and_write_bridge(snaps, spy, live, sector, trend=_TREND["trend"])
    except Exception:  # noqa: BLE001
        log.exception("bridge dual-write failed")


def refresh_rotation(bus) -> None:
    """Compute the sector-rotation assessment + S&P weights and cache them.

    Writes ``cache:sentiment:rotation`` ->
    ``{"assessment", "weights", "risk_threshold", "error"}`` and publishes
    ``events:sentiment:rotation``. Wrapped defensively: a compute failure caches
    an error payload (so the GUI shows a message) rather than crashing."""
    try:
        a, err = compute.rotation_assessment()
    except Exception as exc:  # noqa: BLE001 — surface as a cached error, don't crash.
        log.exception("rotation assessment failed")
        a, err = None, f"Rotation compute failed: {exc}"
    try:
        weights = compute.rotation_weights() or {}
    except Exception:  # noqa: BLE001 — weights are non-essential; degrade to {}.
        log.exception("rotation weights failed")
        weights = {}
    try:
        risk_threshold = compute.rotation_risk_threshold()
    except Exception:  # noqa: BLE001
        risk_threshold = None
    version = bus.cache_set(CACHE_ROTATION, {
        "assessment": a,
        "weights": weights,
        "risk_threshold": risk_threshold,
        "error": err,
    })
    bus.publish(EVENT_ROTATION, {"version": version})


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:sentiment`` command. ``refresh`` → full refresh,
    ``refresh_rotation`` → rotation-only refresh; else no-op."""
    if command.type == "refresh":
        refresh(bus, with_sectors=True)
    elif command.type == "refresh_rotation":
        refresh_rotation(bus)
