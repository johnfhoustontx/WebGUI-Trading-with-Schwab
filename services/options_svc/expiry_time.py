"""Time to an option's expiry: one settlement instant, real elapsed time.

Options stop trading at the 16:00 ET close. Everything in options_svc that needs
"how long until this expires" comes here, so there is one answer (see
CLAUDE.md, "never compute a time-to-expiry inline"). The engine-side twin is
``options_calculator.expiry_time_to_years``; a test holds the two equal.

Moved out of ``compute.py`` on 2026-10-04 (audit CQ-03), unchanged apart from
``_year_fraction`` subtracting in UTC (audit AC-06). ``compute`` re-exports
every name here.
"""
# ── intraday time-to-expiry (the 0DTE fix) ───────────────────────────────────
# Options stop trading at the 4:00pm ET close (QQQ/SPY/equities and PM-settled
# 0DTE index weeklys). Time-to-expiry is the CALENDAR span from now to that close
# in years (/365 — the same convention bs_price/calc_summary already use, and the
# one ThinkorSwim implies IV under). Using calendar ``.days`` instead collapses to
# 0 on expiration day (intrinsic-only) or a bogus full day — the calculator bug.
from zoneinfo import ZoneInfo as _ZoneInfo

_MARKET_TZ = _ZoneInfo("America/New_York")
_EXPIRY_CLOSE_HOUR = 16  # 4:00pm ET
_YEAR_SECONDS = 365.0 * 24.0 * 3600.0


def _expiry_settlement(expiry_date):
    """The 4:00pm ET settlement datetime (tz-aware) for an expiry date."""
    import datetime as dt

    return dt.datetime(expiry_date.year, expiry_date.month, expiry_date.day,
                       _EXPIRY_CLOSE_HOUR, 0, 0, tzinfo=_MARKET_TZ)


def _year_fraction(start_dt, end_dt):
    """Calendar years of REAL elapsed time from start to end (never negative),
    /365.

    Both are moved to UTC first when they are timezone-aware. Subtracting two
    datetimes that carry the same timezone object is a wall-clock subtraction
    in Python, so a daylight-saving change between them was ignored and the
    answer was an hour off (audit AC-06)."""
    import datetime as dt

    if start_dt.tzinfo is not None and end_dt.tzinfo is not None:
        start_dt = start_dt.astimezone(dt.timezone.utc)
        end_dt = end_dt.astimezone(dt.timezone.utc)
    return max((end_dt - start_dt).total_seconds(), 0.0) / _YEAR_SECONDS


def time_to_expiry_years(now_dt, expiry_date):
    """Years from ``now_dt`` (tz-aware) to the expiry's 4:00pm ET close, /365.

    Sub-day resolution: 3 hours before the close on expiry day → ~3/24/365, not 0;
    after the close → 0. Multi-day → calendar days + today's fraction."""
    return _year_fraction(now_dt, _expiry_settlement(expiry_date))


_SIM_MIN_DAYS = 0.01   # sweep-stability floor (~14 min), not a time convention


def _leg_days_to_expiry(expiry, elapsed=0.0, now=None):
    """DAYS to ``expiry``'s 16:00 ET close after ``elapsed`` days from ``now``.

    Fractional and intraday-aware: a 0-DTE leg at 11:00 ET returns 5/24, not the
    ``_SIM_MIN_DAYS`` floor. The What-if sweep previously used whole-day
    ``(exp - today).days``, which pinned EVERY 0-DTE leg (and its P/L baseline)
    at 0.01 days regardless of the hours actually left -- a 4.6x understatement
    at five hours to the close, and inconsistent with the Replay and IV-shock
    engines on the same page (fixed 2026-08-20).

    Tolerates a string ``expiry`` (test doubles use strings); an unparseable one
    degrades to the elapsed-only floor, as before.
    """
    import datetime as dt

    if isinstance(expiry, str):
        try:
            expiry = dt.date.fromisoformat(expiry[:10])
        except ValueError:
            return max(float(elapsed), _SIM_MIN_DAYS)
    now = now or dt.datetime.now(_MARKET_TZ)
    days_now = time_to_expiry_years(now, expiry) * 365.0
    return max(days_now - float(elapsed), _SIM_MIN_DAYS)
