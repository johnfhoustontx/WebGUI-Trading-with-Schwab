from shared.bus import Bus
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
    v = handlers.publish_summary(bus, {"narrative": "Cautious tape.", "generated_at": None})
    assert v >= 1
    env = bus.cache_get(handlers.CACHE_SUMMARY)
    assert env.payload["narrative"] == "Cautious tape."
