import datetime as dt
from zoneinfo import ZoneInfo

from services.market_svc import scheduler as sch

_CT = ZoneInfo("America/Chicago")


def test_fast_cadence_during_rth():
    now = dt.datetime(2026, 7, 7, 10, 0, tzinfo=_CT)  # Tue 10:00 CT
    assert sch.poll_interval(now) == sch.RTH_INTERVAL_SEC


def test_slow_cadence_off_hours():
    now = dt.datetime(2026, 7, 7, 22, 0, tzinfo=_CT)  # Tue 22:00 CT
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC


def test_slow_cadence_on_weekend():
    now = dt.datetime(2026, 7, 11, 10, 0, tzinfo=_CT)  # Sat
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC


def test_slow_cadence_on_holiday():
    now = dt.datetime(2026, 7, 3, 10, 0, tzinfo=_CT)  # holiday in _HOLIDAYS
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC


def test_summary_due_fires_when_interval_elapsed():
    rth = dt.datetime(2026, 7, 7, 10, 0, tzinfo=_CT)
    # never run → due
    assert sch.summary_due(None, secs_since=0, now=rth) is True
    # just ran → not due
    assert sch.summary_due(1.0, secs_since=10, now=rth) is False
    # RTH interval elapsed → due
    assert sch.summary_due(1.0, secs_since=sch.SUMMARY_RTH_SEC + 1, now=rth) is True
    # off-hours uses the longer interval
    off = dt.datetime(2026, 7, 7, 22, 0, tzinfo=_CT)
    assert sch.summary_due(1.0, secs_since=sch.SUMMARY_RTH_SEC + 1, now=off) is False
    assert sch.summary_due(1.0, secs_since=sch.SUMMARY_OFFHOURS_SEC + 1, now=off) is True
