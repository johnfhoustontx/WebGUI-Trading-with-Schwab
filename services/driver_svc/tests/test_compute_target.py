"""Tests for the cumulative MTD banking target helpers in ``driver_svc.compute``.

``effective_target`` carries the $500/day deficit/excess forward month-to-date, clamped
to [floor, cap]; ``mtd_realized_before_today`` sums the driver book's realized P&L this
month excluding today; ``_mtd_trading_days`` counts weekdays−holidays MTD. All pure.
"""
import datetime as dt

from services.driver_svc import compute


# ── effective_target ─────────────────────────────────────────────────────────
def test_effective_target_on_pace_is_base():
    # day 1, nothing banked yet -> base 500
    assert compute.effective_target(500, 1, 0, cap=1000, floor=250) == 500


def test_effective_target_behind_ratchets_to_cap():
    # day 5, only $1000 banked of the $2500 pace -> need 1500 today, capped 1000
    assert compute.effective_target(500, 5, 1000, cap=1000, floor=250) == 1000


def test_effective_target_ahead_eases_to_floor():
    # day 5, $3000 banked (ahead of $2500 pace) -> raw negative -> floored 250
    assert compute.effective_target(500, 5, 3000, cap=1000, floor=250) == 250


def test_effective_target_mid_range():
    # day 3, $700 banked of $1500 pace -> need 800 today (within band)
    assert compute.effective_target(500, 3, 700, cap=1000, floor=250) == 800


def test_effective_target_defensive_on_junk():
    assert compute.effective_target(500, None, None, cap=1000, floor=250) == 500
    assert compute.effective_target(500, "x", 0, cap=1000, floor=250) == 500


# ── mtd_realized_before_today + _mtd_trading_days ────────────────────────────
def _cp(pnl, exit_ts):
    return {"realized_pnl": pnl, "exit_ts": exit_ts}


def test_mtd_realized_sums_this_month_before_today():
    today = dt.date(2026, 7, 9)
    closed = [_cp(100, "2026-07-01T15:00:00-05:00"),   # this month, before today -> counts
              _cp(-40, "2026-07-08T13:00:00-05:00"),   # counts
              _cp(999, "2026-07-09T10:00:00-05:00"),   # TODAY -> excluded
              _cp(500, "2026-06-30T13:00:00-05:00"),   # last month -> excluded
              None, {}, {"realized_pnl": "x", "exit_ts": "2026-07-02T10:00:00-05:00"}]
    assert compute.mtd_realized_before_today(closed, today) == 60.0   # 100 - 40


def test_mtd_realized_empty_is_zero():
    for c in ([], None, "junk"):
        assert compute.mtd_realized_before_today(c, dt.date(2026, 7, 9)) == 0.0


def test_mtd_trading_days_counts_weekdays_minus_holidays():
    # Jul 2026: 1st is Wed. Weekdays through Thu Jul 9 = {1,2,3,6,7,8,9}; Jul 3 is the
    # observed Independence Day holiday -> 6 trading days.
    assert compute._mtd_trading_days(dt.date(2026, 7, 9)) == 6


def test_mtd_trading_days_excludes_holidays_past_2027():
    """Regression: this borrowed the scheduler's ``_HOLIDAYS`` alias, a bounded
    2026-27 set, so from 2028 every holiday counted as a trading day and
    inflated the cumulative MTD target's denominator. Jan 2028: weekdays through
    Mon Jan 31 = 21, minus New Year's observed Fri 2027-12-31 (not in January)
    and MLK Mon Jan 17 -> 20."""
    assert compute._mtd_trading_days(dt.date(2028, 1, 31)) == 20


def test_mtd_trading_days_first_of_month():
    # Jul 1 2026 (Wed) is a single trading day.
    assert compute._mtd_trading_days(dt.date(2026, 7, 1)) == 1
