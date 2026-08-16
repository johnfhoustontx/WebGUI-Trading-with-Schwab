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
        body = r.json()
        assert body["domain"] == "options"
        assert body["up"] is True
        assert body["scheduler_alive"] is True


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


# Every handler ``scheduler.loop`` can hand to an executor. Stubbed wholesale in
# the loop test below — see there for why this list has to be complete.
_BRANCH_HANDLERS = (
    "refresh_paper_account", "refresh_paper_trades", "refresh_captured",
    "publish_captured_closed", "refresh_gamma", "publish_gamma_symbols",
    "publish_gex_status", "publish_gamma_briefing_index", "refresh_header",
    "collect_gex_history", "refresh_gamma_current",
    "run_driver_manage_and_refresh", "run_captured_manage_and_publish",
    "run_paper_entry_and_manage", "run_scheduled_gamma_analyze",
    "run_action_alert", "run_eod_summary", "run_market_snapshot",
)


def test_scheduler_scans_when_due(monkeypatch):
    """When the slot is due, the loop runs exactly one rescan before sleeping.

    The stubbing and the executor drain below are NOT belt-and-braces — without
    them this test leaks work into every test that follows it, and it did:

    one iteration of the real loop launches up to ten branches, each handing a
    handler to ``loop.run_in_executor``. Those run on THREADS. ``loop.close()``
    calls ``shutdown(wait=False)``, so the threads keep going after this test
    returns, executing real gamma / manage / paper work against the real DBs
    while later tests run. ``test_captured_autoclose_e2e`` monkeypatches
    ``signal_repricer.reprice_swing`` with a fixed-length iterator; a leaked
    manage branch landing inside that window drains it, and the test fails on
    ``StopIteration`` with a traceback pointing at code it never called.

    That was latent for as long as the leak existed and surfaced when an
    unrelated change made the gamma branch a few milliseconds slower — which is
    exactly how much it took to shift the collision into the wrong test. A
    pass/fail that depends on the timing of another test's background threads is
    not a signal, so the leak is closed here rather than absorbed.

    ``_BRANCH_HANDLERS`` must list EVERY handler the loop can submit: one
    unstubbed name is one real branch running loose again.
    """
    bus = Bus(fake=True)
    calls = []
    for name in _BRANCH_HANDLERS:
        monkeypatch.setattr(handlers, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(handlers, "autoclose_enabled", lambda *a, **k: False,
                        raising=False)
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
        # Wait for the executor threads BEFORE closing. close() only calls
        # shutdown(wait=False), which returns while they are still running.
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()

    assert calls == [bus]


def test_the_loop_test_leaves_no_branch_work_running():
    """Guards the stub list above against the loop growing a new branch.

    A branch added to ``scheduler.loop`` without its handler added to
    ``_BRANCH_HANDLERS`` silently starts running for real again, and the damage
    shows up as an unrelated test failing intermittently — the hardest kind to
    attribute. Comparing against the source keeps that honest.
    """
    import pathlib
    import re

    src = pathlib.Path(scheduler.__file__).read_text(encoding="utf-8")
    submitted = set(re.findall(r"handlers\.([a-z_]+)", src))
    # ``autoclose_enabled`` is a gate read inline, not submitted; ``rescan`` is
    # stubbed separately above because it is the one the test asserts on.
    exempt = {"autoclose_enabled", "rescan"}
    missing = submitted - exempt - set(_BRANCH_HANDLERS)
    assert not missing, (
        f"scheduler.loop can submit {sorted(missing)} but the loop test does "
        f"not stub them — add them to _BRANCH_HANDLERS")
