"""Tests for the scanner auto-scan scheduling decision (08:00–15:15 CT, 15 min)."""
import datetime as dt
from zoneinfo import ZoneInfo

from pages.options import scanner

CT = ZoneInfo("America/Chicago")


def test_due_during_market_hours_new_slot():
    now = dt.datetime(2026, 6, 15, 9, 0, tzinfo=CT)  # Monday 09:00 CT
    due, slot = scanner.autoscan_due(now, None)
    assert due is True
    assert slot is not None


def test_not_due_same_slot_twice():
    now = dt.datetime(2026, 6, 15, 9, 0, tzinfo=CT)
    _, slot = scanner.autoscan_due(now, None)
    due2, slot2 = scanner.autoscan_due(now, slot)
    assert due2 is False
    assert slot2 == slot


def test_due_again_next_slot():
    slot0 = scanner.autoscan_due(dt.datetime(2026, 6, 15, 9, 0, tzinfo=CT), None)[1]
    due, slot1 = scanner.autoscan_due(dt.datetime(2026, 6, 15, 9, 15, tzinfo=CT), slot0)
    assert due is True and slot1 != slot0


def test_not_due_on_weekend():
    now = dt.datetime(2026, 6, 14, 9, 0, tzinfo=CT)  # Sunday
    assert scanner.autoscan_due(now, None)[0] is False


def test_not_due_before_open_or_after_close():
    assert scanner.autoscan_due(dt.datetime(2026, 6, 15, 7, 0, tzinfo=CT), None)[0] is False
    assert scanner.autoscan_due(dt.datetime(2026, 6, 15, 15, 30, tzinfo=CT), None)[0] is False


def test_not_due_on_holiday():
    # 2026-12-25 is a Friday holiday in the scanner's calendar.
    assert scanner.autoscan_due(dt.datetime(2026, 12, 25, 9, 0, tzinfo=CT), None)[0] is False
