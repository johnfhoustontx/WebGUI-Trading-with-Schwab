"""Market service handlers — validate + publish the dashboard view."""
import logging

from shared.contracts.market import MarketDashboard, MarketRead, MarketSummary, MarketVolume

log = logging.getLogger("market_svc.handlers")

CACHE = "cache:market:dashboard"
EVENT = "events:market:dashboard"
CACHE_SUMMARY = "cache:market:summary"
EVENT_SUMMARY = "events:market:summary"
CACHE_READ = "cache:market:read"
EVENT_READ = "events:market:read"
CACHE_VOLUME = "cache:market:volume"
EVENT_VOLUME = "events:market:volume"


def publish(bus, payload) -> int:
    """Validate against MarketDashboard and cache+publish. Returns the version."""
    md = MarketDashboard(**payload)
    return bus.cache_set(CACHE, md.model_dump(), event=EVENT, skip_unchanged=True)


def publish_summary(bus, payload) -> int:
    """Validate against MarketSummary and cache+publish. Returns the version."""
    ms = MarketSummary(**payload)
    return bus.cache_set(CACHE_SUMMARY, ms.model_dump(), event=EVENT_SUMMARY, skip_unchanged=True)


def publish_read(bus, payload) -> int:
    """Validate against MarketRead and cache+publish. Returns the version."""
    mr = MarketRead(**payload)
    # No skip_unchanged: the payload carries its own ``ts``, and it is written
    # once a slot (or once when a switch changes), never on a clock.
    return bus.cache_set(CACHE_READ, mr.model_dump(), event=EVENT_READ)


def publish_volume(bus, payload) -> int:
    """Validate against MarketVolume and cache+publish. Returns the version."""
    mv = MarketVolume(**payload)
    # No skip_unchanged, for the reason above: it carries its own ``ts``.
    return bus.cache_set(CACHE_VOLUME, mv.model_dump(), event=EVENT_VOLUME)


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:market`` command. There are none today.

    The ticker toggle's ``enable_summary`` / ``disable_summary`` were retired on
    2026-09-10 (the summary is no longer written by a Claude call at all since
    2026-09-16). Consumer groups replay the stream's
    backlog, so one of those can still arrive from an older webgui: it is
    ignored, like any unknown type."""
    log.debug("ignoring cmd:market %s", getattr(command, "type", None))
