"""Market service handlers — validate + publish the dashboard view."""
import logging

from shared.contracts.market import MarketDashboard, MarketSummary

log = logging.getLogger("market_svc.handlers")

CACHE = "cache:market:dashboard"
EVENT = "events:market:dashboard"
CACHE_SUMMARY = "cache:market:summary"
EVENT_SUMMARY = "events:market:summary"
# The webgui's "Show the ticker" toggle, mirrored into Redis. The marquee is a
# Tier-1 render decision, but the narrative it shows costs a Claude call per
# ~20 min in Tier 2 — so the toggle has to reach this service or switching the
# ticker off would only hide the marquee while the API calls continued.
CACHE_SUMMARY_ENABLED = "cache:market:summary_enabled"


def publish(bus, payload) -> int:
    """Validate against MarketDashboard and cache+publish. Returns the version."""
    md = MarketDashboard(**payload)
    return bus.cache_set(CACHE, md.model_dump(), event=EVENT, skip_unchanged=True)


def publish_summary(bus, payload) -> int:
    """Validate against MarketSummary and cache+publish. Returns the version."""
    ms = MarketSummary(**payload)
    return bus.cache_set(CACHE_SUMMARY, ms.model_dump(), event=EVENT_SUMMARY, skip_unchanged=True)


def set_summary_enabled(bus, enabled: bool) -> int:
    """Record whether the periodic Claude verdict should run. Returns the version."""
    return bus.cache_set(CACHE_SUMMARY_ENABLED, {"enabled": bool(enabled)},
                         skip_unchanged=True)


def summary_enabled(bus) -> bool:
    """Whether the scheduler should generate the Claude verdict this cycle.

    Defaults to True when the key is absent (fresh Redis, or a stack that has
    never touched the toggle) and on ANY read failure — the flag can only ever
    turn the verdict OFF explicitly, so a Redis hiccup degrades to today's
    behavior rather than silently killing the ticker narrative.
    """
    try:
        env = bus.cache_get(CACHE_SUMMARY_ENABLED)
        if env is None:
            return True
        return bool(env.payload.get("enabled", True))
    except Exception:  # noqa: BLE001 — an unreadable flag must not disable the verdict.
        log.exception("summary_enabled read failed; defaulting to enabled")
        return True


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:market`` command; unknown types are a no-op.

    ``enable_summary`` / ``disable_summary`` mirror the webgui's ticker toggle.
    """
    if command.type == "enable_summary":
        set_summary_enabled(bus, True)
    elif command.type == "disable_summary":
        set_summary_enabled(bus, False)
