import datetime as dt
from zoneinfo import ZoneInfo

from services.market_svc import scheduler as sch

_CT = ZoneInfo("America/Chicago")


def test_poll_interval_values():
    # A macro dashboard updates tiles in place, so a slightly slower cadence is
    # imperceptible but roughly halves the Schwab /quotes volume (~24k → ~12k/day).
    # Weekend stays hard-throttled (futures closed → nothing ticks).
    assert sch.RTH_INTERVAL_SEC == 3
    assert sch.OFFHOURS_INTERVAL_SEC == 15
    assert sch.WEEKEND_INTERVAL_SEC == 60


def test_fast_cadence_during_rth():
    now = dt.datetime(2026, 7, 7, 10, 0, tzinfo=_CT)  # Tue 10:00 CT
    assert sch.poll_interval(now) == sch.RTH_INTERVAL_SEC


def test_slow_cadence_off_hours():
    now = dt.datetime(2026, 7, 7, 22, 0, tzinfo=_CT)  # Tue 22:00 CT
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC


def test_slow_cadence_on_weekend():
    # Saturday: the futures are CLOSED (they don't reopen until Sun 17:00 CT), so
    # nothing ticks — throttle harder than the normal off-hours pace.
    now = dt.datetime(2026, 7, 11, 10, 0, tzinfo=_CT)  # Sat
    assert sch.poll_interval(now) == sch.WEEKEND_INTERVAL_SEC


def test_slow_cadence_on_holiday():
    now = dt.datetime(2026, 7, 3, 10, 0, tzinfo=_CT)  # NYSE holiday
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


def test_summary_never_due_when_disabled():
    # The ticker toggle is off → no Claude call, no matter how due it otherwise is.
    rth = dt.datetime(2026, 7, 7, 10, 0, tzinfo=_CT)
    assert sch.summary_due(None, secs_since=0, now=rth, enabled=False) is False
    assert sch.summary_due(
        1.0, secs_since=sch.SUMMARY_RTH_SEC + 1, now=rth, enabled=False) is False


def test_summary_due_defaults_to_enabled():
    # Omitting `enabled` must behave exactly as before (callers/tests unchanged).
    rth = dt.datetime(2026, 7, 7, 10, 0, tzinfo=_CT)
    assert sch.summary_due(None, secs_since=0, now=rth) is True


def test_loop_gates_summary_on_the_enabled_flag():
    import inspect

    src = inspect.getsource(sch.loop)
    # The loop must read the live flag and feed it to the gate — so flipping the
    # toggle takes effect on the next cycle without a service restart.
    assert "summary_enabled" in src
    seg = src.split("summary_due", 1)[0]
    assert "handlers.summary_enabled(bus)" in seg


def test_loop_runs_summary_as_background_task():
    """The Claude summary (30s timeout, up to ~60s) must NOT be awaited inline in
    the 2s poll loop — it launches as a background task so the dashboard cadence
    never stalls once per ~40-min slot."""
    import inspect

    src = inspect.getsource(sch.loop)
    assert "create_task" in src
    # The blocking generate_summary is NOT directly awaited in the loop body.
    assert "await loop_.run_in_executor(None, compute.generate_summary" not in src


def test_poll_interval_throttles_deep_weekend():
    import datetime as dt
    # Saturday: futures closed all day -> slow throttle.
    sat = dt.datetime(2026, 7, 18, 10, 0, tzinfo=sch._CT)
    assert sch.poll_interval(sat) == sch.WEEKEND_INTERVAL_SEC
    # Sunday morning: still closed (futures reopen 17:00 CT Sunday).
    sun_am = dt.datetime(2026, 7, 19, 10, 0, tzinfo=sch._CT)
    assert sch.poll_interval(sun_am) == sch.WEEKEND_INTERVAL_SEC
    # Sunday evening after the futures reopen: back to the normal off-hours pace.
    sun_pm = dt.datetime(2026, 7, 19, 18, 0, tzinfo=sch._CT)
    assert sch.poll_interval(sun_pm) == sch.OFFHOURS_INTERVAL_SEC
