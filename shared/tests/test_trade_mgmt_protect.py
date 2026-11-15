"""``[protect]`` in config/trade_mgmt.toml: the profit trail's settings.

Design: docs/plans/2026-11-15-profit-trail-design.md. A bad value is the SHIPPED
one, never a looser one, because these close positions.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from shared import config_toml
from shared import trade_mgmt as tm

CT = ZoneInfo("America/Chicago")


@pytest.fixture(autouse=True)
def _fresh():
    tm.reset_cache()
    yield
    tm.reset_cache()


def _with(tmp_path, monkeypatch, text):
    toml = tmp_path / "tm.toml"
    toml.write_text(text, encoding="utf-8")
    load, _ = config_toml.toml_loader(toml, tm.DEFAULTS, label="test")
    monkeypatch.setattr(tm, "load", load)
    return tm.protect()


def test_the_shipped_settings():
    p = tm.protect()
    assert p["trail_start"] == pytest.approx(0.25)
    assert p["trail_giveback"] == pytest.approx(0.10)
    assert p["quiet_windows"] == [(dt.time(8, 30), dt.time(8, 45)),
                                  (dt.time(14, 45), dt.time(15, 0))]
    assert p["captured_at_target"] == "close"
    assert all(p[b] is True for b in tm.PROTECT_BOOKS)


def test_a_partial_table_keeps_the_rest(tmp_path, monkeypatch):
    p = _with(tmp_path, monkeypatch, "[protect]\ntrail_giveback = 0.15\n")
    assert p["trail_giveback"] == pytest.approx(0.15)
    assert p["trail_start"] == pytest.approx(0.25)


def test_a_start_of_zero_is_allowed_and_turns_the_trail_off(tmp_path, monkeypatch):
    assert _with(tmp_path, monkeypatch, "[protect]\ntrail_start = 0\n")["trail_start"] == 0.0


@pytest.mark.parametrize("text", [
    "trail_giveback = 0.30",          # at or above the start: closes at once
    "trail_giveback = 0",
    "trail_start = 1.5",
    "trail_start = true",
    'trail_giveback = "a lot"',
])
def test_an_unusable_trail_is_the_shipped_one(text, tmp_path, monkeypatch):
    p = _with(tmp_path, monkeypatch, "[protect]\n" + text + "\n")
    assert (p["trail_start"], p["trail_giveback"]) == pytest.approx((0.25, 0.10))


def test_a_bad_window_list_is_the_shipped_one(tmp_path, monkeypatch):
    p = _with(tmp_path, monkeypatch, '[protect]\nquiet_windows = ["8:30-8:45", "later"]\n')
    assert p["quiet_windows"] == [(dt.time(8, 30), dt.time(8, 45)),
                                  (dt.time(14, 45), dt.time(15, 0))]


def test_an_empty_window_list_means_none(tmp_path, monkeypatch):
    assert _with(tmp_path, monkeypatch, "[protect]\nquiet_windows = []\n")["quiet_windows"] == []


def test_an_unknown_target_word_and_a_non_bool_switch_are_shipped(tmp_path, monkeypatch):
    p = _with(tmp_path, monkeypatch,
              '[protect]\ncaptured_at_target = "maybe"\nledger = "no"\naccount = false\n')
    assert p["captured_at_target"] == "close"
    assert p["ledger"] is True
    assert p["account"] is False


@pytest.mark.parametrize("hhmm, quiet", [
    ((8, 29), False), ((8, 30), True), ((8, 44), True), ((8, 45), False),
    ((14, 45), True), ((14, 59), True), ((15, 0), False), ((11, 0), False)])
def test_the_quiet_windows_are_closed_at_the_start_and_open_at_the_end(hhmm, quiet):
    when = dt.datetime(2026, 11, 16, *hhmm, tzinfo=CT)
    assert tm.in_quiet_window(when) is quiet


def test_a_utc_time_is_read_in_central_and_naive_is_central():
    utc = dt.datetime(2026, 11, 16, 14, 35, tzinfo=dt.timezone.utc)   # 08:35 CT
    assert tm.in_quiet_window(utc) is True
    assert tm.in_quiet_window(dt.datetime(2026, 11, 16, 8, 35)) is True
    assert tm.in_quiet_window(None) is False
