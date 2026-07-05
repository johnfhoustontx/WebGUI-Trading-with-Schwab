"""Tests for the driver service scheduler gate (Task #29c).

``morning_due`` fires the morning pipeline at most once per trading day, on or
after 09:28 ET (mirroring ``config.AGENT_RUN_HOUR/MIN``), on weekdays that are not
NYSE market holidays (``compute.run_morning`` ALSO returns a no_trade payload on a
holiday, as defence-in-depth). The gate needs trading-day + time + once-per-day
dedup. The ``loop`` is not unit-tested (it owns its own sleep cadence, like the
other services' loops).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from services.driver_svc import scheduler

_ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_ET)


def test_due_at_run_time_first_time():
    # 2026-06-16 is a Tuesday.
    due, day = scheduler.morning_due(_et(2026, 6, 16, 9, 28), None)
    assert due is True
    assert day == "2026-06-16"


def test_due_after_run_time_catch_up():
    """Service started at 11:00 ET (past 09:28) with no prior run → fire (catch-up)."""
    due, day = scheduler.morning_due(_et(2026, 6, 16, 11, 0), None)
    assert due is True
    assert day == "2026-06-16"


def test_not_due_before_run_time():
    due, day = scheduler.morning_due(_et(2026, 6, 16, 9, 27), None)
    assert due is False
    assert day is None  # unchanged last_run_date passed through


def test_not_due_twice_same_day():
    due, day = scheduler.morning_due(_et(2026, 6, 16, 9, 28), "2026-06-16")
    assert due is False
    assert day == "2026-06-16"  # already ran today


def test_due_again_next_day():
    due, day = scheduler.morning_due(_et(2026, 6, 17, 9, 28), "2026-06-16")
    assert due is True
    assert day == "2026-06-17"


def test_not_due_on_weekend():
    # 2026-06-20 is a Saturday, 2026-06-21 a Sunday.
    sat, _ = scheduler.morning_due(_et(2026, 6, 20, 10, 0), None)
    sun, _ = scheduler.morning_due(_et(2026, 6, 21, 10, 0), None)
    assert sat is False and sun is False


def test_not_due_on_market_holiday():
    # 2026-07-03 (Fri) is the observed Independence Day holiday — the gate must NOT
    # fire (defence beyond compute.run_morning's no_trade), passing last_run_date
    # through unchanged; the adjacent trading day (Thu 07-02) at the same time fires.
    assert _et(2026, 7, 3, 10, 0).date() in scheduler._HOLIDAYS
    due, day = scheduler.morning_due(_et(2026, 7, 3, 10, 0), None)
    assert due is False and day is None
    assert scheduler.morning_due(_et(2026, 7, 2, 10, 0), None)[0] is True


def test_run_time_matches_config():
    """The gate's run time must match the legacy morning-agent schedule."""
    assert (scheduler.RUN_HOUR, scheduler.RUN_MIN) == (9, 28)
