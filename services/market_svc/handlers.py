"""Market service handlers — validate + publish the dashboard view."""
import logging

from shared.contracts.market import MarketDashboard

log = logging.getLogger("market_svc.handlers")

CACHE = "cache:market:dashboard"
EVENT = "events:market:dashboard"


def publish(bus, payload) -> int:
    """Validate against MarketDashboard and cache+publish. Returns the version."""
    md = MarketDashboard(**payload)
    return bus.cache_set(CACHE, md.model_dump(), event=EVENT, skip_unchanged=True)
