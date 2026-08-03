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


def test_derivation_matches_the_dates_every_live_site_uses():
    """The 8 duplicated holiday sets across the services, webgui and scanner all
    contain EXACTLY these 20 dates (verified by the Task A1 gate). The algorithmic
    derivation must reproduce them exactly -- this is what keeps the Phase A/B
    migration behavior-preserving. If this fails, the derivation is wrong, not
    this list."""
    expected = {
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
        date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
        date(2026, 11, 26), date(2026, 12, 25),
        date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
        date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
        date(2027, 11, 25), date(2027, 12, 24),
    }
    assert mc.nyse_holidays(2026) | mc.nyse_holidays(2027) == expected


def test_no_holiday_falls_on_a_weekend():
    """Catches a broken _observed shift -- the NYSE is already closed on
    weekends, so an observed holiday must always land on a weekday."""
    for year in range(2022, 2041):
        for d in mc.nyse_holidays(year):
            assert d.weekday() < 5, f"{d} ({year} set) falls on a weekend"


def test_derivation_works_for_a_year_outside_2026_2027():
    """Proves the yearly maintenance edit is genuinely gone."""
    h2028 = mc.nyse_holidays(2028)
    assert 9 <= len(h2028) <= 10
    assert date(2028, 6, 19) in h2028      # Juneteenth (Monday)
    assert date(2028, 7, 4) in h2028       # Independence Day (Tuesday)
    assert date(2028, 12, 25) in h2028     # Christmas (Monday)
    assert date(2028, 4, 14) in h2028      # Good Friday
    assert date(2028, 11, 23) in h2028     # Thanksgiving


def test_juneteenth_absent_before_2022():
    """The derivation encodes today's rules; Juneteenth is the one historical
    change it models. Everything else pre-2022 is out of the documented
    validity range."""
    assert date(2021, 6, 18) not in mc.nyse_holidays(2021)
    assert date(2022, 6, 20) in mc.nyse_holidays(2022)
