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
import logging
import threading
import time
from datetime import datetime, timezone

from services.sentiment_svc import compute, scheduler
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


def _maybe_recompute_trend():
    """Recompute the directional Market Trend if the 15-min gate is due.

    Threads persisted hysteresis/smoothing state through ``compute_intraday_trend``
    and refreshes the cached payloads in ``_TREND``. Defensive — a recompute
    failure logs and leaves the prior cached trend in place (never aborts refresh).
    """
    from services import _proxy
    with _TREND_LOCK:
        now = time.monotonic()
        if not scheduler.trend_due(now, _TREND["last_ts"]):
            return
        try:
            t = compute.compute_intraday_trend(
                _proxy.schwab_client,
                prior_history=_TREND["history"],
                prior_committed=_TREND["committed"],
                prev_smoothed=_TREND["smoothed"])
            t30 = compute.compute_30d_trend()
            _TREND.update(
                last_ts=now,
                history=t.get("state_history", []),
                committed=t.get("state"),
                smoothed=t.get("smoothed_score"),
                trend=t,
                trend_30d=t30)
        except Exception:  # noqa: BLE001 — recompute failure must not abort refresh.
            log.exception("intraday trend recompute failed")

CACHE_COMPOSITE = "cache:sentiment:composite"
CACHE_HISTORY = "cache:sentiment:history"
CACHE_SECTORS = "cache:sentiment:sectors"
CACHE_ROTATION = "cache:sentiment:rotation"

EVENT_COMPOSITE = "events:sentiment:composite"
EVENT_SECTORS = "events:sentiment:sectors"
EVENT_ROTATION = "events:sentiment:rotation"


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
    snaps, spy = compute.load_snapshots()
    live = compute.load_live()

    # Validation gate — fail loudly if the composite shape drifts.
    _composite_gate(live, snaps)

    # 15-min directional Market-Trend recompute (gated + state persisted).
    _maybe_recompute_trend()

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

    bus.cache_set(CACHE_HISTORY, {"snaps": snaps or [], "spy": spy or []})

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
        compute.build_and_write_bridge(snaps, spy, live, sector)
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
