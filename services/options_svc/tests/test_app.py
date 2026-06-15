"""Tests for the options service FastAPI app + auto-scan scheduler (Task 2.4).

Hermetic: ``handlers.rescan`` is monkeypatched to a no-op recorder so nothing
touches a live proxy/compute. The schedule logic (ported verbatim from
``webgui/pages/options/scanner.py``) is unit-tested directly with constructed
tz-aware datetimes.

pytest-asyncio is NOT installed in this venv, so the scheduler coroutine is
driven manually via a fresh event loop + ``run_until_complete``.
"""
import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from shared.bus import Bus
from services.options_svc import handlers, scheduler


def test_health(monkeypatch):
    """/health is 200 + the right body; scheduler is harmless on startup."""
    monkeypatch.setattr(handlers, "rescan", lambda *a, **k: None)
    from services.options_svc.app import app

    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json() == {"domain": "options", "up": True}


def test_autoscan_due_logic():
    """The ported schedule helpers gate the scan correctly."""
    ct = scheduler._CT

    # Weekday (Mon 2026-06-15) at 09:00 CT is due, then not due again same slot.
    weekday_9 = datetime(2026, 6, 15, 9, 0, tzinfo=ct)
    assert scheduler._is_trading_day(weekday_9)
    assert scheduler._is_market_hours(weekday_9)
    due, slot = scheduler.autoscan_due(weekday_9, None)
    assert due is True
    due2, slot2 = scheduler.autoscan_due(weekday_9, slot)
    assert due2 is False
    assert slot2 == slot

    # Weekend (Sat 2026-06-13) is not a trading day → not due.
    weekend = datetime(2026, 6, 13, 9, 0, tzinfo=ct)
    assert scheduler._is_trading_day(weekend) is False
    assert scheduler.autoscan_due(weekend, None)[0] is False

    # Weekday before the window (07:00 CT) is not in market hours → not due.
    before = datetime(2026, 6, 15, 7, 0, tzinfo=ct)
    assert scheduler._is_market_hours(before) is False
    assert scheduler.autoscan_due(before, None)[0] is False

    # Holiday (2026-01-01, a Thursday) is not a trading day → not due.
    holiday = datetime(2026, 1, 1, 9, 0, tzinfo=ct)
    assert scheduler._is_trading_day(holiday) is False
    assert scheduler.autoscan_due(holiday, None)[0] is False


def test_scheduler_scans_when_due(monkeypatch):
    """When the slot is due, the loop runs exactly one rescan before sleeping."""
    bus = Bus(fake=True)
    calls = []
    monkeypatch.setattr(handlers, "rescan", lambda b: calls.append(b))

    # Fixed in-window weekday so the first iteration is always due.
    fixed = datetime(2026, 6, 15, 9, 0, tzinfo=scheduler._CT)
    monkeypatch.setattr(scheduler, "_market_now", lambda: fixed)

    # Break out of the infinite loop after the first sleep.
    async def _boom(*a, **k):
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", _boom)

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(scheduler.loop(bus))
    finally:
        loop.close()

    assert calls == [bus]
