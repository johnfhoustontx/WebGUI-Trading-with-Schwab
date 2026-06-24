"""Tests for the options service scheduler's GEX-collection cadence.

``gex_due`` mirrors ``autoscan_due`` but on the gex_collector cadence/window
(2-min slots within 08:30–15:20 CT on trading days) so intraday history is
written whenever this always-on service is up — replacing the fragile standalone
gex_collector.py window. Pure schedule logic, tested with fixed CT datetimes.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import scheduler

_CT = ZoneInfo("America/Chicago")


def _ct(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=_CT)


# 2026-06-15 is a Monday (a normal trading day).
def test_gex_due_first_tick_in_window():
    due, slot = scheduler.gex_due(_ct(2026, 6, 15, 8, 40), None)
    assert due is True and slot is not None


def test_gex_due_not_repeated_within_same_2min_slot():
    due, slot = scheduler.gex_due(_ct(2026, 6, 15, 8, 40), None)
    due2, slot2 = scheduler.gex_due(_ct(2026, 6, 15, 8, 41), slot)
    assert due2 is False and slot2 == slot   # 08:40–08:41 is one slot


def test_gex_due_fires_on_next_2min_slot():
    _, slot = scheduler.gex_due(_ct(2026, 6, 15, 8, 42), None)
    due, slot2 = scheduler.gex_due(_ct(2026, 6, 15, 8, 44), slot)
    assert due is True and slot2 != slot


def test_gex_due_before_window():
    due, _ = scheduler.gex_due(_ct(2026, 6, 15, 8, 15), None)
    assert due is False


def test_gex_due_after_window():
    due, _ = scheduler.gex_due(_ct(2026, 6, 15, 15, 25), None)
    assert due is False


def test_gex_due_weekend():
    # 2026-06-13 is a Saturday.
    due, _ = scheduler.gex_due(_ct(2026, 6, 13, 9, 0), None)
    assert due is False


def test_gex_due_holiday():
    # 2026-07-03 is in scheduler._HOLIDAYS.
    due, _ = scheduler.gex_due(_ct(2026, 7, 3, 9, 0), None)
    assert due is False


# ── manage_due (paper auto-manage cadence) ──────────────────────────────────
def test_manage_due_first_tick_in_window():
    due, slot = scheduler.manage_due(_ct(2026, 6, 15, 9, 0), None)
    assert due is True and slot is not None


def test_manage_due_not_repeated_within_same_5min_slot():
    _, slot = scheduler.manage_due(_ct(2026, 6, 15, 9, 0), None)
    due2, slot2 = scheduler.manage_due(_ct(2026, 6, 15, 9, 3), slot)
    assert due2 is False and slot2 == slot


def test_manage_due_fires_on_next_5min_slot():
    _, slot = scheduler.manage_due(_ct(2026, 6, 15, 9, 2), None)
    due, slot2 = scheduler.manage_due(_ct(2026, 6, 15, 9, 6), slot)
    assert due is True and slot2 != slot


def test_manage_due_before_market_open():
    due, _ = scheduler.manage_due(_ct(2026, 6, 15, 7, 30), None)
    assert due is False


def test_manage_due_after_market_close():
    due, _ = scheduler.manage_due(_ct(2026, 6, 15, 15, 30), None)
    assert due is False


def test_manage_due_weekend():
    # 2026-06-13 is a Saturday.
    due, _ = scheduler.manage_due(_ct(2026, 6, 13, 9, 0), None)
    assert due is False


def test_manage_due_holiday():
    due, _ = scheduler.manage_due(_ct(2026, 7, 3, 9, 0), None)
    assert due is False


# ── periodic_refresh_due (header + gex_status per-tick gating) ───────────────
# During market hours the header/status refresh every tick; off-hours/weekends
# they throttle to a longer interval so the service stops making proxy + SQLite +
# Redis calls every 30s, 24/7, with no browser open.
def test_periodic_refresh_due_every_tick_in_market_hours():
    # Same last_slot on consecutive market-hours ticks -> always due.
    due1, slot1 = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 10, 0), "x")
    due2, slot2 = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 10, 0), slot1)
    assert due1 is True and due2 is True


def test_periodic_refresh_due_offhours_first_tick():
    due, slot = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 20, 0), None)
    assert due is True and slot is not None


def test_periodic_refresh_due_offhours_throttled_within_slot():
    _, slot = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 20, 0), None)
    due2, slot2 = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 20, 2), slot)
    assert due2 is False and slot2 == slot  # 20:00-20:02 share one 5-min slot


def test_periodic_refresh_due_offhours_fires_next_slot():
    _, slot = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 20, 2), None)
    due, slot2 = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 20, 6), slot)
    assert due is True and slot2 != slot


def test_periodic_refresh_due_weekend_throttled():
    # Saturday 10:00 — not a trading day, so the off-hours throttle applies even
    # though the clock time is within the market-hours window.
    _, slot = scheduler.periodic_refresh_due(_ct(2026, 6, 13, 10, 0), None)
    due2, slot2 = scheduler.periodic_refresh_due(_ct(2026, 6, 13, 10, 1), slot)
    assert due2 is False and slot2 == slot


# ── Gamma display persistence (active session date + cleared window) ─────────
# Reference weekdays: 2026-06-22 Mon, 23 Tue, 24 Wed, 25 Thu, 26 Fri, 27 Sat, 28 Sun.
def test_active_session_date_is_today_on_a_trading_day():
    assert scheduler.active_session_date(_ct(2026, 6, 23, 16, 0)).isoformat() == "2026-06-23"


def test_active_session_date_holds_friday_through_weekend():
    assert scheduler.active_session_date(_ct(2026, 6, 27, 10, 0)).isoformat() == "2026-06-26"  # Sat
    assert scheduler.active_session_date(_ct(2026, 6, 28, 23, 0)).isoformat() == "2026-06-26"  # Sun


def test_active_session_date_holds_prior_day_on_holiday():
    # 2026-01-19 (MLK) is a Monday holiday → most recent trading day is Fri 2026-01-16.
    assert scheduler.active_session_date(_ct(2026, 1, 19, 12, 0)).isoformat() == "2026-01-16"


def test_gamma_cleared_overnight_on_a_trading_day():
    assert scheduler.gamma_cleared(_ct(2026, 6, 23, 0, 0)) is True    # Tue midnight
    assert scheduler.gamma_cleared(_ct(2026, 6, 23, 7, 0)) is True    # Tue pre-session
    assert scheduler.gamma_cleared(_ct(2026, 6, 23, 10, 0)) is False  # Tue live session
    assert scheduler.gamma_cleared(_ct(2026, 6, 23, 23, 0)) is False  # Tue after close → persist


def test_gamma_not_cleared_on_non_trading_days():
    assert scheduler.gamma_cleared(_ct(2026, 6, 27, 2, 0)) is False   # Sat → persist Friday
    assert scheduler.gamma_cleared(_ct(2026, 1, 19, 2, 0)) is False   # MLK Mon → persist


# ── Cadence-mirror drift guard ──────────────────────────────────────────────
# The GEX-collection cadence is intentionally mirrored (not imported) between the
# standalone collector and this Tier-2 scheduler to keep the scheduler's import
# light (see scheduler.py's _GEX_* comment). Mirrors drift silently, so assert the
# two interval constants — and the staleness threshold — stay in lockstep. This is
# the only safety net the half-edit hazard has.
def test_gex_interval_mirrors_collector():
    import gex_collector
    import gex_status

    assert scheduler._GEX_INTERVAL_MIN == gex_collector.POLL_INTERVAL_MIN
    # Staleness threshold == 2 poll intervals (in seconds).
    assert gex_status.STALE_AFTER_SEC == gex_collector.POLL_INTERVAL_MIN * 60 * 2
