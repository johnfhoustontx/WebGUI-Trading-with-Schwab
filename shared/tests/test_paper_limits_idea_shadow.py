"""shared.paper_limits.idea_shadow: the quality-triggered ideas' switch and limits
(config/paper.toml [idea_shadow])."""
import pytest

from shared import paper_limits as pl

_DEFAULTS = {"mode": "shadow", "min_grade": "Strong", "grades": ("Strong",),
             "min_vol_rank": 50.0, "max_per_hour": 2, "min_gap_min": 20,
             "max_per_day": 10, "one_per_symbol": True}


def test_the_shipped_defaults():
    assert pl.idea_shadow() == _DEFAULTS


def test_the_file_overrides_each_key(monkeypatch):
    monkeypatch.setattr(pl, "load", lambda: {"idea_shadow": {
        "mode": "off", "min_grade": "Good", "min_vol_rank": 0, "max_per_hour": 4,
        "min_gap_min": 0, "max_per_day": 30, "one_per_symbol": False}})
    assert pl.idea_shadow() == {
        "mode": "off", "min_grade": "Good", "grades": ("Strong", "Good"),
        "min_vol_rank": 0.0, "max_per_hour": 4, "min_gap_min": 0,
        "max_per_day": 30, "one_per_symbol": False}


@pytest.mark.parametrize("key,bad", [
    ("mode", "on"), ("mode", True), ("min_grade", "Marginal"), ("min_grade", None),
    ("min_vol_rank", -1), ("min_vol_rank", 101), ("min_vol_rank", float("nan")),
    ("min_vol_rank", True), ("min_vol_rank", "50"),
    ("max_per_hour", 0), ("max_per_hour", 5), ("max_per_hour", 1.5),
    ("min_gap_min", -1), ("min_gap_min", 60),
    ("max_per_day", 0), ("max_per_day", 31), ("max_per_day", False),
    ("one_per_symbol", "yes"), ("one_per_symbol", 1),
])
def test_a_bad_value_reads_as_its_default(monkeypatch, key, bad):
    monkeypatch.setattr(pl, "load", lambda: {"idea_shadow": {key: bad}})
    got = pl.idea_shadow()
    assert got[key] == pl.DEFAULTS["idea_shadow"][key]
    assert got["grades"] == ("Strong",)


def test_a_bad_key_keeps_the_good_ones_beside_it(monkeypatch):
    monkeypatch.setattr(pl, "load", lambda: {"idea_shadow": {"max_per_hour": 0,
                                                             "max_per_day": 7}})
    got = pl.idea_shadow()
    assert got["max_per_hour"] == 2 and got["max_per_day"] == 7


def test_a_missing_section_is_the_defaults(monkeypatch):
    monkeypatch.setattr(pl, "load", lambda: {"idea_shadow": "junk"})
    assert pl.idea_shadow() == _DEFAULTS
