"""Time to expiry is real elapsed time, across a daylight-saving change too.

Audit AC-06. Subtracting two datetimes that carry the SAME timezone object is a
wall-clock subtraction in Python: the shift between them is ignored. Both
helpers built the settlement in America/New_York, so a reference time also in
New York came out one hour off whenever a clock change lay between now and the
expiry. The Calculator's expiry column then priced an hour of time value that
was not there: -$287.19 against a true -$300.00.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import options_calculator as oc
from services.options_svc import compute

ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")
UTC = dt.timezone.utc
HOUR = 1.0 / (365.0 * 24.0)

# Clocks go BACK on 2026-11-01 and FORWARD on 2027-03-14 (US rules).
FALL_NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=ET)        # Friday noon EDT
FALL_EXPIRY = dt.date(2026, 11, 6)                            # the next Friday, EST
SPRING_NOW = dt.datetime(2027, 3, 12, 12, 0, tzinfo=ET)
SPRING_EXPIRY = dt.date(2027, 3, 19)


def test_a_week_across_the_autumn_change_is_one_hour_longer():
    """7 days and 4 hours on the clock; 173 hours of real time."""
    assert compute.time_to_expiry_years(FALL_NOW, FALL_EXPIRY) == pytest.approx(173 * HOUR)
    assert oc.expiry_time_to_years(FALL_NOW, FALL_EXPIRY) == pytest.approx(173 * HOUR)


def test_a_week_across_the_spring_change_is_one_hour_shorter():
    assert compute.time_to_expiry_years(SPRING_NOW, SPRING_EXPIRY) == pytest.approx(171 * HOUR)
    assert oc.expiry_time_to_years(SPRING_NOW, SPRING_EXPIRY) == pytest.approx(171 * HOUR)


@pytest.mark.parametrize("now,expiry", [(FALL_NOW, FALL_EXPIRY), (SPRING_NOW, SPRING_EXPIRY)])
def test_the_answer_does_not_depend_on_which_zone_the_instant_is_written_in(now, expiry):
    """One instant, four spellings, one T - in both helpers."""
    spellings = [now, now.astimezone(CT), now.astimezone(UTC),
                 now.astimezone(CT).replace(tzinfo=None)]      # naive = Central here
    svc = [compute.time_to_expiry_years(s, expiry) for s in spellings[:3]]
    calc = [oc.expiry_time_to_years(s, expiry) for s in spellings]
    for value in svc + calc:
        assert value == pytest.approx(svc[0], abs=1e-12)


def test_no_clock_change_in_between_is_unchanged():
    now = dt.datetime(2026, 10, 5, 12, 0, tzinfo=ET)
    assert compute.time_to_expiry_years(now, dt.date(2026, 10, 9)) == pytest.approx(100 * HOUR)
    assert oc.expiry_time_to_years(now, dt.date(2026, 10, 9)) == pytest.approx(100 * HOUR)


def test_the_two_helpers_agree_at_the_close_on_expiry_day():
    at_close = dt.datetime(2026, 11, 6, 16, 0, tzinfo=ET)
    assert compute.time_to_expiry_years(at_close, FALL_EXPIRY) == 0.0
    assert oc.expiry_time_to_years(at_close, FALL_EXPIRY) == 0.0
