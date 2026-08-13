"""Momentum contract + publish handler + the nightly scheduler slot."""
import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from services.sentiment_svc import handlers, scheduler
from shared import market_calendar as mc
from shared.contracts.sentiment import MomentumSnapshot

CT = ZoneInfo("America/Chicago")


def _payload(session_date="2026-07-28"):
    return {
        "schema": 1,
        "computed_at": "2026-07-28T16:22:04-05:00",
        "session_date": session_date,
        "regime": {"state": "favorable", "lookback": "63/126",
                   "crash_risk": False, "reasons": ["SPY above its 200 DMA"]},
        "levels": {
            "sector": [{"symbol": "XLK", "label": "Information Technology",
                        "score": 1.2, "percentile": 90.0, "rank": 1,
                        "components": {"trend": 1.0}, "participation": 0.8}],
            "industry": [],
            "stock": [],
        },
        "excluded": [{"symbol": "TPIC", "reason": "liquidity"}],
    }


@pytest.fixture(autouse=True)
def _clear_momentum_state():
    # The last-run sentinel is module state; leaving it set would make the
    # next test's run a no-op skip.
    handlers.reset_momentum_state()
    yield
    handlers.reset_momentum_state()


class FakeBus:
    def __init__(self):
        self.cache = {}
        self.published = []

    def cache_set(self, key, payload, **kwargs):
        self.cache[key] = payload
        return len(self.cache)

    def publish(self, event, payload):
        self.published.append((event, payload))


# --- contract ---------------------------------------------------------------

def test_payload_round_trips_through_the_contract():
    snap = MomentumSnapshot(**_payload())

    assert snap.session_date == "2026-07-28"
    assert snap.regime["state"] == "favorable"
    assert snap.levels["sector"][0]["symbol"] == "XLK"


def test_contract_requires_the_three_levels():
    bad = _payload()
    del bad["levels"]["stock"]

    with pytest.raises(Exception):
        MomentumSnapshot(**bad)


def test_contract_defaults_excluded_to_empty():
    payload = _payload()
    del payload["excluded"]

    assert MomentumSnapshot(**payload).excluded == []


def test_a_real_compute_payload_validates():
    """The shape compute actually emits must satisfy the contract.

    A dead client keeps this offline — the payload shape is the assertion, and
    a unit test must never reach the proxy (client=None fans out ~390 fetches).
    """
    from services.sentiment_svc import compute, momentum_db

    class Dead:
        def get_daily_history(self, symbol, months=12):
            raise RuntimeError("offline")

        def get_quotes(self, symbols):
            raise RuntimeError("offline")

    conn = momentum_db.connect(":memory:")
    try:
        built = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                         client=Dead())
    finally:
        conn.close()

    snap = MomentumSnapshot(**built)
    assert snap.session_date == "2026-07-28"
    assert set(snap.levels) == {"sector", "industry", "stock"}


# --- handler ----------------------------------------------------------------

def test_handler_caches_and_publishes(monkeypatch):
    monkeypatch.setattr(handlers.compute, "compute_momentum",
                        lambda **kw: _payload())
    bus = FakeBus()

    handlers.refresh_momentum(bus)

    assert handlers.CACHE_MOMENTUM in bus.cache
    assert bus.cache[handlers.CACHE_MOMENTUM]["session_date"] == "2026-07-28"
    assert bus.published and bus.published[0][0] == handlers.EVENT_MOMENTUM


def test_handler_does_not_touch_the_existing_views(monkeypatch):
    monkeypatch.setattr(handlers.compute, "compute_momentum",
                        lambda **kw: _payload())
    bus = FakeBus()

    handlers.refresh_momentum(bus)

    assert set(bus.cache) == {handlers.CACHE_MOMENTUM}


def test_handler_never_raises_when_compute_blows_up(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(handlers.compute, "compute_momentum", boom)
    bus = FakeBus()

    handlers.refresh_momentum(bus)          # must not raise

    assert bus.cache == {}


def test_a_second_run_for_the_same_session_is_skipped(monkeypatch):
    calls = []
    monkeypatch.setattr(handlers.compute, "compute_momentum",
                        lambda **kw: calls.append(1) or _payload())
    handlers.reset_momentum_state()
    bus = FakeBus()

    handlers.refresh_momentum(bus)
    handlers.refresh_momentum(bus)

    assert len(calls) == 1


def test_force_reruns_the_same_session(monkeypatch):
    calls = []
    monkeypatch.setattr(handlers.compute, "compute_momentum",
                        lambda **kw: calls.append(1) or _payload())
    handlers.reset_momentum_state()
    bus = FakeBus()

    handlers.refresh_momentum(bus)
    handlers.refresh_momentum(bus, force=True)

    assert len(calls) == 2


def test_manual_command_forces_a_run(monkeypatch):
    calls = []
    monkeypatch.setattr(handlers.compute, "compute_momentum",
                        lambda **kw: calls.append(1) or _payload())
    handlers.reset_momentum_state()
    bus = FakeBus()

    handlers.handle_command(bus, SimpleNamespace(type="refresh_momentum"))
    handlers.handle_command(bus, SimpleNamespace(type="refresh_momentum"))

    assert len(calls) == 2


# --- scheduler slot ---------------------------------------------------------

def test_slot_does_not_fire_during_rth():
    now = dt.datetime(2026, 7, 28, 11, 0, tzinfo=CT)

    due, _ = scheduler.momentum_due(now, None)

    assert due is False


def test_slot_fires_after_the_close_on_a_weekday():
    now = dt.datetime(2026, 7, 28, 16, 25, tzinfo=CT)

    due, slot = scheduler.momentum_due(now, None)

    assert due is True
    assert slot == "2026-07-28"


def test_slot_does_not_fire_twice_for_the_same_session():
    now = dt.datetime(2026, 7, 28, 16, 25, tzinfo=CT)
    _, slot = scheduler.momentum_due(now, None)

    due, _ = scheduler.momentum_due(dt.datetime(2026, 7, 28, 18, 0, tzinfo=CT), slot)

    assert due is False


def test_slot_fires_again_the_next_trading_day():
    due, _ = scheduler.momentum_due(
        dt.datetime(2026, 7, 29, 16, 25, tzinfo=CT), "2026-07-28")

    assert due is True


def test_slot_does_not_fire_before_the_scheduled_time():
    now = dt.datetime(2026, 7, 28, 16, 10, tzinfo=CT)

    due, _ = scheduler.momentum_due(now, None)

    assert due is False


def test_slot_does_not_fire_on_a_weekend():
    due, _ = scheduler.momentum_due(dt.datetime(2026, 8, 1, 17, 0, tzinfo=CT), None)

    assert due is False


def test_slot_does_not_fire_on_a_market_holiday():
    holiday = dt.date(2026, 12, 25)                      # Christmas, a Friday
    assert mc.is_holiday(holiday)                        # non-vacuity
    now = dt.datetime(holiday.year, holiday.month, holiday.day, 17, 0, tzinfo=CT)

    due, _ = scheduler.momentum_due(now, None)

    assert due is False


def test_slot_uses_the_shared_holiday_calendar():
    """Not a private copy — the calendar lives in shared/market_calendar.py for
    the WHOLE repo, and its holidays are DERIVED per year rather than listed.

    This asserts BEHAVIOR rather than a module attribute: the local
    ``scheduler._HOLIDAYS`` set this once read was retired in the calendar
    consolidation, and a bounded set would have made every 2028+ holiday fire
    the nightly slot.
    """
    # A holiday beyond any hand-maintained 2026-27 list still suppresses the slot.
    mlk_2028 = dt.date(2028, 1, 17)
    assert mc.is_holiday(mlk_2028)
    due, _ = scheduler.momentum_due(
        dt.datetime(2028, 1, 17, 17, 0, tzinfo=CT), None)
    assert due is False
    # ...and an ordinary weekday next to it still does fire.
    due, _ = scheduler.momentum_due(
        dt.datetime(2028, 1, 18, 17, 0, tzinfo=CT), None)
    assert due is True
