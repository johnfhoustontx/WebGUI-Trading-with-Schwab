from shared.bus import Bus
from shared.contracts.envelope import Command
from services.market_svc import handlers


def test_publish_validates_and_caches():
    bus = Bus()  # fakeredis under pytest
    payload = {"categories": [{"category": "Volatility",
                               "tiles": [{"display": "VIX", "color_state": "flat"}]}],
               "proxy_up": True, "errors": []}
    version = handlers.publish(bus, payload)
    assert version >= 1
    env = bus.cache_get(handlers.CACHE)
    assert env.payload["categories"][0]["category"] == "Volatility"
    assert env.payload["proxy_up"] is True


def test_publish_summary():
    from shared.bus import Bus
    from services.market_svc import handlers
    bus = Bus()
    v = handlers.publish_summary(bus, {"narrative": "Cautious tape."})
    assert v >= 1
    env = bus.cache_get(handlers.CACHE_SUMMARY)
    assert env.payload["narrative"] == "Cautious tape."


def test_retired_toggle_commands_are_ignored_not_errors():
    """enable_summary / disable_summary were retired 2026-09-10. A fresh consumer
    group replays the stream backlog, so an old command WILL arrive - it must be
    a no-op, never an exception and never a gate."""
    bus = Bus()
    for t in ("disable_summary", "enable_summary", "nonsense"):
        handlers.handle_command(bus, Command(type=t))
    assert bus.cache_get("cache:market:summary_enabled") is None


def test_the_toggle_gate_is_gone():
    assert not hasattr(handlers, "summary_enabled")
    assert not hasattr(handlers, "set_summary_enabled")
