from datetime import date

import repo_paths
from shared import market_calendar as mc


def test_sessions_toml_path_declared():
    assert repo_paths.SESSIONS_TOML.name == "sessions.toml"
    assert repo_paths.SESSIONS_TOML.parent.name == "config"


def test_holidays_is_frozenset_of_20_dates():
    assert isinstance(mc.HOLIDAYS, frozenset)
    assert len(mc.HOLIDAYS) == 20            # 10 per year, 2026 + 2027
    assert date(2026, 6, 19) in mc.HOLIDAYS   # Juneteenth
    assert date(2027, 12, 24) in mc.HOLIDAYS  # Christmas observed 2027


def test_is_holiday():
    assert mc.is_holiday(date(2026, 12, 25)) is True
    assert mc.is_holiday(date(2026, 12, 24)) is False


def test_is_trading_day_excludes_weekends_and_holidays():
    assert mc.is_trading_day(date(2026, 7, 6)) is True    # Monday
    assert mc.is_trading_day(date(2026, 7, 4)) is False   # Saturday
    assert mc.is_trading_day(date(2026, 7, 5)) is False   # Sunday
    assert mc.is_trading_day(date(2026, 7, 3)) is False   # holiday (observed)


def test_prev_trading_day_skips_weekend_and_holiday():
    # Monday 2026-07-06 -> Thursday 2026-07-02 (Fri 7/3 is the observed holiday)
    assert mc.prev_trading_day(date(2026, 7, 6)) == date(2026, 7, 2)


def test_next_trading_day_skips_weekend_and_holiday():
    assert mc.next_trading_day(date(2026, 7, 2)) == date(2026, 7, 6)


def test_prev_trading_day_is_strictly_before():
    d = date(2026, 7, 7)                       # a plain Tuesday
    assert mc.prev_trading_day(d) < d


def test_next_trading_day_is_strictly_after():
    d = date(2026, 7, 7)
    assert mc.next_trading_day(d) > d
