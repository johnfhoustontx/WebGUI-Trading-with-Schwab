"""Tests for the sentiment service FastAPI app + scheduler (Task 1.3).

Hermetic: ``handlers.refresh`` is monkeypatched to a no-op recorder so nothing
touches a live proxy/compute. The app's startup runs the scheduler once (the
no-op refresh) which is harmless and fast.

pytest-asyncio is NOT installed in this venv, so the scheduler coroutine is
driven manually via a fresh event loop + ``run_until_complete``.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from shared.bus import Bus
from services.sentiment_svc import handlers, scheduler


def test_health(monkeypatch):
    """/health is 200 + the right body; scheduler's no-op refresh on startup is harmless."""
    monkeypatch.setattr(handlers, "refresh", lambda *a, **k: None)
    from services.sentiment_svc.app import app

    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["domain"] == "sentiment"
        assert body["up"] is True
        assert body["scheduler_alive"] is True


def test_scheduler_runs_full_refresh_first(monkeypatch):
    """First refresh must use with_sectors=True (full refresh), mirroring _bg_loop."""
    bus = Bus(fake=True)
    seen = []

    def _rec(b, with_sectors=False):
        seen.append(with_sectors)

    monkeypatch.setattr(handlers, "refresh", _rec)

    # Break out of the infinite loop after the first refresh by making the
    # scheduler's asyncio.sleep raise CancelledError on first await.
    async def _boom(*a, **k):
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", _boom)

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(scheduler.loop(bus))
    finally:
        loop.close()

    # The full refresh (with_sectors=True) must be the first recorded call.
    assert seen and seen[0] is True


def test_scheduler_runs_one_shot_rotation_refresh(monkeypatch):
    """A one-shot rotation refresh runs at startup (after the full refresh,
    before the poll loop) so the manual-refresh-only page has data on first load."""
    bus = Bus(fake=True)
    order = []

    monkeypatch.setattr(handlers, "refresh",
                        lambda b, with_sectors=False: order.append(("refresh", with_sectors)))
    monkeypatch.setattr(handlers, "refresh_rotation",
                        lambda b: order.append(("rotation",)))

    async def _boom(*a, **k):
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", _boom)

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(scheduler.loop(bus))
    finally:
        loop.close()

    # Full refresh first, then the one-shot rotation refresh, before the poll.
    assert order[:2] == [("refresh", True), ("rotation",)]


def test_scheduler_rotation_failure_non_fatal(monkeypatch):
    """A rotation-refresh failure at startup must not kill the loop — the
    composite poll still proceeds (we trip CancelledError on the first sleep)."""
    bus = Bus(fake=True)
    order = []

    monkeypatch.setattr(handlers, "refresh",
                        lambda b, with_sectors=False: order.append(("refresh", with_sectors)))

    def _boom_rotation(b):
        raise RuntimeError("rotation exploded")

    monkeypatch.setattr(handlers, "refresh_rotation", _boom_rotation)

    async def _boom(*a, **k):
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", _boom)

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(scheduler.loop(bus))
    finally:
        loop.close()

    # Full refresh ran; rotation blew up but was swallowed so the loop reached
    # the poll's sleep (which is what raised CancelledError).
    assert order == [("refresh", True)]
