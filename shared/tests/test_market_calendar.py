import datetime as dt
from datetime import date
from zoneinfo import ZoneInfo

import pytest

import repo_paths
from shared import market_calendar as mc


def _t(h, m):
    return dt.time(h, m)

CT = ZoneInfo("America/Chicago")
ET = ZoneInfo("America/New_York")


def _ct(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=CT)


def _et(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=ET)


@pytest.fixture(autouse=True)
def _clean_config_cache():
    """Every test starts from the real sessions.toml, not a neighbour's stub."""
    mc.reset_config_cache()
    yield
    mc.reset_config_cache()


def test_sessions_toml_path_declared():
    assert repo_paths.SESSIONS_TOML.name == "sessions.toml"
    assert repo_paths.SESSIONS_TOML.parent.name == "config"


def test_no_bounded_holidays_alias():
    """The 2026-27 ``HOLIDAYS`` union is gone on purpose. While it existed the
    module answered two different ways from 2028 on -- ``is_holiday()`` was
    year-general, the set was not -- so a consumer's answer depended on which it
    happened to touch. Every consumer now calls the predicates."""
    assert not hasattr(mc, "HOLIDAYS")


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


def test_new_year_observed_on_31_dec_of_the_prior_year():
    """The year-boundary spill: 1 Jan 2028 is a Saturday, so the NYSE observes
    New Year's Day on Friday 2027-12-31 -- a date that lives in the *2028* set.
    ``is_holiday`` consulted only ``nyse_holidays(d.year)``, so it read that
    closure as an ordinary trading day."""
    assert date(2028, 1, 1).weekday() == 5              # Saturday -- the trigger
    assert date(2027, 12, 31) in mc.nyse_holidays(2028)
    assert date(2027, 12, 31) not in mc.nyse_holidays(2027)   # not in its own year
    assert mc.is_holiday(date(2027, 12, 31)) is True
    assert mc.is_trading_day(date(2027, 12, 31)) is False


def test_prev_trading_day_skips_the_spilled_new_year_holiday():
    # Monday 2028-01-03 -> Thursday 2027-12-30 (Fri 12/31 is the observed
    # New Year's closure, and 1-2 Jan are the weekend).
    assert mc.prev_trading_day(date(2028, 1, 3)) == date(2027, 12, 30)


def test_spill_lookup_does_not_disturb_any_other_year():
    """The extra ``d.year + 1`` lookup must only ever add a 31 Dec date. Pin the
    full 2026 and 2027 sets (10 each) and check no ordinary late-December day
    became a holiday."""
    assert len(mc.nyse_holidays(2026)) == 10
    assert len(mc.nyse_holidays(2027)) == 10
    # 2027-01-01 is a Friday, so nothing spills back into 2026.
    assert mc.is_holiday(date(2026, 12, 31)) is False
    assert mc.is_trading_day(date(2026, 12, 31)) is True
    # And a normal 30 Dec in the spill year stays a trading day.
    assert mc.is_trading_day(date(2027, 12, 30)) is True


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


def test_mins_to_close_counts_down_to_the_regular_close():
    # The regular session ends 15:00 CT == 16:00 ET, the cash close.
    assert mc.mins_to_close(_ct(2026, 8, 17, 14, 0)) == 60.0
    assert mc.mins_to_close(_ct(2026, 8, 17, 8, 30)) == 390.0
    # 15:00 is INSIDE the regular session (it owns the minute it shares with
    # the curb open), so the last reading is 0.0 -- not None, and never negative.
    assert mc.mins_to_close(_ct(2026, 8, 17, 15, 0)) == 0.0


def test_mins_to_close_is_none_outside_the_regular_session():
    # None, NOT 0.0 or a negative: it gates the afternoon/late dealer setups,
    # and 0.0 would read as "the close is upon us" all weekend long.
    assert mc.mins_to_close(_ct(2026, 8, 17, 7, 0)) is None    # GTH
    assert mc.mins_to_close(_ct(2026, 8, 17, 15, 5)) is None   # curb
    assert mc.mins_to_close(_ct(2026, 8, 22, 12, 0)) is None   # Saturday
    assert mc.mins_to_close(_ct(2026, 9, 7, 12, 0)) is None    # Labor Day


def test_mins_to_close_converts_a_non_ct_datetime():
    # 18:00 UTC on a summer weekday == 13:00 CT -> two hours left.
    utc = dt.datetime(2026, 8, 17, 18, 0, tzinfo=dt.timezone.utc)
    assert mc.mins_to_close(utc) == 120.0


def test_next_regular_open_is_today_when_the_open_is_still_ahead():
    # 2026-08-17 is a Monday. Before 08:30 CT the next open is today's.
    assert mc.next_regular_open(_ct(2026, 8, 17, 6, 45)) == _ct(2026, 8, 17, 8, 30)


def test_next_regular_open_rolls_to_tomorrow_once_the_open_has_passed():
    # Strictly after: mid-session and post-close both answer the NEXT day, so a
    # countdown can never stall on the instant it just passed.
    assert mc.next_regular_open(_ct(2026, 8, 17, 8, 30)) == _ct(2026, 8, 18, 8, 30)
    assert mc.next_regular_open(_ct(2026, 8, 17, 12, 0)) == _ct(2026, 8, 18, 8, 30)
    assert mc.next_regular_open(_ct(2026, 8, 17, 16, 0)) == _ct(2026, 8, 18, 8, 30)


def test_next_regular_open_skips_the_weekend():
    # Friday evening and Saturday both answer Monday -- the roll goes through
    # next_trading_day, so it is the calendar's answer, not a +1 day.
    assert mc.next_regular_open(_ct(2026, 8, 21, 17, 0)) == _ct(2026, 8, 24, 8, 30)
    assert mc.next_regular_open(_ct(2026, 8, 22, 12, 0)) == _ct(2026, 8, 24, 8, 30)


def test_next_regular_open_skips_a_holiday():
    # Labor Day 2026 is Monday 7 Sep, so Sunday answers Tuesday the 8th -- and
    # the holiday itself answers the same, rather than its own dead open.
    assert mc.is_trading_day(date(2026, 9, 7)) is False
    assert mc.next_regular_open(_ct(2026, 9, 6, 12, 0)) == _ct(2026, 9, 8, 8, 30)
    assert mc.next_regular_open(_ct(2026, 9, 7, 6, 0)) == _ct(2026, 9, 8, 8, 30)


def test_next_regular_open_converts_a_non_ct_datetime():
    # 11:00 UTC on a summer weekday == 06:00 CT -> today's open is still ahead.
    utc = dt.datetime(2026, 8, 17, 11, 0, tzinfo=dt.timezone.utc)
    assert mc.next_regular_open(utc) == _ct(2026, 8, 17, 8, 30)


def test_next_regular_open_reads_the_configured_regular_start(monkeypatch):
    """It adds no time literal of its own -- move sessions.regular.start and the
    answer moves with it, exactly as ``session_at`` does."""
    monkeypatch.setattr(mc, "_session_bounds",
                        lambda name: (dt.time(9, 5), dt.time(15, 0)))
    assert mc.next_regular_open(_ct(2026, 8, 17, 6, 0)) == _ct(2026, 8, 17, 9, 5)


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
    assert mc.in_window("driver_entry", _ct(2026, 8, 17, 14, 29)) is True
    assert mc.in_window("driver_entry", _ct(2026, 8, 17, 14, 31)) is False


def test_driver_entry_end_is_exclusive():
    """15:29 ET is in, the whole 15:30 ET minute is OUT -- matching
    ``driver_svc``'s ``hm >= RTH_END`` gate, which keeps the last 30 min before
    the close free of NEW entries. Inclusive here would open a 16th checkpoint
    slot at 15:30 ET."""
    assert mc.in_window("driver_entry", _et(2026, 8, 17, 15, 29)) is True
    assert mc.in_window("driver_entry", _et(2026, 8, 17, 15, 30)) is False
    assert mc.in_window("driver_entry", _et(2026, 8, 17, 15, 31)) is False


def test_end_exclusive_does_not_leak_to_other_windows():
    """A window WITHOUT the flag keeps the default INCLUSIVE close."""
    assert mc.in_window("scan", _ct(2026, 8, 17, 15, 15)) is True
    assert mc.in_window("market_snapshot", _ct(2026, 8, 17, 15, 0)) is True


def test_driver_entry_end_exclusive_survives_a_partial_user_toml(
        monkeypatch, tmp_path):
    """``end_exclusive`` lives in ``_DEFAULTS``, so a config that overrides only
    ``start`` -- or omits the window entirely -- cannot silently lose
    exclusivity and re-open the 15:30 ET slot."""
    _write_cfg(monkeypatch, tmp_path,
               '[windows.driver_entry]\ntz = "America/New_York"\n'
               'start = "09:45"\n')
    assert mc.window_bounds("driver_entry") == (dt.time(9, 45), dt.time(15, 30))
    assert mc.in_window("driver_entry", _et(2026, 8, 17, 15, 29)) is True
    assert mc.in_window("driver_entry", _et(2026, 8, 17, 15, 30)) is False


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


# --- scheduled slot times ([slots] in sessions.toml) -------------------------
# The scheduled Claude-analyze briefings and the thrice-daily action digest were
# hard-coded dicts in services/options_svc/scheduler.py, and the nightly momentum
# cascade a tuple in sentiment_svc. They are named clock times - the same thing
# [windows] already models - and two of the three cost real money per firing
# (each analyze slot is a paid Claude call), so they belong where an operator can
# see and change them.

def test_analyze_slots_match_the_pre_extraction_times():
    assert mc.slot_times("analyze") == {
        "premarket": _t(8, 0),
        "open": _t(8, 48),
        "midday": _t(11, 30),
        "close": _t(15, 15),
    }
    assert mc.slot_grace_min("analyze") == 20


def test_action_alert_slots_match_the_pre_extraction_times():
    assert mc.slot_times("action_alert") == {
        "morning": _t(10, 0),
        "midday": _t(13, 0),
        "close": _t(15, 0),
    }
    assert mc.slot_grace_min("action_alert") == 20


def test_momentum_is_a_single_nightly_slot():
    assert mc.slot_times("momentum") == {"at": _t(16, 20)}


def test_unknown_slot_group_raises():
    """A typo'd group is a programming error, not something to degrade past -
    mirrors _window()."""
    import pytest as _pytest
    with _pytest.raises(KeyError):
        mc.slot_times("nope")


def test_a_malformed_slot_time_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setattr(mc, "load_config",
                        lambda: {"slots": {"analyze": {"midday": "half past"}}})
    assert mc.slot_times("analyze")["midday"] == _t(11, 30)


def test_a_malformed_grace_falls_back(monkeypatch):
    monkeypatch.setattr(mc, "load_config",
                        lambda: {"slots": {"analyze": {"grace_min": "soon"}}})
    assert mc.slot_grace_min("analyze") == 20


def test_grace_min_is_not_returned_as_a_slot(monkeypatch):
    """grace_min shares the table with the times; treating it as one would
    schedule a briefing at a nonsense hour."""
    assert "grace_min" not in mc.slot_times("analyze")


def test_stream_window_bounds_and_membership():
    """The stream runs 08:00-15:20 CT, matching when collection runs.

    Held as its own window rather than borrowing [windows.collection]: the file
    warns against conflating windows, and widening collection must not silently
    extend a public broadcast.
    """
    mc.reset_config_cache()

    start, end = mc.window_bounds("stream")
    assert (start.hour, start.minute) == (8, 0)
    assert (end.hour, end.minute) == (15, 20)

    assert mc.in_window("stream", _ct(2026, 9, 2, 9, 0)) is True
    assert mc.in_window("stream", _ct(2026, 9, 2, 7, 59)) is False
    assert mc.in_window("stream", _ct(2026, 9, 2, 15, 21)) is False


def test_stream_window_survives_a_missing_toml():
    """Every config in this repo degrades to built-in defaults rather than
    raising. A window present only in the TOML would break on a malformed file."""
    assert mc._DEFAULTS["windows"]["stream"] == {"start": "08:00", "end": "15:20"}


def test_regular_session_has_opened_is_false_before_the_open():
    """08:00 CT on a Tuesday, before the 08:30 open."""
    assert mc.regular_session_has_opened(dt.datetime(2026, 9, 8, 8, 0)) is False


def test_regular_session_has_opened_is_true_during_and_after_the_session():
    assert mc.regular_session_has_opened(dt.datetime(2026, 9, 8, 9, 30)) is True
    # After the cash close the day's move is still real, so this stays True.
    assert mc.regular_session_has_opened(dt.datetime(2026, 9, 8, 15, 45)) is True


def test_regular_session_has_opened_is_false_at_the_weekend():
    """2026-09-05 is a Saturday."""
    assert mc.regular_session_has_opened(dt.datetime(2026, 9, 5, 12, 0)) is False
