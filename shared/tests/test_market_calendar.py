import datetime as dt
from datetime import date
from zoneinfo import ZoneInfo

import pytest

import repo_paths
from shared import market_calendar as mc

CT = ZoneInfo("America/Chicago")


def _ct(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=CT)


@pytest.fixture(autouse=True)
def _clean_config_cache():
    """Every test starts from the real sessions.toml, not a neighbour's stub."""
    mc.reset_config_cache()
    yield
    mc.reset_config_cache()


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


# -- activation gate --------------------------------------------------------
def test_extended_hours_inactive_before_activation():
    assert mc.extended_hours_active(dt.date(2026, 8, 14)) is False   # Friday
    assert mc.extended_hours_active(dt.date(2026, 8, 16)) is False   # Sunday


def test_extended_hours_active_on_and_after_activation():
    assert mc.extended_hours_active(dt.date(2026, 8, 17)) is True    # Monday
    assert mc.extended_hours_active(dt.date(2026, 9, 1)) is True


# -- sessions BEFORE activation: ETH windows must read CLOSED ---------------
def test_gth_is_closed_before_activation():
    assert mc.session_at(_ct(2026, 8, 14, 7, 0)) is mc.Session.CLOSED


def test_curb_is_closed_before_activation():
    assert mc.session_at(_ct(2026, 8, 14, 15, 5)) is mc.Session.CLOSED


def test_regular_session_unaffected_by_activation():
    assert mc.session_at(_ct(2026, 8, 14, 10, 0)) is mc.Session.REGULAR


# -- sessions ON/AFTER activation -------------------------------------------
def test_gth_session_after_activation():
    assert mc.session_at(_ct(2026, 8, 17, 7, 0)) is mc.Session.GTH


def test_curb_session_after_activation():
    assert mc.session_at(_ct(2026, 8, 17, 15, 5)) is mc.Session.CURB


# -- boundaries -------------------------------------------------------------
def test_session_boundaries_after_activation():
    d = (2026, 8, 17)
    assert mc.session_at(_ct(*d, 6, 29)) is mc.Session.CLOSED
    assert mc.session_at(_ct(*d, 6, 30)) is mc.Session.GTH
    assert mc.session_at(_ct(*d, 8, 25)) is mc.Session.GTH
    assert mc.session_at(_ct(*d, 8, 26)) is mc.Session.CLOSED   # 5-min gap
    assert mc.session_at(_ct(*d, 8, 30)) is mc.Session.REGULAR
    assert mc.session_at(_ct(*d, 15, 0)) is mc.Session.REGULAR  # regular wins overlap
    assert mc.session_at(_ct(*d, 15, 1)) is mc.Session.CURB
    assert mc.session_at(_ct(*d, 15, 15)) is mc.Session.CURB
    assert mc.session_at(_ct(*d, 15, 16)) is mc.Session.CLOSED


def test_non_trading_day_is_always_closed():
    assert mc.session_at(_ct(2026, 8, 22, 10, 0)) is mc.Session.CLOSED  # Saturday
    assert mc.session_at(_ct(2026, 9, 7, 10, 0)) is mc.Session.CLOSED   # Labor Day


def test_is_regular_hours_matches_session_at():
    assert mc.is_regular_hours(_ct(2026, 8, 17, 10, 0)) is True
    assert mc.is_regular_hours(_ct(2026, 8, 17, 7, 0)) is False


def test_is_extended_hours_only_after_activation():
    assert mc.is_extended_hours(_ct(2026, 8, 17, 7, 0)) is True
    assert mc.is_extended_hours(_ct(2026, 8, 14, 7, 0)) is False
    assert mc.is_extended_hours(_ct(2026, 8, 17, 10, 0)) is False


def test_naive_datetime_is_treated_as_ct():
    naive = dt.datetime(2026, 8, 17, 10, 0)
    assert mc.session_at(naive) is mc.Session.REGULAR


def test_aware_non_ct_datetime_is_converted():
    """17:00 UTC on a summer weekday == 12:00 CT -> regular hours."""
    utc_noon_ct = dt.datetime(2026, 8, 17, 17, 0, tzinfo=dt.timezone.utc)
    assert mc.session_at(utc_noon_ct) is mc.Session.REGULAR


# -- config loading ---------------------------------------------------------
def test_load_config_reads_the_real_sessions_toml():
    cfg = mc.load_config()
    assert cfg["activation"]["extended_hours_from"] == "2026-08-17"
    assert cfg["sessions"]["gth"]["start"] == "06:30"
    assert cfg["windows"]["collection"]["eth_start"] == "06:30"


def test_alerts_fire_in_extended_hours_defaults_off():
    assert mc.alerts_fire_in_extended_hours() is False


def test_missing_sessions_toml_falls_back_to_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "_TOML_PATH", tmp_path / "nope.toml")
    mc.reset_config_cache()
    assert mc.load_config() == mc._DEFAULTS
    assert mc.activation_date() == dt.date(2026, 8, 17)
    assert mc.session_at(_ct(2026, 8, 17, 7, 0)) is mc.Session.GTH


def test_corrupt_sessions_toml_falls_back_to_defaults(monkeypatch, tmp_path):
    bad = tmp_path / "sessions.toml"
    bad.write_text("this is not [ valid toml", encoding="utf-8")
    monkeypatch.setattr(mc, "_TOML_PATH", bad)
    mc.reset_config_cache()
    assert mc.load_config() == mc._DEFAULTS
    assert mc.session_at(_ct(2026, 8, 17, 10, 0)) is mc.Session.REGULAR


def test_malformed_time_value_falls_back_to_the_default_time(monkeypatch, tmp_path):
    bad = tmp_path / "sessions.toml"
    bad.write_text('[sessions.gth]\nstart = "half past six"\n', encoding="utf-8")
    monkeypatch.setattr(mc, "_TOML_PATH", bad)
    mc.reset_config_cache()
    # The bad start silently reverts to 06:30 rather than raising.
    assert mc.session_at(_ct(2026, 8, 17, 6, 30)) is mc.Session.GTH


def test_malformed_activation_date_falls_back(monkeypatch, tmp_path):
    bad = tmp_path / "sessions.toml"
    bad.write_text('[activation]\nextended_hours_from = "soon"\n', encoding="utf-8")
    monkeypatch.setattr(mc, "_TOML_PATH", bad)
    mc.reset_config_cache()
    assert mc.activation_date() == dt.date(2026, 8, 17)


def _write_cfg(monkeypatch, tmp_path, body):
    bad = tmp_path / "sessions.toml"
    bad.write_text(body, encoding="utf-8")
    monkeypatch.setattr(mc, "_TOML_PATH", bad)
    mc.reset_config_cache()


@pytest.mark.parametrize("body", [
    'sessions = "oops"',
    'windows = "oops"',
    '[windows]\nscan = "oops"',          # nested: bad value one level down
    '[sessions]\ngth = "oops"',          # nested
    'activation = "soon"',
    'alerts = "yes"',
])
def test_malformed_shape_degrades_to_defaults(monkeypatch, tmp_path, body):
    """A SCALAR where a table belongs is a different failure class from a bad
    value: _merge would store it verbatim and it would then blow up on a
    scheduler tick, not at load time. Every accessor must survive it."""
    _write_cfg(monkeypatch, tmp_path, body)
    now = _ct(2026, 8, 17, 10, 0)
    assert mc.load_config() == mc._DEFAULTS
    assert mc.session_at(now) is mc.Session.REGULAR
    assert mc.in_window("scan", now) is True
    assert mc.in_collection_window(now) is True
    assert mc.session_flip_time() == dt.time(8, 0)
    assert mc.window_bounds("scan") == (dt.time(8, 0), dt.time(15, 15))
    assert mc.activation_date() == dt.date(2026, 8, 17)
    assert mc.alerts_fire_in_extended_hours() is False


def test_malformed_shape_does_not_disturb_a_sibling_key(monkeypatch, tmp_path):
    """Rejecting one bad table must not discard the valid keys beside it."""
    _write_cfg(monkeypatch, tmp_path,
               '[windows]\nscan = "oops"\n\n[windows.collection]\nstop = "15:30"\n')
    assert mc.window_bounds("scan") == (dt.time(8, 0), dt.time(15, 15))   # default
    assert mc.window_bounds("collection")[1] == dt.time(15, 30)           # honoured


def test_load_config_result_is_the_cached_object(monkeypatch, tmp_path):
    """Documents why load_config's result must be treated as read-only: it is
    the cached dict itself, so a mutating caller would poison every other."""
    assert mc.load_config() is mc.load_config()


# -- named operating windows ------------------------------------------------
def test_in_window_scan():
    assert mc.in_window("scan", _ct(2026, 8, 17, 8, 0)) is True
    assert mc.in_window("scan", _ct(2026, 8, 17, 7, 59)) is False
    assert mc.in_window("scan", _ct(2026, 8, 17, 15, 15)) is True
    assert mc.in_window("scan", _ct(2026, 8, 17, 15, 16)) is False


def test_in_window_false_on_non_trading_day():
    assert mc.in_window("scan", _ct(2026, 8, 22, 10, 0)) is False   # Saturday


def test_window_bounds_reads_start_and_end():
    assert mc.window_bounds("scan") == (dt.time(8, 0), dt.time(15, 15))
    # collection names its close `stop`, not `end`.
    assert mc.window_bounds("collection") == (dt.time(8, 0), dt.time(15, 20))


def test_driver_entry_window_is_evaluated_in_eastern_time():
    """driver_entry carries its own tz; 09:45 ET == 08:45 CT."""
    assert mc.in_window("driver_entry", _ct(2026, 8, 17, 8, 45)) is True
    assert mc.in_window("driver_entry", _ct(2026, 8, 17, 8, 44)) is False
    assert mc.in_window("driver_entry", _ct(2026, 8, 17, 14, 30)) is True
    assert mc.in_window("driver_entry", _ct(2026, 8, 17, 14, 31)) is False


def test_collection_window_ineligible_symbol_starts_at_0800():
    assert mc.in_collection_window(_ct(2026, 8, 17, 7, 0), eth_eligible=False) is False
    assert mc.in_collection_window(_ct(2026, 8, 17, 8, 0), eth_eligible=False) is True


def test_collection_window_eligible_symbol_starts_at_0630_after_activation():
    assert mc.in_collection_window(_ct(2026, 8, 17, 6, 30), eth_eligible=True) is True
    assert mc.in_collection_window(_ct(2026, 8, 17, 6, 29), eth_eligible=True) is False


def test_collection_window_eligible_symbol_is_inert_before_activation():
    """THE critical regression guard: pre-activation an eligible symbol must
    behave exactly like today -- 08:00 start, no GTH collection."""
    assert mc.in_collection_window(_ct(2026, 8, 14, 7, 0), eth_eligible=True) is False
    assert mc.in_collection_window(_ct(2026, 8, 14, 8, 0), eth_eligible=True) is True


def test_collection_window_defaults_to_ineligible():
    assert mc.in_collection_window(_ct(2026, 8, 17, 7, 0)) is False


def test_collection_window_stop_is_exclusive():
    assert mc.in_collection_window(_ct(2026, 8, 17, 15, 19)) is True
    assert mc.in_collection_window(_ct(2026, 8, 17, 15, 20)) is False


def test_collection_window_false_on_non_trading_day():
    assert mc.in_collection_window(_ct(2026, 8, 22, 10, 0), eth_eligible=True) is False


def test_session_flip_time_is_independent_of_collection_start():
    """Widening GTH collection to 06:30 must NOT move the Gamma display flip."""
    assert mc.session_flip_time() == dt.time(8, 0)


def test_unknown_window_name_raises():
    """A typo'd window name is a programming error, not a config degrade."""
    with pytest.raises(KeyError):
        mc.in_window("nope", _ct(2026, 8, 17, 10, 0))
