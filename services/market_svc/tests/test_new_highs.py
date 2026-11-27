"""New highs / new lows on the Macro Board: the screener requests and the view."""
import pytest

from services.market_svc import new_highs as N
from shared.bus.client import reset_fake_bus


def _cfg(**over):
    cfg = {"enabled": True, "url": "https://scanner.tradingview.com/america/scan",
           "exchanges": ["NYSE", "NASDAQ", "AMEX"], "min_price": 5.0,
           "rth_refresh_sec": 60, "after_close_min": 10, "timeout_sec": 10,
           "retry_sec": 120, "public_show": True}
    cfg.update(over)
    return cfg


_UNIVERSE = [
    {"left": "type", "operation": "equal", "right": "stock"},
    {"left": "is_primary", "operation": "equal", "right": True},
    {"left": "exchange", "operation": "in_range", "right": ["NYSE", "NASDAQ", "AMEX"]},
]

# The 2026-11-27 counts, straight from the screener.
_COUNTS = {"universe": 4984,
           "all.52w.highs": 121, "all.52w.lows": 85,
           "all.13w.highs": 205, "all.13w.lows": 120,
           "all.4w.highs": 347, "all.4w.lows": 158,
           "over5.52w.highs": 120, "over5.52w.lows": 12,
           "over5.13w.highs": 203, "over5.13w.lows": 27,
           "over5.4w.highs": 314, "over5.4w.lows": 55}


@pytest.fixture(autouse=True)
def _empty_bus():
    reset_fake_bus()
    yield


def test_windows_are_the_three_the_operator_named():
    assert N.WINDOWS == (
        ("52w", "52 wk", "price_52_week_high", "price_52_week_low"),
        ("13w", "13 wk", "High.3M", "Low.3M"),
        ("4w", "4 wk", "High.1M", "Low.1M"),
    )


def test_requests_for_order_and_count():
    keys = [k for k, _ in N.requests_for(_cfg())]
    assert len(keys) == 13
    assert keys[0] == "universe"
    assert keys[1] == "all.52w.highs"
    assert keys[2] == "all.52w.lows"
    assert keys[7] == "over5.52w.highs"
    assert keys[-1] == "over5.4w.lows"
    assert set(keys) == set(_COUNTS)


def test_requests_for_without_a_price_row():
    keys = [k for k, _ in N.requests_for(_cfg(min_price=0))]
    assert len(keys) == 7
    assert not any(k.startswith("over5") for k in keys)


def test_universe_body_has_only_the_universe_filter():
    body = dict(N.requests_for(_cfg()))["universe"]
    assert body["filter"] == _UNIVERSE
    assert body["range"] == [0, 1]
    assert body["columns"] == ["name"]
    assert body["markets"] == ["america"]
    assert body["symbols"] == {"query": {"types": []}}


def test_window_bodies():
    bodies = dict(N.requests_for(_cfg()))
    low13 = bodies["all.13w.lows"]["filter"]
    assert low13[:3] == _UNIVERSE
    assert low13[-1] == {"left": "low", "operation": "eless", "right": "Low.3M"}
    assert not any(f["left"] == "close" for f in low13)
    high52 = bodies["over5.52w.highs"]["filter"]
    assert {"left": "close", "operation": "egreater", "right": 5.0} in high52
    assert high52[-1] == {"left": "high", "operation": "egreater",
                          "right": "price_52_week_high"}
    assert bodies["over5.4w.highs"]["filter"][-1]["right"] == "High.1M"


def test_exchanges_follow_the_config():
    body = dict(N.requests_for(_cfg(exchanges=["NYSE"])))["universe"]
    assert body["filter"][2]["right"] == ["NYSE"]


def _no_ts(obj):
    if isinstance(obj, dict):
        return "ts" not in obj and all(_no_ts(v) for v in obj.values())
    return True


def test_build_full():
    view = N.build(dict(_COUNTS), _cfg(), "2026-11-27")
    assert view["rows"]["all"]["52w"] == {"highs": 121, "lows": 85}
    assert view["rows"]["over5"]["4w"] == {"highs": 314, "lows": 55}
    assert view["universe"] == 4984
    assert view["session"] == "2026-11-27"
    assert view["source"] == "TradingView"
    assert view["enabled"] is True
    assert view["min_price"] == 5.0
    assert view["exchanges"] == ["NYSE", "NASDAQ", "AMEX"]
    assert view["public"] is True
    # skip_unchanged view: no timestamp of its own anywhere in it.
    assert _no_ts(view)


def test_build_carries_the_public_switch():
    assert N.build(dict(_COUNTS), _cfg(public_show=False), "2026-11-27")["public"] is False


def test_build_without_a_price_row():
    counts = {k: v for k, v in _COUNTS.items() if not k.startswith("over5")}
    view = N.build(counts, _cfg(min_price=0), "2026-11-27")
    assert set(view["rows"]) == {"all"}


@pytest.mark.parametrize("missing", ["universe", "all.4w.lows", "over5.52w.highs"])
def test_build_any_none_builds_nothing(missing):
    counts = dict(_COUNTS)
    counts[missing] = None
    assert N.build(counts, _cfg(), "2026-11-27") is None


def test_build_a_missing_key_builds_nothing():
    counts = dict(_COUNTS)
    del counts["all.13w.highs"]
    assert N.build(counts, _cfg(), "2026-11-27") is None


def test_publish_validates_and_caches():
    from shared.bus import Bus
    bus = Bus()
    N.publish(bus, N.build(dict(_COUNTS), _cfg(), "2026-11-27"))
    got = bus.cache_get("cache:market:new_highs").payload
    assert got["rows"]["all"]["13w"]["highs"] == 205
