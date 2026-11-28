"""Breadth's new highs / lows, read from the market service's view.

Schwab has no $NYHGH / $NYLOW (measured 2026-11-27), so the counts come from
``cache:market:new_highs``. A count is used only when it is today's and fresh;
anything else is no reading, never 0.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from services.sentiment_svc import new_highs_input as NH

CT = ZoneInfo("America/Chicago")
WED = dt.date(2026, 11, 25)                       # a full trading day
IN_SESSION = dt.datetime(2026, 11, 25, 10, 0, tzinfo=CT)
AFTER_CLOSE = dt.datetime(2026, 11, 25, 19, 0, tzinfo=CT)
NEXT_PREOPEN = dt.datetime(2026, 11, 26, 7, 0, tzinfo=CT)


def _view(**over):
    v = {"enabled": True, "source": "TradingView", "universe": 3426,
         "session": WED.isoformat(), "min_price": 5.0,
         "exchanges": ["NYSE", "NASDAQ", "AMEX"], "public": True,
         "stale_after_sec": 300,
         "counts": {"52w": {"highs": 90, "lows": 10},
                    "13w": {"highs": 168, "lows": 24},
                    "4w": {"highs": 266, "lows": 49}}}
    v.update(over)
    return v


def _ts(when):
    return when.astimezone(dt.timezone.utc).isoformat()


def test_todays_fresh_counts_are_read_for_the_window():
    ts = _ts(IN_SESSION - dt.timedelta(seconds=60))
    assert NH.pick(_view(), ts, IN_SESSION, "52w") == (90.0, 10.0)
    assert NH.pick(_view(), ts, IN_SESSION, "4w") == (266.0, 49.0)


def test_a_real_zero_is_a_count():
    view = _view(counts={"52w": {"highs": 0, "lows": 0}})
    assert NH.pick(view, _ts(IN_SESSION), IN_SESSION, "52w") == (0.0, 0.0)


@pytest.mark.parametrize("view", [
    None, "x", {}, _view(enabled=False), _view(counts={}),
    _view(counts={"52w": {"highs": 90}}),
    _view(counts={"52w": {"highs": 90, "lows": -1}}),
    _view(counts={"52w": {"highs": True, "lows": 10}}),
    _view(counts={"52w": {"highs": 1.5, "lows": 10}}),
    _view(counts={"52w": "x"}),
])
def test_a_view_without_a_usable_count_is_no_reading(view):
    assert NH.pick(view, _ts(IN_SESSION), IN_SESSION, "52w") is None


def test_yesterdays_counts_before_the_open_are_no_reading():
    """At 07:00 the view still holds Wednesday's close: not today's breadth."""
    assert NH.pick(_view(), _ts(AFTER_CLOSE), NEXT_PREOPEN, "52w") is None


def test_counts_that_stopped_refreshing_in_the_session_are_no_reading():
    old = _ts(IN_SESSION - dt.timedelta(seconds=301))
    assert NH.pick(_view(), old, IN_SESSION, "52w") is None
    assert NH.pick(_view(), None, IN_SESSION, "52w") is None
    assert NH.pick(_view(), "not a time", IN_SESSION, "52w") is None


def test_the_days_settled_counts_still_read_after_the_close():
    """Nothing refreshes after the settling pass, so age says nothing then."""
    settled = _ts(dt.datetime(2026, 11, 25, 15, 10, tzinfo=CT))
    assert NH.pick(_view(), settled, AFTER_CLOSE, "52w") == (90.0, 10.0)


def test_read_takes_the_view_and_its_confirmed_stamp_from_the_bus():
    reset_fake_bus()
    bus = Bus(fake=True)
    assert NH.read(bus, now=IN_SESSION) is None            # nothing published
    bus.cache_set(NH.CACHE, _view())
    _ver, ts = bus.cache_metas([NH.CACHE])[NH.CACHE]
    now = dt.datetime.fromisoformat(ts).astimezone(CT)
    view = _view(session=now.date().isoformat())
    bus.cache_set(NH.CACHE, view)
    assert NH.read(bus, now=now) == (90.0, 10.0)


def test_read_takes_the_window_from_config(monkeypatch):
    seen = {}

    def _pick(view, ts, now, window):
        seen["window"] = window
        return None

    monkeypatch.setattr(NH, "pick", _pick)
    monkeypatch.setattr(NH.composite_config, "highs_lows_window", lambda: "13w")
    reset_fake_bus()
    bus = Bus(fake=True)
    bus.cache_set(NH.CACHE, _view())
    NH.read(bus, now=IN_SESSION)
    assert seen["window"] == "13w"


def test_read_never_raises_on_a_broken_bus():
    class _Broken:
        def cache_get(self, key):
            raise RuntimeError("down")

        def cache_metas(self, keys):
            raise RuntimeError("down")

    assert NH.read(_Broken(), now=IN_SESSION) is None
