"""config/feed.toml: the raw feed's mode and its catch-up tunables.

``mode`` is the cut-over switch (docs/plans/2026-10-14-raw-feed-and-catch-up-design.md
§7): anything it does not recognise must read as "off", the behaviour of the
app before the feed existed, never as a mode that fetches or writes."""
import pytest

from shared import feed_config as fc


def test_the_shipped_file_matches_the_defaults():
    fc.reset_cache()
    assert (fc.mode(), fc.keep_sessions(), fc.zlib_level(), fc.stale_alarm_min(),
            fc.push_max_age_min(), fc.lag_alarm_min()) == ("off", 5, 6, 2, 3, 5)
    assert fc.shadow_enabled() is False


def test_the_shipped_file_offers_nothing_unbuilt():
    # The alarms and the push age belong to the deriver and feed_svc (Phases
    # 3-4): until they exist the file must not offer them as live settings.
    import tomllib
    import repo_paths
    with open(repo_paths.FEED_TOML, "rb") as fh:
        shipped = tomllib.load(fh)
    assert set(shipped) == {"feed"}
    assert set(shipped["feed"]) == {"mode", "keep_sessions", "zlib_level"}


@pytest.mark.parametrize("raw", ["ON!", "", None, 3, ["tee"], "Tee "])
def test_an_unknown_mode_is_off(monkeypatch, raw):
    monkeypatch.setattr(fc, "load", lambda: {"feed": {"mode": raw}, "catchup": {}})
    assert fc.mode() == ("tee" if raw == "Tee " else "off")


@pytest.mark.parametrize("mode", ["off", "tee"])
def test_each_built_mode_reads_back(monkeypatch, mode):
    monkeypatch.setattr(fc, "load", lambda: {"feed": {"mode": mode}, "catchup": {}})
    assert fc.mode() == mode
    assert fc.shadow_enabled() is (mode == "tee")


def test_on_reads_as_off_until_the_feed_service_exists(monkeypatch, caplog):
    # "on" hands the fetching to feed_svc, which is not built yet (Phases 3-4).
    # Read as "on" it would stop the tee and run as off while promising
    # gap-free promotes; it must say so instead.
    monkeypatch.setattr(fc, "load", lambda: {"feed": {"mode": "on"}, "catchup": {}})
    monkeypatch.setattr(fc, "_WARNED", set())
    with caplog.at_level("WARNING"):
        assert fc.mode() == "off"
        assert fc.mode() == "off"
    assert sum("not built yet" in r.getMessage() for r in caplog.records) == 1


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


@pytest.mark.parametrize("raw, want", [(1, 1), (9, 9), (0, 6), (10, 6), ("1", 6), (None, 6)])
def test_the_zlib_level_is_one_to_nine_else_six(monkeypatch, raw, want):
    monkeypatch.setattr(fc, "load", lambda: {"feed": {"zlib_level": raw}, "catchup": {}})
    assert fc.zlib_level() == want
