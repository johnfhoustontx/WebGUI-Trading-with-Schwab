"""config/feed.toml: the raw feed's mode and its catch-up tunables.

``mode`` is the cut-over switch (docs/plans/2026-10-14-raw-feed-and-catch-up-design.md
§7): anything it does not recognise must read as "off", the behaviour of the
app before the feed existed, never as a mode that fetches or writes."""
import pytest

from shared import feed_config as fc


def test_the_shipped_file_matches_the_defaults():
    fc.reset_cache()
    assert (fc.mode(), fc.keep_sessions(), fc.stale_alarm_min(),
            fc.push_max_age_min(), fc.lag_alarm_min()) == ("off", 5, 2, 3, 5)
    assert fc.shadow_enabled() is False


@pytest.mark.parametrize("raw", ["ON!", "", None, 3, ["tee"], "Tee "])
def test_an_unknown_mode_is_off(monkeypatch, raw):
    monkeypatch.setattr(fc, "load", lambda: {"feed": {"mode": raw}, "catchup": {}})
    assert fc.mode() == ("tee" if raw == "Tee " else "off")


@pytest.mark.parametrize("mode", ["off", "tee", "on"])
def test_each_known_mode_reads_back(monkeypatch, mode):
    monkeypatch.setattr(fc, "load", lambda: {"feed": {"mode": mode}, "catchup": {}})
    assert fc.mode() == mode
    assert fc.shadow_enabled() is (mode == "tee")


@pytest.mark.parametrize("raw", [-1, 0, "5", 2.5, True, None, float("nan")])
def test_a_bad_count_is_the_default(monkeypatch, raw):
    monkeypatch.setattr(fc, "load", lambda: {
        "feed": {"keep_sessions": raw, "stale_alarm_min": raw},
        "catchup": {"push_max_age_min": raw, "lag_alarm_min": raw}})
    assert (fc.keep_sessions(), fc.stale_alarm_min(),
            fc.push_max_age_min(), fc.lag_alarm_min()) == (5, 2, 3, 5)


def test_a_good_count_is_read(monkeypatch):
    monkeypatch.setattr(fc, "load", lambda: {
        "feed": {"keep_sessions": 8, "stale_alarm_min": 4},
        "catchup": {"push_max_age_min": 10, "lag_alarm_min": 15}})
    assert (fc.keep_sessions(), fc.stale_alarm_min(),
            fc.push_max_age_min(), fc.lag_alarm_min()) == (8, 4, 10, 15)


def test_a_missing_section_is_the_defaults(monkeypatch):
    monkeypatch.setattr(fc, "load", lambda: {"feed": 5})
    assert (fc.mode(), fc.keep_sessions(), fc.lag_alarm_min()) == ("off", 5, 5)
