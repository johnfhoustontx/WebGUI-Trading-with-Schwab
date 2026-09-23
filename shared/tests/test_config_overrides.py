"""The operator override layer: config/local/<name>.toml over config/<name>.toml.

Settings -> Configuration writes ONLY the override file. The tracked file stays
the shipped value, because editing it would dirty the prod checkout and
tools/promote.sh refuses a dirty tree.
"""
import tomllib

import pytest

from shared import config_toml as ct

DEFAULTS = {"risk": {"cap": 100.0, "halt": 50.0}, "enabled": True}


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_CONFIG_OVERRIDES_IN_TESTS", "1")
    base = tmp_path / "paper.toml"
    base.write_text("[risk]\ncap = 200.0\n", encoding="utf-8")
    return base


def test_overlay_path_is_the_local_sibling(tmp_path):
    p = tmp_path / "scanner.toml"
    assert ct.overlay_path(p) == str(tmp_path / "local" / "scanner.toml")


def test_an_override_wins_over_the_tracked_file_and_keeps_siblings(files):
    ct.write_overrides(files, {"risk": {"halt": 75.0}})
    load, _ = ct.toml_loader(files, DEFAULTS)
    cfg = load()
    assert cfg["risk"] == {"cap": 200.0, "halt": 75.0}   # tracked + override
    assert cfg["enabled"] is True                         # built-in default


def test_saving_an_override_is_seen_without_a_restart(files):
    load, _ = ct.toml_loader(files, DEFAULTS)
    assert load()["risk"]["halt"] == 50.0
    ct.write_overrides(files, {"risk": {"halt": 60.0}})
    assert load()["risk"]["halt"] == 60.0     # the cache key covers both layers


def test_overrides_are_ignored_under_pytest_unless_a_test_opts_in(files, monkeypatch):
    ct.write_overrides(files, {"risk": {"halt": 75.0}})
    monkeypatch.delenv("TRADING_CONFIG_OVERRIDES_IN_TESTS")
    load, _ = ct.toml_loader(files, DEFAULTS)
    assert load()["risk"]["halt"] == 50.0


def test_a_malformed_override_is_ignored_not_fatal(files):
    op = ct.overlay_path(files)
    import os
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as fh:
        fh.write("[risk\nhalt = ")
    load, _ = ct.toml_loader(files, DEFAULTS)
    assert load()["risk"] == {"cap": 200.0, "halt": 50.0}


def test_an_empty_override_deletes_the_file(files):
    op = ct.write_overrides(files, {"risk": {"halt": 75.0}})
    ct.write_overrides(files, {})
    import os
    assert not os.path.exists(op)


def test_the_tracked_file_is_never_written(files):
    before = files.read_bytes()
    ct.write_overrides(files, {"risk": {"cap": 1.0}})
    assert files.read_bytes() == before


@pytest.mark.parametrize("data", [
    {"enabled": False, "uoa": {"k": 3.5, "top_n": 2}},
    {"iv_rank": {"0-DTE": 40, "SWING": 30}},
    {"windows": {"scan": {"start": "08:00", "end": "15:15"}}},
    {"trail": {"ratchet_ladder": [[0.5, 0.0], [0.65, 0.25]]}},
    {"netprem_groups": [{"key": "a", "label": "A", "symbols": ["$SPX", "SPY"]}]},
    {"sectors": {"$SPX": "INDEX", "AAPL": "Information Technology"}},
    {"gamma_flip": {"symbols": []}},
])
def test_dumps_round_trips(data):
    assert tomllib.loads(ct.dumps(data)) == data


def test_dumps_refuses_a_non_finite_number():
    with pytest.raises(ValueError):
        ct.dumps({"risk": {"cap": float("nan")}})
