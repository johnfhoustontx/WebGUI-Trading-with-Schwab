"""config/breadth.toml - the breadth history's settings."""
import datetime as dt

from shared import breadth_config as bc


def test_shipped_file_equals_the_defaults():
    bc.reset_cache()
    assert bc.load() == bc.DEFAULTS


def test_bad_values_fall_back(monkeypatch):
    monkeypatch.setattr(bc, "load_raw", lambda: {
        "settle_min": 5, "tolerance_pct": "x", "min_issues": -1,
        "history_years": 99, "enabled": "no"})
    got = bc.load()
    assert got["settle_min"] == 20          # below the new highs settle
    assert got["tolerance_pct"] == 2.0
    assert got["min_issues"] == 1000
    assert got["history_years"] == 20
    assert got["enabled"] is True           # only a real False switches it off


def test_false_switches_it_off(monkeypatch):
    monkeypatch.setattr(bc, "load_raw", lambda: {"enabled": False})
    assert bc.load()["enabled"] is False


def test_a_window_that_does_not_run_forward_falls_back(monkeypatch):
    monkeypatch.setattr(bc, "load_raw", lambda: {"correct_at": "13:00", "correct_until": "12:00"})
    got = bc.load()
    assert (got["correct_at"], got["correct_until"]) == ("07:00", "12:00")


def test_a_bad_time_falls_back(monkeypatch):
    monkeypatch.setattr(bc, "load_raw", lambda: {"correct_at": "25:99", "correct_until": 7})
    got = bc.load()
    assert (got["correct_at"], got["correct_until"]) == ("07:00", "12:00")


def test_times_parse(monkeypatch):
    monkeypatch.setattr(bc, "load_raw", lambda: {"correct_at": "06:30"})
    assert bc.clock(bc.load()["correct_at"]) == dt.time(6, 30)


def test_paths():
    import repo_paths
    assert repo_paths.BREADTH_TOML.name == "breadth.toml"
    assert repo_paths.BREADTH_DB.as_posix().endswith("shared/data/breadth.db")
