"""Tests for the sentiment service scheduler gating (Phase 4)."""
import datetime as dt
from zoneinfo import ZoneInfo

from services.sentiment_svc import scheduler

_CT = ZoneInfo("America/Chicago")


def _ct(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=_CT)


def test_trend_interval_is_15_min():
    assert scheduler.TREND_INTERVAL_SEC == 900


def test_trend_due_when_interval_elapsed():
    assert scheduler.trend_due(900, 0) is True


def test_trend_not_due_before_interval():
    assert scheduler.trend_due(600, 0) is False


def test_trend_due_on_cold_start():
    assert scheduler.trend_due(900, None) is True


# --- off-hours refresh gating (P4) --------------------------------------------
# During RTH (Mon-Fri 08:30-15:00 CT) the refresh runs every tick (its normal
# 120s cadence); off-hours/weekends it throttles to once per _OFFHOURS_INTERVAL_MIN
# slot. Mirrors options_svc.scheduler.periodic_refresh_due.
# 2026-06-15 is a Monday; 2026-06-13 is a Saturday.

def test_refresh_due_during_rth_always_true():
    # 10:00 CT Monday, in the RTH window -> due every tick, slot unchanged.
    due, slot = scheduler.refresh_due(_ct(2026, 6, 15, 10, 0), "prev")
    assert due is True and slot == "prev"


def test_refresh_due_rth_boundaries():
    # 08:30 CT (open) is in-window; 15:00 CT (close) is the exclusive upper edge.
    due_open, _ = scheduler.refresh_due(_ct(2026, 6, 15, 8, 30), None)
    assert due_open is True
    due_close, slot_close = scheduler.refresh_due(_ct(2026, 6, 15, 15, 0), None)
    assert due_close is True  # first off-hours tick still refreshes
    # 15:01 is off-hours; within the same 5-min slot it should throttle.
    due2, _ = scheduler.refresh_due(_ct(2026, 6, 15, 15, 1), slot_close)
    assert due2 is False


def test_refresh_due_off_hours_first_tick_then_throttles():
    # Evening (off-hours): first tick due, subsequent ticks in the same throttle
    # slot are not, next slot fires again. Off-hours interval is 15 min.
    assert scheduler._OFFHOURS_INTERVAL_MIN == 15
    due1, slot1 = scheduler.refresh_due(_ct(2026, 6, 15, 20, 0), None)
    assert due1 is True and slot1 is not None
    due2, slot2 = scheduler.refresh_due(_ct(2026, 6, 15, 20, 10), slot1)
    assert due2 is False and slot2 == slot1  # 20:00-20:10 = same 15-min slot
    due3, slot3 = scheduler.refresh_due(_ct(2026, 6, 15, 20, 16), slot1)
    assert due3 is True and slot3 != slot1  # crossed into the next 15-min slot


def test_refresh_due_weekend_throttles():
    # Saturday -> off-hours regardless of time -> throttled to the 15-min slot.
    due1, slot1 = scheduler.refresh_due(_ct(2026, 6, 13, 10, 0), None)
    assert due1 is True
    due2, _ = scheduler.refresh_due(_ct(2026, 6, 13, 10, 1), slot1)
    assert due2 is False


def test_refresh_due_holiday_throttles():
    # 2026-07-03 is an observed holiday (in _HOLIDAYS) -> off-hours -> throttled.
    due1, slot1 = scheduler.refresh_due(_ct(2026, 7, 3, 10, 0), None)
    assert due1 is True
    due2, _ = scheduler.refresh_due(_ct(2026, 7, 3, 10, 1), slot1)
    assert due2 is False


def test_refresh_due_before_rth_is_off_hours():
    # 07:00 CT (pre-open) is off-hours -> throttled.
    due1, slot1 = scheduler.refresh_due(_ct(2026, 6, 15, 7, 0), None)
    assert due1 is True
    due2, _ = scheduler.refresh_due(_ct(2026, 6, 15, 7, 1), slot1)
    assert due2 is False


def test_holidays_sourced_from_shared_calendar():
    from services.sentiment_svc import scheduler
    from shared import market_calendar as mc
    assert scheduler._HOLIDAYS is mc.HOLIDAYS
