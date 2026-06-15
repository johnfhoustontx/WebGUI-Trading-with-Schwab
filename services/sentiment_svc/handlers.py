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
* ``cache:sentiment:sectors``   → ``{"sector", "sector_at"}`` (with_sectors only)

Kept synchronous: it calls blocking ``compute`` functions and the scaffold's
consumer loop awaits the result only if it is awaitable.
"""
import logging
from datetime import datetime, timezone

from services.sentiment_svc import compute
from shared.contracts.sentiment import CompositeSnapshot

log = logging.getLogger(__name__)

CACHE_COMPOSITE = "cache:sentiment:composite"
CACHE_HISTORY = "cache:sentiment:history"
CACHE_SECTORS = "cache:sentiment:sectors"

EVENT_COMPOSITE = "events:sentiment:composite"
EVENT_SECTORS = "events:sentiment:sectors"


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

    now_iso = datetime.now(timezone.utc).isoformat()

    version = bus.cache_set(CACHE_COMPOSITE, {
        "live": live,
        "composite_at": now_iso,
        "proxy_up": compute.proxy_up(),
    })
    bus.publish(EVENT_COMPOSITE, {"version": version})

    bus.cache_set(CACHE_HISTORY, {"snaps": snaps or [], "spy": spy or []})

    sector = None
    if with_sectors:
        try:
            sector = compute.load_sector_perf(spy)
            v = bus.cache_set(CACHE_SECTORS, {
                "sector": sector,
                "sector_at": now_iso,
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


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:sentiment`` command. ``refresh`` → full refresh; else no-op."""
    if command.type == "refresh":
        refresh(bus, with_sectors=True)
