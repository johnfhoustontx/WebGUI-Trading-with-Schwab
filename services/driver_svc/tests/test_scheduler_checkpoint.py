"""Tests for the autonomous checkpoint cadence + halt re-arm (Phase 6).

``checkpoint_due`` fires the autonomous decision cycle at most once per
``settings.CHECKPOINT_MIN``-minute slot during the entry window (09:45–15:30 ET,
inside RTH — open's first 15 min skipped, no new entries in the last 30 min) on weekdays;
``should_rearm`` clears a stale overnight halt latch on the next trading day. Both
are PURE (ET-aware datetimes / plain dicts in — no clock, no bus), so they take
fixed inputs here. The ``loop`` itself is not unit-tested (it owns its sleep
cadence, like the other services' loops).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from services.driver_svc import scheduler, settings

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# ── checkpoint_due ───────────────────────────────────────────────────────────


def test_checkpoint_due_every_30m_in_rth():
    # 2026-06-24 is a Wednesday.
    now = _et(2026, 6, 24, 10, 0)
    due, ts = scheduler.checkpoint_due(now, None)
    assert due is True
    # same 30-min slot (10:00–10:29) → not due again, slot key unchanged.
    again_due, again_ts = scheduler.checkpoint_due(_et(2026, 6, 24, 10, 20), ts)
    assert again_due is False
    assert again_ts == ts
    # next slot (10:30) → due.
    assert scheduler.checkpoint_due(_et(2026, 6, 24, 10, 35), ts)[0] is True


def test_checkpoint_due_at_window_open():
    """09:45 is the first entry-window slot — due (the 09:30 open bell is skipped)."""
    due, ts = scheduler.checkpoint_due(_et(2026, 6, 24, 9, 45), None)
    assert due is True
    assert ts == f"2026-06-24:{(9 * 60 + 45) // settings.CHECKPOINT_MIN}"


def test_checkpoint_not_due_at_open_bell():
    """The 09:30 open bell is intentionally OUTSIDE the entry window — not due."""
    assert scheduler.checkpoint_due(_et(2026, 6, 24, 9, 30), None)[0] is False
    # the whole first ~15 min after the open is skipped.
    assert scheduler.checkpoint_due(_et(2026, 6, 24, 9, 44), None)[0] is False


def test_checkpoint_not_due_before_open():
    assert scheduler.checkpoint_due(_et(2026, 6, 24, 8, 0), None)[0] is False


def test_checkpoint_not_due_in_last_half_hour():
    """No new entries in the last 30 min — a slot at/after 15:30 ET never fires."""
    assert scheduler.checkpoint_due(_et(2026, 6, 24, 15, 30), None)[0] is False
    assert scheduler.checkpoint_due(_et(2026, 6, 24, 15, 59), None)[0] is False
    # ...and the actual 16:00 close, of course.
    assert scheduler.checkpoint_due(_et(2026, 6, 24, 16, 0), None)[0] is False


def test_checkpoint_due_rejects_the_close_boundary():
    """15:30 ET is OUT -- enforces 'no new entries in the last 30 min'.

    Sharper than the minute-level test above, and pinned because the gate moved
    from a truncating ``(hour, minute)`` tuple comparison to the shared
    ``mc.in_window("driver_entry", ...)``, which compares full ``time`` objects
    (seconds included). 15:29:59 must still be IN and 15:30:00 the first instant
    OUT -- if ``end_exclusive`` were ever dropped from config/sessions.toml, this
    fails rather than silently re-opening a checkpoint inside the no-entry zone.
    """
    inside = datetime(2026, 6, 24, 15, 29, 59, tzinfo=ET)
    boundary = datetime(2026, 6, 24, 15, 30, 0, tzinfo=ET)
    after = datetime(2026, 6, 24, 15, 30, 30, tzinfo=ET)
    assert scheduler.checkpoint_due(inside, None)[0] is True
    assert scheduler.checkpoint_due(boundary, None)[0] is False
    assert scheduler.checkpoint_due(after, None)[0] is False
    # The open boundary is INCLUSIVE, and likewise unaffected by seconds.
    assert scheduler.checkpoint_due(
        datetime(2026, 6, 24, 9, 44, 59, tzinfo=ET), None)[0] is False
    assert scheduler.checkpoint_due(
        datetime(2026, 6, 24, 9, 45, 0, tzinfo=ET), None)[0] is True


def test_checkpoint_due_last_entry_slot():
    """The 15:00–15:29 ET slot is the final fire-able entry slot (14:00 CT)."""
    assert scheduler.checkpoint_due(_et(2026, 6, 24, 15, 0), None)[0] is True
    assert scheduler.checkpoint_due(_et(2026, 6, 24, 15, 29), None)[0] is True


def test_checkpoint_not_due_on_weekend():
    # 2026-06-27 is a Saturday, 2026-06-28 a Sunday.
    assert scheduler.checkpoint_due(_et(2026, 6, 27, 11, 0), None)[0] is False
    assert scheduler.checkpoint_due(_et(2026, 6, 28, 11, 0), None)[0] is False


def test_checkpoint_not_due_on_market_holiday():
    """A weekday NYSE holiday must NOT fire a checkpoint (no Claude call on a day
    the market is closed) — 2026-07-03 (Fri, observed Independence Day) is a holiday
    mid-window, while 2026-07-02 (Thu, a trading day) at the same time IS due."""
    assert scheduler._is_trading_day(_et(2026, 7, 3, 11, 0)) is False
    assert scheduler.checkpoint_due(_et(2026, 7, 3, 11, 0), None)[0] is False
    # ...but the surrounding trading day at the same clock time is due.
    assert scheduler.checkpoint_due(_et(2026, 7, 2, 11, 0), None)[0] is True
    # the passed-in slot key survives a holiday poll.
    assert scheduler.checkpoint_due(_et(2026, 7, 3, 11, 0), "prev")[1] == "prev"


def test_checkpoint_slot_key_passthrough_when_not_due():
    """When not due, the passed-in slot key is returned unchanged."""
    assert scheduler.checkpoint_due(_et(2026, 6, 24, 8, 0), "prev")[1] == "prev"
    assert scheduler.checkpoint_due(_et(2026, 6, 27, 11, 0), "prev")[1] == "prev"


def test_checkpoint_due_next_day_new_slot_key():
    """A slot index repeats day-to-day, but the date-prefixed key differs → due."""
    _, ts_wed = scheduler.checkpoint_due(_et(2026, 6, 24, 10, 0), None)
    due_thu, ts_thu = scheduler.checkpoint_due(_et(2026, 6, 25, 10, 0), ts_wed)
    assert due_thu is True
    assert ts_thu != ts_wed


# ── should_rearm ─────────────────────────────────────────────────────────────


def test_should_rearm_clears_stale_halt():
    assert scheduler.should_rearm(
        {"halted": True, "halted_date": "2026-06-23"}, "2026-06-24") is True


def test_should_rearm_keeps_same_day_halt():
    assert scheduler.should_rearm(
        {"halted": True, "halted_date": "2026-06-24"}, "2026-06-24") is False


def test_should_rearm_noop_when_not_halted():
    assert scheduler.should_rearm({"halted": False}, "2026-06-24") is False


def test_should_rearm_missing_date_is_noop():
    """A halt with no recorded date can't be proven stale → don't re-arm."""
    assert scheduler.should_rearm({"halted": True, "halted_date": None}, "2026-06-24") is False
    assert scheduler.should_rearm({"halted": True}, "2026-06-24") is False


# ── Extended trading hours: no entries outside RTH (B10) ─────────────────────


def test_driver_takes_no_entries_in_extended_hours():
    """DELIBERATE: the 09:45-15:30 ET entry window excludes BOTH Cboe extended
    sessions -- GTH (07:30-09:25 ET) and Curb (16:00-16:15 ET).

    Observe-only posture; see section 7 of the 2026-08-02 extended-hours design
    doc. Widening this would let the decider open positions on thin ETH quotes.
    """
    # 2026-08-17 is the ETH activation date (a Monday, a trading day).
    # 07:45 ET -- inside Cboe's GTH session
    due, _ = scheduler.checkpoint_due(_et(2026, 8, 17, 7, 45), None)
    assert due is False
    # 16:05 ET -- inside Cboe's Curb session
    due, _ = scheduler.checkpoint_due(_et(2026, 8, 17, 16, 5), None)
    assert due is False
