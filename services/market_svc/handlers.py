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
