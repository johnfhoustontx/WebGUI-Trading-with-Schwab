"""Single source of truth for the market calendar and session windows.

**Holidays are consolidated here (Phase A, done).** The eight duplicated holiday
sets across the five Tier-2 services, the webgui and ``options-scanner/scanner.py``
now read from this module. Two sites remain outside it, both deliberately:
``claude-driver/config.py`` is exempt (legacy; its consumers were removed
2026-07-08), and ``options-scanner/scanner_engine.py`` still holds a DIVERGENT
9-date 2026-only set — a tracked follow-up, since correcting it is a behavior
change rather than a refactor.

**Session windows are NOT here yet.** The fourteen hardcoded window constants
still live in their own modules; migrating them is Phase B.

What ships today is the holiday calendar and the trading-day helpers below.
Session windows, the ``Session`` vocabulary and ``config/sessions.toml``
loading (mtime-cached, mirroring
``services/options_svc/flow_alerts.load_thresholds``) arrive in Phase B.

Everything here is a PURE function of its arguments -- no network, no
database, no clock.

``shared/`` is a namespace package (no ``__init__.py``), so
``from shared.market_calendar import ...`` resolves once the repo root is on
``sys.path`` -- which the services and webgui already arrange. The one caller
needing a bootstrap is ``options-scanner/scanner.py`` (legacy standalone CLI);
use the three-line pattern from ``services/options_svc/commission.py``.

The holiday set is **derived algorithmically** (see ``nyse_holidays``), so
there is no yearly maintenance edit. This module absorbed the former
``sentiment-dashboard/market_calendar.py``, whose derivation it carries over
verbatim; see the note on ``prev_trading_day`` for the one semantic
difference between the two.
"""
import logging
from datetime import date, timedelta
from functools import lru_cache

log = logging.getLogger(__name__)

_MAX_SPAN_DAYS = 10  # longest plausible market closure; bounds the search loops


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm -- Easter Sunday for ``year``."""
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
    """``n``th occurrence of ``weekday`` (Mon=0..Sun=6) in (year, month)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last occurrence of ``weekday`` in (year, month)."""
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    d = next_first - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _observed(d: date) -> date:
    """NYSE observance rule: Sat -> prior Fri; Sun -> following Mon."""
    if d.weekday() == 5:    # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:    # Sunday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def nyse_holidays(year: int) -> frozenset:
    """Full-day NYSE closures for ``year``, derived from today's NYSE rules.

    Memoized -- ``is_trading_day`` runs on scheduler ticks and must stay cheap.

    **Validity: 2022 onward.** The rule set encoded here is the *current* one,
    so historical years are wrong: Juneteenth only became an NYSE holiday in
    2022 (guarded below), and earlier rule changes are not modelled at all.

    **Ad-hoc closures are NOT derivable and are NOT included** -- national days
    of mourning (e.g. Presidents Ford/Bush/Carter), 9/11, and weather closures
    such as Hurricane Sandy. If one of those matters, it must be handled by the
    caller.

    Note the year-boundary spill: when 1 Jan falls on a Saturday, the observed
    holiday is 31 Dec of the *previous* year, so ``nyse_holidays(y)`` can
    contain a date in ``y - 1``. See ``is_holiday`` for how that is handled.
    """
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


# The 2026-2027 union, kept as a name because the Phase B migrations bind it as
# their local ``_HOLIDAYS`` alias. Derived, not a literal -- but the pinned test
# in shared/tests/test_market_calendar.py asserts it still equals the exact 20
# dates every live site contains, which is what keeps that migration
# behavior-preserving.
HOLIDAYS = nyse_holidays(2026) | nyse_holidays(2027)


def is_holiday(d) -> bool:
    """True if ``d`` (a ``date``) is an NYSE full-closure holiday.

    Derived per-year, so any year works -- there is no set to update.

    Known limitation, deliberately matching the eight live holiday sets this
    module replaces: the lookup consults ``nyse_holidays(d.year)`` only, so the
    New-Year's-observed spill into 31 Dec of the prior year (when 1 Jan is a
    Saturday, next on 2027-12-31) reads as a trading day. Changing that would
    diverge from the sets currently in production, so it is left alone here.
    """
    return d in nyse_holidays(d.year)


def is_trading_day(d) -> bool:
    """True if ``d`` is a weekday that is not an NYSE full-closure holiday."""
    return d.weekday() < 5 and not is_holiday(d)


def prev_trading_day(d):
    """Most recent trading day STRICTLY before ``d``.

    **Exclusive.** ``sentiment-dashboard/market_calendar.py`` (absorbed into
    this module) exported a same-named *inclusive* variant -- "largest trading
    day <= d", returning ``d`` itself when ``d`` was a trading day. The
    exclusive form was kept because ``services/options_svc/scheduler.py``'s
    ``_prev_trading_day`` is exclusive and is the load-bearing caller.

    The difference is invisible at a call site -- both return a date and
    neither raises -- so do not "simplify" one into the other.
    """
    for _ in range(_MAX_SPAN_DAYS):
        d = d - timedelta(days=1)
        if is_trading_day(d):
            return d
    raise ValueError(f"no trading day within {_MAX_SPAN_DAYS} days before {d}")


def next_trading_day(d):
    """Next trading day STRICTLY after ``d``.

    **Exclusive** -- see the note on ``prev_trading_day``. The absorbed
    ``sentiment-dashboard`` module's variant was inclusive ("smallest trading
    day >= d"), and the difference is invisible at a call site.
    """
    for _ in range(_MAX_SPAN_DAYS):
        d = d + timedelta(days=1)
        if is_trading_day(d):
            return d
    raise ValueError(f"no trading day within {_MAX_SPAN_DAYS} days after {d}")
