"""The Configuration page's pure helpers."""
from datetime import datetime
from zoneinfo import ZoneInfo

import config_schema as cs
import config_store as store
from pages import config_editor as ce


def _rows(name, title):
    cfg = cs.BY_NAME[name]
    sec = next(s for s in cfg.sections if s.title == title)
    import tomllib
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / "config" / name
    base = store.flatten(tomllib.loads(p.read_text(encoding="utf-8")))
    return ce.expand_fields(cfg, sec, base, {})


def test_slot_wildcards_expand_to_each_named_slot_with_a_readable_label():
    rows = _rows("sessions.toml", "Hourly trade idea (Discord and Telegram)")
    labels = [label for _p, _f, label in rows]
    assert labels[0] == "Fire if late by at most"         # exact entry kept once
    assert "08:35" in labels and labels.count("Fire if late by at most") == 1


def test_optional_per_structure_rules_appear_even_when_unset():
    rows = _rows("trade_mgmt.toml", "Per-structure rules")
    paths = {p for p, _f, _l in rows}
    assert ("structures", "LONG_CALL", "debit_stop_frac") in paths


def test_display_value_speaks_units():
    f = cs.Field("x", "x", kind="fraction", unit="%")
    assert ce.display_value(0.5, 0.5, f) == "50%"
    assert ce.display_value(True, True, cs.Field("b", "b", kind="bool")) == "on"
    assert ce.display_value(None, None, f) == "not set"


def test_restart_targets_follow_section_and_field_overrides():
    cfg = cs.BY_NAME["sessions.toml"]
    assert ce.restart_targets(cfg, [("slots", "eod_report", "at")]) == [cs.TIMERS]
    assert ce.restart_targets(cfg, [("windows", "driver_entry", "start")]) == [cs.DRIVER]


def test_search_needs_every_word_somewhere():
    assert ce.matches("take profit", "Take profit at", "")
    assert not ce.matches("take vix", "Take profit at", "")
    assert not ce.matches("", "anything")


def test_market_busy_is_true_mid_session_and_false_on_a_weekend():
    ct = ZoneInfo("America/Chicago")
    assert ce.market_busy(datetime(2026, 9, 16, 10, 0, tzinfo=ct))
    assert not ce.market_busy(datetime(2026, 9, 19, 10, 0, tzinfo=ct))
