"""Market service handlers — validate + publish the dashboard view."""
import logging

from shared.contracts.market import MarketDashboard, MarketSummary

log = logging.getLogger("market_svc.handlers")

CACHE = "cache:market:dashboard"
EVENT = "events:market:dashboard"
CACHE_SUMMARY = "cache:market:summary"
EVENT_SUMMARY = "events:market:summary"


def publish(bus, payload) -> int:
    """Validate against MarketDashboard and cache+publish. Returns the version."""
    md = MarketDashboard(**payload)
    return bus.cache_set(CACHE, md.model_dump(), event=EVENT, skip_unchanged=True)


def publish_summary(bus, payload) -> int:
    """Validate against MarketSummary and cache+publish. Returns the version."""
    ms = MarketSummary(**payload)
    return bus.cache_set(CACHE_SUMMARY, ms.model_dump(), event=EVENT_SUMMARY, skip_unchanged=True)


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:market`` command. There are none today.

    The ticker toggle's ``enable_summary`` / ``disable_summary`` were retired on
    2026-09-10, when the summary began feeding the Desk as well as the marquee —
    the toggle now only hides the marquee. Consumer groups replay the stream's
    backlog, so one of those can still arrive from an older webgui: it is
    ignored, like any unknown type."""
    log.debug("ignoring cmd:market %s", getattr(command, "type", None))
