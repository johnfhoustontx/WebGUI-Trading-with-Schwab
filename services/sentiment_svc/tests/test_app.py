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
        assert r.json() == {"domain": "sentiment", "up": True}


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
