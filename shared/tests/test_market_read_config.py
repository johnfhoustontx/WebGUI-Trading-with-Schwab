"""config/market_read.toml - the Market read scorecard's schedule and thresholds.

Design: docs/plans/2026-10-05-market-read-scorecard-design.md.
"""
import math
import tomllib

import pytest

import repo_paths
from shared import market_read_config as mr


@pytest.fixture(autouse=True)
def _fresh():
    mr.reset_cache()
    yield
    mr.reset_cache()


def _with(monkeypatch, **over):
    """Make the RAW loader return the defaults with keys replaced. A dotted
    key replaces one value inside a table. The accessor is patched, so the
    test proves the value is read, not merely equal."""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in mr.DEFAULTS.items()}
    for key, value in over.items():
        if "__" in key:
            table, name = key.split("__", 1)
            cfg[table][name] = value
        else:
            cfg[key] = value
    monkeypatch.setattr(mr, "load_raw", lambda: cfg)


def test_shipped_file_matches_the_built_in_defaults():
    assert mr.load() == mr.DEFAULTS
    # load() merges the file OVER the defaults, so a key deleted from the file
    # would still pass the line above. The raw file is the other direction.
    with open(repo_paths.MARKET_READ_TOML, "rb") as fh:
        assert tomllib.load(fh) == mr.DEFAULTS


def test_load_returns_a_fresh_mapping_each_time():
    a = mr.load()
    a["direction"]["move_pct"] = 99
    assert mr.load()["direction"]["move_pct"] == mr.DEFAULTS["direction"]["move_pct"]


@pytest.mark.parametrize("key", ["enabled", "public"])
@pytest.mark.parametrize("value,want", [(True, True), (False, False), ("true", False),
                                        (1, False), (None, False)])
def test_a_switch_is_on_only_for_a_real_true(monkeypatch, key, value, want):
    _with(monkeypatch, **{key: value})
    assert mr.load()[key] is want


@pytest.mark.parametrize("value,want", [(15, 15), (30, 30), (10, 15), (45, 15),
                                        (0, 15), ("30", 15), (True, 15), (15.0, 15)])
def test_the_interval_is_15_or_30(monkeypatch, value, want):
    _with(monkeypatch, interval_min=value)
    assert mr.load()["interval_min"] == want


@pytest.mark.parametrize("value,want", [(120, 120), (0, 300), (-5, 300),
                                        (math.nan, 300), ("x", 300), (None, 300)])
def test_the_stale_limit_falls_back_when_unusable(monkeypatch, value, want):
    _with(monkeypatch, stale_after_sec=value)
    assert mr.load()["stale_after_sec"] == want


@pytest.mark.parametrize("key,default", [
    ("direction__move_pct", 0.25), ("structure__room_pct", 0.50),
    ("structure__near_pct", 0.25), ("volatility__vix_move_pct", 1.0),
    ("flow__lean_pts", 5.0)])
@pytest.mark.parametrize("bad", [-1, math.nan, math.inf, "0.5", None, True])
def test_a_threshold_that_is_not_a_usable_number_falls_back(monkeypatch, key, default, bad):
    _with(monkeypatch, **{key: bad})
    table, name = key.split("__")
    assert mr.load()[table][name] == default


def test_a_threshold_of_zero_is_a_real_setting(monkeypatch):
    _with(monkeypatch, direction__move_pct=0)
    assert mr.load()["direction"]["move_pct"] == 0


@pytest.mark.parametrize("strong,weak", [(0.4, 0.6), (0.5, 0.5), (1.2, 0.4),
                                         (0.6, -0.1), ("0.6", 0.4), (None, 0.4)])
def test_breadth_shares_that_do_not_make_sense_both_fall_back(monkeypatch, strong, weak):
    _with(monkeypatch, breadth__strong_share=strong, breadth__weak_share=weak)
    assert mr.load()["breadth"] == mr.DEFAULTS["breadth"]


def test_usable_breadth_shares_are_kept(monkeypatch):
    _with(monkeypatch, breadth__strong_share=0.7, breadth__weak_share=0.3)
    assert mr.load()["breadth"] == {"strong_share": 0.7, "weak_share": 0.3}


@pytest.mark.parametrize("bad", [None, "SPY", [], [1, 2], ["", "  "], 5])
def test_structure_symbols_must_be_a_list_of_names(monkeypatch, bad):
    _with(monkeypatch, structure__symbols=bad)
    assert mr.load()["structure"]["symbols"] == ["SPY", "QQQ"]


def test_structure_symbols_are_kept_in_order(monkeypatch):
    _with(monkeypatch, structure__symbols=["$SPX", "IWM", 7, ""])
    assert mr.load()["structure"]["symbols"] == ["$SPX", "IWM"]


@pytest.mark.parametrize("value,want", [(25, 25), (1, 1), (0, 10), (-3, 10),
                                        (2.9, 2), ("10", 10), (None, 10)])
def test_min_contracts_is_a_whole_number_of_at_least_one(monkeypatch, value, want):
    _with(monkeypatch, flow__min_contracts=value)
    assert mr.load()["flow"]["min_contracts"] == want


def test_a_scalar_where_a_table_belongs_falls_back_to_that_table(monkeypatch):
    _with(monkeypatch, flow=5, direction="x")
    cfg = mr.load()
    assert cfg["flow"] == mr.DEFAULTS["flow"]
    assert cfg["direction"] == mr.DEFAULTS["direction"]
