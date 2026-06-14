"""NYSE trading-day calendar (lightweight, no external deps).

Covers full-day closures only — early-close sessions (e.g. day after
Thanksgiving) are still trading days for the purposes of daily-close
scoring, so they're treated as open here.

Public surface:
    is_trading_day(d)   -> bool
    prev_trading_day(d) -> date  (d itself if it's a trading day,
                                  else walk back)
    next_trading_day(d) -> date
    nyse_holidays(year) -> set[date]
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm — Easter Sunday for `year`."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31
    day = ((h + L - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """`n`th occurrence of `weekday` (Mon=0..Sun=6) in (year, month)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last occurrence of `weekday` in (year, month)."""
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    d = next_first - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _observed(d: date) -> date:
    """NYSE observance rule: Sat → Fri prior; Sun → Mon following."""
    if d.weekday() == 5:    # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:    # Sunday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def nyse_holidays(year: int) -> frozenset:
    """Full-day NYSE closures for `year`."""
    holidays = {
        _observed(date(year, 1, 1)),                  # New Year's Day
        _nth_weekday(year, 1, 0, 3),                  # MLK Day (3rd Mon Jan)
        _nth_weekday(year, 2, 0, 3),                  # Presidents Day
        _easter(year) - timedelta(days=2),            # Good Friday
        _last_weekday(year, 5, 0),                    # Memorial Day
        _observed(date(year, 6, 19)),                 # Juneteenth (from 2022)
        _observed(date(year, 7, 4)),                  # Independence Day
        _nth_weekday(year, 9, 0, 1),                  # Labor Day
        _nth_weekday(year, 11, 3, 4),                 # Thanksgiving
        _observed(date(year, 12, 25)),                # Christmas
    }
    # Juneteenth didn't become a federal holiday until 2021 (NYSE 2022).
    if year < 2022:
        holidays.discard(_observed(date(year, 6, 19)))
    return frozenset(holidays)


def is_trading_day(d: date) -> bool:
    """True iff `d` is a weekday and not an NYSE full-day holiday."""
    if d.weekday() >= 5:
        return False
    return d not in nyse_holidays(d.year)


def prev_trading_day(d: date) -> date:
    """Largest trading day <= d."""
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_trading_day(d: date) -> date:
    """Smallest trading day >= d."""
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d
