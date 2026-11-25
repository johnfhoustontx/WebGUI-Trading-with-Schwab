"""Tests for the portfolio scheduler (Task #21).

The scheduler builds the model, streams quotes on a background thread, and
throttle-publishes on ticks. The blocking ``loop``/``_stream_worker`` own their
own sleep/SSE cadence (not unit-tested directly, like the driver loop); the pure
decision helper ``rebuild_due`` and the tick-application ``apply_tick_to_state``
are tested here.
"""
import asyncio
import datetime as dt

from services.portfolio_svc import scheduler
from services.portfolio_svc.state import PortfolioState

_CT = scheduler._CT
# A weekday within RTH (Wed 2026-07-01 10:00 CT) and off-hours (same day 22:00 CT).
_RTH = dt.datetime(2026, 7, 1, 10, 0, tzinfo=_CT)
_OFFHOURS = dt.datetime(2026, 7, 1, 22, 0, tzinfo=_CT)
_WEEKEND = dt.datetime(2026, 7, 4, 10, 0, tzinfo=_CT)  # Saturday


def test_rebuild_due_on_interval_during_rth():
    # During market hours the normal ~10-min interval governs.
    assert scheduler.rebuild_due(0, False, interval=600, now=_RTH) is False
    assert scheduler.rebuild_due(599, False, interval=600, now=_RTH) is False
    assert scheduler.rebuild_due(600, False, interval=600, now=_RTH) is True
    assert scheduler.rebuild_due(601, False, interval=600, now=_RTH) is True


def test_rebuild_due_on_request():
    # A pending refresh command forces a rebuild even before the interval,
    # regardless of the market gate (RTH, off-hours, or weekend).
    assert scheduler.rebuild_due(0, True, interval=600, now=_RTH) is True
    assert scheduler.rebuild_due(0, True, interval=600, now=_OFFHOURS) is True
    assert scheduler.rebuild_due(0, True, interval=600, now=_WEEKEND) is True


def test_rebuild_due_offhours_throttled_to_longer_interval():
    # Off-hours the normal interval is NOT enough — it throttles to the longer
    # off-hours interval before a periodic rebuild is due.
    oh = scheduler.OFFHOURS_REBUILD_INTERVAL_SEC
    assert oh > 600
    assert scheduler.rebuild_due(600, False, interval=600, now=_OFFHOURS) is False
    assert scheduler.rebuild_due(oh - 1, False, interval=600, now=_OFFHOURS) is False
    assert scheduler.rebuild_due(oh, False, interval=600, now=_OFFHOURS) is True
    assert scheduler.rebuild_due(oh + 1, False, interval=600, now=_OFFHOURS) is True


def test_rebuild_due_weekend_throttled_like_offhours():
    # Weekends/holidays are off-hours: normal interval insufficient, long one is.
    oh = scheduler.OFFHOURS_REBUILD_INTERVAL_SEC
    assert scheduler.rebuild_due(600, False, interval=600, now=_WEEKEND) is False
    assert scheduler.rebuild_due(oh, False, interval=600, now=_WEEKEND) is True


def test_apply_tick_to_state_updates_model_and_marks_dirty():
    state = PortfolioState()
    state.raw_model = {
        "holdings": [{"symbol": "AAPL", "asset_type": "EQUITY",
                      "sector": "Technology", "sector_etf": "XLK",
                      "quantity": 10, "avg_price": 100.0,
                      "market_value": 1000.0}],
        "sectors": [{"sector": "Technology", "weight": 1.0}],
    }
    scheduler.apply_tick_to_state(state, {"symbol": "AAPL", "last": 110.0,
                                          "net_change": 1.0})
    assert state.raw_model["holdings"][0]["market_value"] == 1100.0
    assert state.dirty is True


def test_reconnect_delay_grows_and_caps():
    # Exponential growth from the base, capped at RECONNECT_WAIT_MAX_SEC.
    base = scheduler.RECONNECT_WAIT_SEC
    cap = scheduler.RECONNECT_WAIT_MAX_SEC
    assert scheduler.reconnect_delay(0) == base           # first reconnect
    assert scheduler.reconnect_delay(1) == base * 2       # doubles
    assert scheduler.reconnect_delay(2) == base * 4
    # Grows monotonically until it saturates at the cap and never exceeds it.
    prev = 0
    for n in range(0, 20):
        d = scheduler.reconnect_delay(n)
        assert d >= prev
        assert d <= cap
        prev = d
    assert scheduler.reconnect_delay(20) == cap           # long-broken → capped


def test_reconnect_delay_reset_returns_to_base():
    # A reset (consecutive_failures back to 0) returns to the base delay.
    assert scheduler.reconnect_delay(5) > scheduler.reconnect_delay(0)
    assert scheduler.reconnect_delay(0) == scheduler.RECONNECT_WAIT_SEC


def test_stream_worker_logs_and_backs_off_on_failure(monkeypatch, caplog):
    # A broken stream (make_data raises) must LOG a warning and back off using
    # the capped-exponential delay, not raise out or spin at a fixed interval.
    delays = []

    class _Stop:
        def __init__(self, n):
            self._n, self._i = n, 0

        def is_set(self):
            self._i += 1
            return self._i > self._n  # allow n loop iterations, then stop

        def wait(self, secs):
            delays.append(secs)

    def _boom():
        raise RuntimeError("auth broken")

    monkeypatch.setattr(scheduler.compute, "make_data", _boom)

    state = PortfolioState()
    with caplog.at_level("WARNING", logger="portfolio_svc.scheduler"):
        scheduler._stream_worker(state, _Stop(3))

    assert state.streaming is False
    # Backoff grows across the failed attempts (base, base*2, base*4).
    assert delays == [scheduler.reconnect_delay(0),
                      scheduler.reconnect_delay(1),
                      scheduler.reconnect_delay(2)]
    assert any("stream setup failed" in r.message for r in caplog.records)


def test_loop_is_coroutine():
    assert asyncio.iscoroutinefunction(scheduler.loop)


# --- the option stream (live marks for option legs) ---------------------------

_PUT = "AVGO  261125P00312500"


def _option_state():
    state = PortfolioState()
    state.raw_model = {
        "holdings": [{"symbol": "AAPL", "asset_type": "EQUITY", "quantity": 1,
                      "sector": "Technology", "sector_etf": "XLK",
                      "market_value": 100.0},
                     {"symbol": _PUT, "asset_type": "OPTION", "quantity": 2,
                      "sector": "Technology",
                      "avg_price": 12.40, "market_value": 1880.0,
                      "day_pl": -150.0, "total_pl": -600.0}],
        "sectors": [],
    }
    return state


class _OnceStop:
    """Lets the worker run one stream session, then stops it."""
    def __init__(self):
        self._set = False

    def is_set(self):
        return self._set

    def wait(self, secs):
        self._set = True


def test_the_option_worker_streams_the_legs_and_applies_their_marks(monkeypatch):
    state = _option_state()
    seen = {}

    class _Data:
        def stream_options(self, symbols, on_tick, should_stop):
            seen["symbols"] = list(symbols)
            on_tick({"symbol": _PUT, "mark": 10.0})

        def stream_quotes(self, *a):
            raise AssertionError("the option worker must not open the equity stream")

    monkeypatch.setattr(scheduler.compute, "make_data", _Data)
    scheduler._stream_worker(state, _OnceStop(), kind="options")
    assert seen["symbols"] == [_PUT]
    assert state.raw_model["holdings"][1]["market_value"] == 2000.0
    assert state.dirty is True
    # The live/manual badge belongs to the equity stream alone.
    assert state.streaming is False


def test_the_equity_worker_leaves_option_legs_off_its_stream(monkeypatch):
    state = _option_state()
    seen = {}

    class _Data:
        def stream_quotes(self, symbols, on_tick, should_stop):
            seen["symbols"] = list(symbols)

    monkeypatch.setattr(scheduler.compute, "make_data", _Data)
    scheduler._stream_worker(state, _OnceStop())
    assert _PUT not in seen["symbols"]
    assert seen["symbols"] == ["AAPL", "XLK", "SPY"]
