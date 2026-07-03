from datetime import date, datetime

from shared import market_calendar as mc


def test_holidays_is_frozenset_of_dates():
    assert isinstance(mc.HOLIDAYS, frozenset)
    assert date(2026, 6, 19) in mc.HOLIDAYS
    assert date(2027, 12, 24) in mc.HOLIDAYS
    assert len(mc.HOLIDAYS) == 20


def test_is_holiday():
    assert mc.is_holiday(date(2026, 12, 25)) is True
    assert mc.is_holiday(date(2026, 12, 24)) is False


def test_is_trading_day_weekend_and_holiday():
    assert mc.is_trading_day(date(2026, 7, 6)) is True
    assert mc.is_trading_day(date(2026, 7, 4)) is False
    assert mc.is_trading_day(date(2026, 7, 3)) is False
    assert mc.is_trading_day(datetime(2026, 7, 6, 10, 0, tzinfo=mc.CT)) is True


def test_prev_and_next_trading_day_skip_weekend_holiday():
    assert mc.prev_trading_day(date(2026, 7, 6)) == date(2026, 7, 2)
    assert mc.next_trading_day(date(2026, 7, 2)) == date(2026, 7, 6)


def test_market_now_is_ct_aware():
    now = mc.market_now()
    assert now.tzinfo is not None


def test_canonical_holiday_membership_pins():
    from datetime import date
    # A few load-bearing dates — a tripwire if the set is ever edited by mistake.
    for d in [date(2026, 1, 1), date(2026, 6, 19), date(2026, 7, 3), date(2026, 12, 25),
              date(2027, 1, 1), date(2027, 6, 18), date(2027, 12, 24)]:
        assert d in mc.HOLIDAYS
    assert date(2026, 3, 26) not in mc.HOLIDAYS  # not a 2026 holiday
