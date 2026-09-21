"""Settings -> Configuration: the catalogue covers every setting, and the editor's
parse / override logic is exact.

The coverage tests are the teeth of the standing "configurable by default" rule
(CLAUDE.md): a TOML key with no catalogue entry is a setting the operator cannot
see in the app, so it fails here.
"""
import math
import pathlib
import tomllib

import pytest

import config_schema as cs
import config_store as store
from shared import config_toml

CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config"


def _shipped(name):
    with open(CONFIG / name, "rb") as fh:
        return tomllib.load(fh)


# ── coverage ─────────────────────────────────────────────────────────────────
def test_every_config_file_is_either_catalogued_or_explained():
    on_disk = {p.name for p in CONFIG.glob("*.toml")}
    known = set(cs.BY_NAME) | set(cs.NOT_HERE)
    assert on_disk - known == set(), f"uncatalogued config files: {on_disk - known}"
    assert set(cs.BY_NAME) - on_disk == set(), "catalogue names a missing file"


@pytest.mark.parametrize("cfg", cs.EDITABLE, ids=lambda c: c.name)
def test_every_key_in_the_file_has_a_catalogue_entry(cfg):
    missing = [" › ".join(p) for p in store.flatten(_shipped(cfg.name))
               if cs.locate(cfg, p)[1] is None]
    assert not missing, (f"{cfg.name}: add these to webgui/config_schema.py so "
                         f"they appear in Settings -> Configuration: {missing}")


@pytest.mark.parametrize("cfg", cs.EDITABLE, ids=lambda c: c.name)
def test_every_required_catalogue_entry_exists_in_the_file(cfg):
    base = store.flatten(_shipped(cfg.name))
    stale = [f.key for s in cfg.sections for f in s.fields
             if "*" not in f.key and not f.optional
             and tuple(cs.split_key(f.key)) not in base]
    assert not stale, f"{cfg.name}: catalogue entries with no key in the file: {stale}"


@pytest.mark.parametrize("cfg", cs.EDITABLE, ids=lambda c: c.name)
def test_every_shipped_value_round_trips_through_its_own_field(cfg):
    """The shipped value, shown in the editor and parsed back, is unchanged —
    so a wrong kind, unit or bound in the catalogue fails here, not on screen."""
    for path, value in store.flatten(_shipped(cfg.name)).items():
        _sec, fld = cs.locate(cfg, path)
        back = cs.parse(fld, cs.to_display(fld, value), shipped=value)
        if isinstance(value, float):
            assert math.isclose(back, value, rel_tol=1e-9), (path, value, back)
        elif isinstance(value, list) and value and isinstance(value[0], list):
            assert all(math.isclose(a, b) for r, s in zip(back, value)
                       for a, b in zip(r, s)), (path, value, back)
        else:
            assert back == value, (path, value, back)


def test_every_restart_target_is_a_known_unit():
    for cfg in cs.FILES:
        for sec in cfg.sections:
            for f in sec.fields:
                for u in cs.restart_for(cfg, sec, f):
                    assert u in cs.RESTART_LABELS, (cfg.name, f.key, u)


def test_labels_and_help_are_plain_words():
    for cfg in cs.FILES:
        assert cfg.title and cfg.summary
        for sec in cfg.sections:
            for f in sec.fields:
                assert "*" in f.key or f.label, f"{cfg.name} {f.key} has no label"
                assert "**" not in f.help


# ── parse ────────────────────────────────────────────────────────────────────
F = cs.Field


def test_a_fraction_is_typed_as_a_percent():
    f = F("x", "x", kind="fraction", min=0, max=60)
    assert cs.parse(f, 12) == 0.12
    assert cs.to_display(f, 0.12) == 12.0
    with pytest.raises(ValueError, match="at most 60"):
        cs.parse(f, 75)


@pytest.mark.parametrize("raw, msg", [(3.5, "whole number"), (-1, "at least 0"),
                                      (None, "required"), (float("nan"), "finite")])
def test_int_refusals_say_why(raw, msg):
    with pytest.raises(ValueError, match=msg):
        cs.parse(F("x", "x", kind="int", min=0, max=10), raw)


def test_a_float_that_shipped_as_an_int_stays_an_int():
    f = F("x", "x", kind="money", min=0, max=1e9)
    assert isinstance(cs.parse(f, 10000.0, shipped=10000), int)
    assert isinstance(cs.parse(f, 10000.5, shipped=10000), float)


@pytest.mark.parametrize("raw, ok", [("8:30", "08:30"), ("15:00", "15:00")])
def test_time_is_normalised(raw, ok):
    assert cs.parse(F("t", "t", kind="time"), raw) == ok


@pytest.mark.parametrize("raw", ["25:00", "8h30", "", "12:60"])
def test_bad_times_are_refused(raw):
    with pytest.raises(ValueError):
        cs.parse(F("t", "t", kind="time"), raw)


def test_a_pair_must_run_low_to_high():
    f = F("p", "p", kind="pair", min=0, max=1)
    assert cs.parse(f, [0.3, 0.55]) == [0.3, 0.55]
    with pytest.raises(ValueError, match="below"):
        cs.parse(f, [0.55, 0.3])


def test_a_ladder_must_rise_and_lock_less_than_its_peak():
    f = F("l", "l", kind="ladder")
    assert cs.parse(f, [[50, 0], [65, 25]]) == [[0.5, 0.0], [0.65, 0.25]]
    with pytest.raises(ValueError, match="rise"):
        cs.parse(f, [[65, 25], [50, 0]])
    with pytest.raises(ValueError, match="LESS"):
        cs.parse(f, [[50, 60]])


def test_symbols_are_uppercased_and_deduplicated():
    f = F("s", "s", kind="symbols")
    assert cs.parse(f, ["spy", "SPY", " qqq "]) == ["SPY", "QQQ"]
    assert cs.parse(f, []) == []


def test_an_optional_value_may_be_cleared():
    f = F("s", "s", kind="int", min=0, max=90, optional=True)
    assert cs.parse(f, None) is None


def test_quoted_keys_split_on_dots_outside_quotes():
    assert cs.split_key('iv_rank."0-DTE"') == ["iv_rank", "0-DTE"]


def test_exact_entries_win_over_wildcards():
    sec, f = cs.locate(cs.BY_NAME["sessions.toml"], ("slots", "analyze", "grace_min"))
    assert f.kind == "int"
    sec, f = cs.locate(cs.BY_NAME["sessions.toml"], ("slots", "analyze", "open"))
    assert f.kind == "time"


def test_cross_checks_catch_combinations():
    assert cs.cross_check("driver.toml", {("targets", "target_floor"): 600.0,
                                          ("targets", "daily_target"): 500.0,
                                          ("targets", "target_cap"): 1000.0})
    assert cs.cross_check("sessions.toml", {("windows", "scan", "start"): "15:00",
                                            ("windows", "scan", "end"): "08:00"})
    assert not cs.cross_check("sessions.toml", {("windows", "scan", "start"): "08:00",
                                                ("windows", "scan", "end"): "15:15"})


# ── overrides ────────────────────────────────────────────────────────────────
def test_only_changed_values_become_overrides():
    shipped = {"risk": {"cap": 3000.0, "halt": 1500.0}, "enabled": True}
    values = store.flatten(shipped)
    values[("risk", "halt")] = 1000.0
    assert store.build_overrides(shipped, values) == {"risk": {"halt": 1000.0}}


def test_choosing_the_shipped_value_again_removes_the_override():
    shipped = {"risk": {"cap": 3000.0}}
    assert store.build_overrides(shipped, store.flatten(shipped)) == {}


def test_a_table_array_is_rewritten_whole_in_its_order():
    shipped = _shipped("symbols.toml")
    values = store.flatten(shipped)
    values[("netprem_groups", "sectors", "symbols")] = ["XLE", "XLF"]
    over = store.build_overrides(shipped, values)
    groups = over["netprem_groups"]
    assert [g["key"] for g in groups] == [g["key"] for g in shipped["netprem_groups"]]
    assert groups[1]["symbols"] == ["XLE", "XLF"]
    assert groups[0] == shipped["netprem_groups"][0]
    merged = store.effective(shipped, tomllib.loads(config_toml.dumps(over)))
    assert merged["netprem_groups"][1]["symbols"] == ["XLE", "XLF"]


def test_an_unset_optional_is_not_written():
    shipped = {"structures": {"LONG_CALL": {"exit_dte": 21}}}
    values = store.flatten(shipped)
    values[("structures", "LONG_CALL", "debit_stop_frac")] = None
    assert store.build_overrides(shipped, values) == {}
    values[("structures", "LONG_CALL", "debit_stop_frac")] = 0.6
    assert store.build_overrides(shipped, values) == {
        "structures": {"LONG_CALL": {"debit_stop_frac": 0.6}}}


def test_save_writes_the_override_and_logs_the_change(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_CONFIG_OVERRIDES_IN_TESTS", "1")
    (tmp_path / "driver.toml").write_text("[risk]\ncap = 3000.0\n", encoding="utf-8")
    monkeypatch.setattr(store, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(store, "CHANGE_LOG", tmp_path / "local" / "changes.jsonl")
    store.save("driver.toml", {"risk": {"cap": 2000.0}},
               changes=[("risk › cap", 3000.0, 2000.0)])
    shipped, over = store.load("driver.toml")
    assert shipped == {"risk": {"cap": 3000.0}}
    assert over == {"risk": {"cap": 2000.0}}
    assert store.overridden_count("driver.toml") == 1
    log = store.recent_changes()
    assert log[0]["key"] == "risk › cap" and log[0]["to"] == 2000.0


def test_the_change_log_stamp_is_central_time_and_carries_its_offset(
        tmp_path, monkeypatch):
    """⚠ This is the STORED format, so the assertion is on the file, not on a
    rendered string: from here on every row's ``at`` is an AWARE Central stamp,
    which is the only thing that lets a reader name the zone. The naive rows
    already in ``changes.jsonl`` are handled at the reading end - see
    ``config_editor.change_stamp``."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    monkeypatch.setenv("TRADING_CONFIG_OVERRIDES_IN_TESTS", "1")
    (tmp_path / "driver.toml").write_text("[risk]\ncap = 3000.0\n", encoding="utf-8")
    monkeypatch.setattr(store, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(store, "CHANGE_LOG", tmp_path / "local" / "changes.jsonl")
    store.save("driver.toml", {"risk": {"cap": 2000.0}},
               changes=[("risk › cap", 3000.0, 2000.0)])
    at = store.recent_changes()[0]["at"]
    when = datetime.fromisoformat(at)
    assert when.utcoffset() is not None, f"{at!r} carries no zone"
    # the offset IS Central's for that instant - not UTC, and not whatever the
    # host happens to be set to.
    assert when.utcoffset() == when.astimezone(
        ZoneInfo("America/Chicago")).utcoffset()
