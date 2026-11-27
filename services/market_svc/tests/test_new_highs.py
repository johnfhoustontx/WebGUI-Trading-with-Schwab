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


# ── fetching one count ───────────────────────────────────────────────────────
import datetime as dt
from zoneinfo import ZoneInfo

import requests

_CT = ZoneInfo("America/Chicago")


class _Answer:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not JSON")
        return self._body


def _answering(answer):
    def fake(url, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer
    return fake


def test_fetch_count_ok(monkeypatch):
    seen = {}

    def fake(url, **kwargs):
        seen.update(url=url, **kwargs)
        return _Answer(body={"totalCount": 121, "data": []})
    monkeypatch.setattr(N, "_post", fake)
    body = dict(N.requests_for(_cfg()))["all.52w.highs"]
    assert N.fetch_count(body, _cfg()) == 121
    assert seen["url"] == "https://scanner.tradingview.com/america/scan"
    assert seen["json"] == body
    assert seen["timeout"] == 10
    assert seen["headers"]["User-Agent"].startswith("Mozilla/5.0")


@pytest.mark.parametrize("answer", [
    _Answer(status=500, body={"totalCount": 3}),
    _Answer(bad_json=True),
    _Answer(body={"error": "x"}),
    _Answer(body={"totalCount": "121"}),
    _Answer(body={"totalCount": -1}),
    _Answer(body={"totalCount": True}),
    _Answer(body=[1, 2]),
    requests.ConnectionError("down"),
    requests.Timeout("slow"),
])
def test_fetch_count_failures(monkeypatch, answer):
    monkeypatch.setattr(N, "_post", _answering(answer))
    assert N.fetch_count({"filter": []}, _cfg()) is None


# ── when a refresh is due ────────────────────────────────────────────────────
def _at(y, m, d, hh, mm, ss=0):
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=_CT)


def test_due_cadence():
    cfg = _cfg()
    tue = (2026, 11, 24)
    assert N.due(_at(*tue, 8, 0), {}, cfg) is False            # before the open
    assert N.due(_at(*tue, 8, 31), {}, cfg) is True             # first at the open
    state = {"next_at": _at(*tue, 8, 31).timestamp() + 60}
    assert N.due(_at(*tue, 8, 31, 30), state, cfg) is False     # not a minute yet
    assert N.due(_at(*tue, 8, 32, 1), state, cfg) is True
    assert N.due(_at(*tue, 15, 5), {}, cfg) is False            # closed, not settled yet
    assert N.due(_at(*tue, 15, 10), {}, cfg) is True            # the settling refresh
    done = {"after_close_done": "2026-11-24"}
    assert N.due(_at(*tue, 15, 10), done, cfg) is False         # once only
    assert N.due(_at(*tue, 20, 0), done, cfg) is False
    assert N.due(_at(2026, 11, 28, 10, 0), {}, cfg) is False    # Saturday
    assert N.due(_at(2026, 11, 26, 10, 0), {}, cfg) is False    # Thanksgiving


def test_due_half_day():
    cfg = _cfg()
    assert N.due(_at(2026, 11, 27, 11, 59), {}, cfg) is True
    assert N.due(_at(2026, 11, 27, 12, 5), {}, cfg) is False    # closed at 12:00
    assert N.due(_at(2026, 11, 27, 12, 10), {}, cfg) is True    # settled at 12:10


def test_due_after_close_waits_for_a_retry():
    # A failed settling refresh is tried again after retry_sec, not every call.
    cfg = _cfg()
    state = {"next_at": _at(2026, 11, 24, 15, 10).timestamp() + 120}
    assert N.due(_at(2026, 11, 24, 15, 11), state, cfg) is False
    assert N.due(_at(2026, 11, 24, 15, 12, 1), state, cfg) is True


# ── the refresh ──────────────────────────────────────────────────────────────
_OPEN = _at(2026, 11, 24, 10, 0)


def _screener(calls, fail_on=None):
    """A fake screener answering each body with its _COUNTS value; the call
    numbered ``fail_on`` (1-based) fails."""
    by_body = {repr(b): _COUNTS[k] for k, b in N.requests_for(_cfg())}

    def fake(url, **kwargs):
        calls.append(kwargs["json"])
        if fail_on is not None and len(calls) == fail_on:
            raise requests.ConnectionError("down")
        return _Answer(body={"totalCount": by_body[repr(kwargs["json"])]})
    return fake


@pytest.fixture
def degrades(monkeypatch):
    seen = []
    monkeypatch.setattr(N._degrade, "degraded", lambda area, **kw: seen.append(area))
    return seen


def _use(monkeypatch, cfg):
    monkeypatch.setattr(N.cfg_mod, "load", lambda: cfg)


def test_refresh_publishes_all_or_nothing(monkeypatch, degrades):
    from shared.bus import Bus
    bus = Bus()
    _use(monkeypatch, _cfg())
    calls = []
    monkeypatch.setattr(N, "_post", _screener(calls))
    state = {}
    assert N.refresh(bus, state, now=_OPEN) is True
    env = bus.cache_get(N.CACHE)
    assert env.payload["rows"]["all"]["4w"] == {"highs": 347, "lows": 158}
    assert env.payload["session"] == "2026-11-24"
    assert state["next_at"] == _OPEN.timestamp() + 60
    first = env.version

    calls.clear()
    later = _OPEN + dt.timedelta(minutes=2)
    monkeypatch.setattr(N, "_post", _screener(calls, fail_on=7))
    assert N.refresh(bus, state, now=later) is False
    assert bus.cache_get(N.CACHE).version == first               # the last full set stays
    assert degrades == ["market.new_highs"]
    assert state["next_at"] == later.timestamp() + 120           # retry_sec


def test_refresh_requests_are_sequential(monkeypatch, degrades):
    from shared.bus import Bus
    _use(monkeypatch, _cfg())
    calls = []
    monkeypatch.setattr(N, "_post", _screener(calls))
    N.refresh(Bus(), {}, now=_OPEN)
    assert calls == [b for _, b in N.requests_for(_cfg())]


def test_refresh_not_due_fetches_nothing(monkeypatch, degrades):
    from shared.bus import Bus
    _use(monkeypatch, _cfg())
    calls = []
    monkeypatch.setattr(N, "_post", _screener(calls))
    assert N.refresh(Bus(), {"next_at": _OPEN.timestamp() + 30}, now=_OPEN) is False
    assert calls == []


def test_refresh_settling_marks_the_day(monkeypatch, degrades):
    from shared.bus import Bus
    _use(monkeypatch, _cfg())
    monkeypatch.setattr(N, "_post", _screener([]))
    state = {}
    assert N.refresh(Bus(), state, now=_at(2026, 11, 24, 15, 10)) is True
    assert state["after_close_done"] == "2026-11-24"


def test_refresh_disabled_then_enabled(monkeypatch, degrades):
    from shared.bus import Bus
    bus = Bus()
    calls = []
    monkeypatch.setattr(N, "_post", _screener(calls))
    _use(monkeypatch, _cfg(enabled=False))
    state = {"next_at": _OPEN.timestamp() + 30}
    assert N.refresh(bus, state, now=_OPEN) is False
    off = bus.cache_get(N.CACHE)
    assert off.payload["enabled"] is False and off.payload["rows"] == {}
    assert N.refresh(bus, state, now=_OPEN) is False
    assert bus.cache_get(N.CACHE).version == off.version         # published once
    assert calls == []
    _use(monkeypatch, _cfg())
    assert N.refresh(bus, state, now=_OPEN) is True              # at once, not after next_at
    assert bus.cache_get(N.CACHE).payload["enabled"] is True


def test_refresh_never_raises(monkeypatch, degrades):
    from shared.bus import Bus

    def boom():
        raise RuntimeError("bad file")
    monkeypatch.setattr(N.cfg_mod, "load", boom)
    assert N.refresh(Bus(), {}, now=_OPEN) is False
    assert degrades == ["market.new_highs"]


def test_the_loop_refreshes_beside_the_poll_not_inside_it():
    """Thirteen requests in a row can take seconds: handed to the executor
    without an await, they never hold up the three-second poll."""
    import inspect

    from services.market_svc import scheduler
    src = inspect.getsource(scheduler.loop)
    assert "run_in_executor(None, new_highs.refresh" in src
    assert "await loop_.run_in_executor(None, new_highs.refresh" not in src
