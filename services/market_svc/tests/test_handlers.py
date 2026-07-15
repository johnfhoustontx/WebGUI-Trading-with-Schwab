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


# ── summary enable/disable gate ────────────────────────────────────────────
# The webgui's "Show the ticker" toggle is a Tier-1 setting; the Claude verdict
# is generated in Tier 2. The toggle enqueues a command, this service records the
# flag, and the scheduler reads it — so switching the ticker off actually stops
# the API calls instead of just hiding the marquee.


def test_summary_enabled_defaults_true_when_key_absent():
    # No key (fresh Redis / never toggled) must preserve today's behavior.
    assert handlers.summary_enabled(Bus()) is True


def test_set_summary_enabled_roundtrips():
    bus = Bus()
    handlers.set_summary_enabled(bus, False)
    assert handlers.summary_enabled(bus) is False
    handlers.set_summary_enabled(bus, True)
    assert handlers.summary_enabled(bus) is True


def test_summary_enabled_defaults_true_on_unreadable_key():
    class _BadBus:
        def cache_get(self, key):
            raise RuntimeError("redis down")

    # A bus failure must not silently disable the ticker verdict.
    assert handlers.summary_enabled(_BadBus()) is True


def test_handle_command_toggles_summary():
    bus = Bus()
    handlers.handle_command(bus, Command(type="disable_summary"))
    assert handlers.summary_enabled(bus) is False
    handlers.handle_command(bus, Command(type="enable_summary"))
    assert handlers.summary_enabled(bus) is True


def test_handle_command_ignores_unknown_type():
    bus = Bus()
    handlers.handle_command(bus, Command(type="nonsense"))  # must not raise
    assert handlers.summary_enabled(bus) is True
