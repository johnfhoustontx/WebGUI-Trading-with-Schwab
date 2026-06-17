"""Tests for the portfolio scheduler (Task #21).

The scheduler builds the model, streams quotes on a background thread, and
throttle-publishes on ticks. The blocking ``loop``/``_stream_worker`` own their
own sleep/SSE cadence (not unit-tested directly, like the driver loop); the pure
decision helper ``rebuild_due`` and the tick-application ``apply_tick_to_state``
are tested here.
"""
import asyncio

from services.portfolio_svc import scheduler
from services.portfolio_svc.state import PortfolioState


def test_rebuild_due_on_interval():
    assert scheduler.rebuild_due(0, False, interval=600) is False
    assert scheduler.rebuild_due(600, False, interval=600) is True
    assert scheduler.rebuild_due(601, False, interval=600) is True


def test_rebuild_due_on_request():
    # A pending refresh command forces a rebuild even before the interval.
    assert scheduler.rebuild_due(0, True, interval=600) is True


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


def test_loop_is_coroutine():
    assert asyncio.iscoroutinefunction(scheduler.loop)
