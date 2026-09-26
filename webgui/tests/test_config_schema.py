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
    (tmp_path / "paper.toml").write_text("[risk]\ncap = 3000.0\n", encoding="utf-8")
    monkeypatch.setattr(store, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(store, "CHANGE_LOG", tmp_path / "local" / "changes.jsonl")
    store.save("paper.toml", {"risk": {"cap": 2000.0}},
               changes=[("risk › cap", 3000.0, 2000.0)])
    shipped, over = store.load("paper.toml")
    assert shipped == {"risk": {"cap": 3000.0}}
    assert over == {"risk": {"cap": 2000.0}}
    assert store.overridden_count("paper.toml") == 1
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
    (tmp_path / "paper.toml").write_text("[risk]\ncap = 3000.0\n", encoding="utf-8")
    monkeypatch.setattr(store, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(store, "CHANGE_LOG", tmp_path / "local" / "changes.jsonl")
    store.save("paper.toml", {"risk": {"cap": 2000.0}},
               changes=[("risk › cap", 3000.0, 2000.0)])
    at = store.recent_changes()[0]["at"]
    when = datetime.fromisoformat(at)
    assert when.utcoffset() is not None, f"{at!r} carries no zone"
    # the offset IS Central's for that instant - not UTC, and not whatever the
    # host happens to be set to.
    assert when.utcoffset() == when.astimezone(
        ZoneInfo("America/Chicago")).utcoffset()


# ── Market news: the feeds list is read-only, the switches are editable ─────
NEWS = cs.BY_NAME["news.toml"]


def test_rth_polling_cannot_go_below_three_minutes():
    _sec, fld = cs.locate(NEWS, ("collector", "rth_poll_min"))
    assert fld.min == 3
    with pytest.raises(ValueError, match="at least 3"):
        cs.parse(fld, 2)
    assert cs.parse(fld, 3) == 3


def test_every_shipped_feed_field_is_read_only():
    paths = [p for p in store.flatten(_shipped("news.toml")) if p[0] == "feeds"]
    assert paths
    assert all(cs.is_readonly(NEWS, p) for p in paths)


def test_a_feeds_key_the_catalogue_does_not_know_is_still_read_only():
    """A hand-written [[feeds]] entry may carry a key with no field; it must not
    become writable by falling outside the catalogue."""
    assert cs.is_readonly(NEWS, ("feeds", "3", "some_new_key"))


def test_the_feed_switches_are_editable_and_named_by_feed():
    flags = [p for p in store.flatten(_shipped("news.toml")) if p[0] == "feed_flags"]
    assert {p[1] for p in flags} == {f["name"] for f in _shipped("news.toml")["feeds"]}
    for p in flags:
        sec, fld = cs.locate(NEWS, p)
        assert sec.title == "Feed switches" and fld.kind == "bool", p
        assert not cs.is_readonly(NEWS, p), p
    _s, pub = cs.locate(NEWS, ("feed_flags", "ZeroHedge", "public"))
    assert pub.help == "Off keeps this feed's headlines in the app only."
    _s, en = cs.locate(NEWS, ("feed_flags", "ZeroHedge", "enabled"))
    assert "stops polling" in en.help


def test_no_other_file_has_a_read_only_field():
    """Read-only is the news feed list's; nothing else changed shape."""
    for cfg in cs.EDITABLE:
        if cfg is NEWS:
            continue
        assert not [p for p in store.flatten(_shipped(cfg.name))
                    if cs.is_readonly(cfg, p)], cfg.name


def test_quoted_feed_names_with_spaces_and_dots_split_and_locate():
    assert cs.split_key('feed_flags."SEC Insider Buys".public') == [
        "feed_flags", "SEC Insider Buys", "public"]
    assert cs.split_key('feed_flags."A.B".enabled') == ["feed_flags", "A.B", "enabled"]
    _s, fld = cs.locate(NEWS, cs.split_key('feed_flags."Federal Reserve".enabled'))
    assert fld is not None and fld.kind == "bool"


def test_a_switch_override_round_trips_through_the_writer_and_merges_by_key():
    """Names with spaces are quoted by the writer, read back by tomllib, and the
    override is a TABLE - merged over the shipped file it leaves every other
    feed's switches, and the whole feeds list, as shipped."""
    shipped = _shipped("news.toml")
    values = store.flatten(shipped)
    values[("feed_flags", "SEC Insider Buys", "public")] = False
    values[("feed_flags", "Federal Reserve", "enabled")] = False
    over = store.build_overrides(shipped, values)
    assert over == {"feed_flags": {"SEC Insider Buys": {"public": False},
                                   "Federal Reserve": {"enabled": False}}}
    text = config_toml.dumps(over)
    assert '[feed_flags."SEC Insider Buys"]' in text
    back = tomllib.loads(text)
    assert back == over
    merged = store.effective(shipped, back)
    assert merged["feeds"] == shipped["feeds"]
    assert merged["feed_flags"]["SEC Insider Buys"] == {"enabled": True, "public": False}
    assert merged["feed_flags"]["MarketWatch"] == shipped["feed_flags"]["MarketWatch"]


# ── news v2: impact rank, economic calendar (T11) ────────────────────────────
def test_phrases_keep_case_and_spaces_and_split_on_commas_only():
    f = F("w", "w", kind="phrases")
    assert cs.parse(f, "rate cut, FOMC ,rate cut") == ["rate cut", "FOMC"]
    assert cs.parse(f, ["Fed chair", "fed chair"]) == ["Fed chair"]     # casefold dedupe
    assert cs.parse(f, ["  chapter 11 ", "", "SEC charges"]) == ["chapter 11",
                                                                  "SEC charges"]
    assert cs.parse(f, []) == []


def test_the_impact_keyword_words_are_phrases_and_their_points_are_bounded():
    cfg = cs.BY_NAME["news.toml"]
    _s, words = cs.locate(cfg, ("impact", "keywords", "tier1", "words"))
    assert words.kind == "phrases"
    _s, pts = cs.locate(cfg, ("impact", "keywords", "tier2", "points"))
    assert pts.kind == "int" and pts.min == -10 and pts.max == 10
    with pytest.raises(ValueError, match="at most 10"):
        cs.parse(pts, 11)


def test_the_dividend_keys_restart_trade_and_news():
    cfg = cs.BY_NAME["news.toml"]
    sec, fld = cs.locate(cfg, ("calendar", "dividends", "refresh_at"))
    assert set(cs.restart_for(cfg, sec, fld)) == {cs.TRADE, cs.NEWS}
    sec, fld = cs.locate(cfg, ("calendar", "refresh_min"))
    assert set(cs.restart_for(cfg, sec, fld)) == {cs.NEWS}


def test_the_caution_names_the_fred_key_env_var_and_no_field_holds_it():
    cfg = cs.BY_NAME["news.toml"]
    assert "FRED_API_KEY" in cfg.caution
    assert not any("api_key" in f.key for s in cfg.sections for f in s.fields)


def test_a_source_user_agent_may_be_left_empty_and_stays_empty():
    """"" is a real setting (send the feed User-Agent), not an absent one."""
    cfg = cs.BY_NAME["news.toml"]
    _s, ua = cs.locate(cfg, ("calendar", "sources", "bls", "user_agent"))
    assert ua.kind == "text"
    assert cs.parse(ua, "", shipped="") == ""
    assert cs.parse(ua, "  ", shipped="Mozilla/5.0 x") == ""
    assert cs.parse(ua, " Mozilla/5.0 x ") == "Mozilla/5.0 x"
    # an ordinary text field still refuses an empty value
    with pytest.raises(ValueError, match="required"):
        cs.parse(F("t", "t", kind="text"), "")


def test_indicator_transform_and_schedule_are_choices_from_the_loader():
    from shared import news_config
    cfg = cs.BY_NAME["news.toml"]
    _s, tr = cs.locate(cfg, ("calendar", "indicators", "cpi", "transform"))
    _s, sc = cs.locate(cfg, ("calendar", "indicators", "cpi", "schedule"))
    assert tr.kind == "choice" and set(tr.choices) == set(news_config.TRANSFORMS)
    assert sc.kind == "choice" and set(sc.choices) == set(news_config.SCHEDULES)


def test_no_new_news_section_is_read_only():
    cfg = cs.BY_NAME["news.toml"]
    for path in (("impact", "high_at"), ("impact", "source_points", "WSJ"),
                 ("impact", "filings", "424B5"), ("impact", "filings", "untracked"),
                 ("calendar", "sources", "nasdaq_ipo", "accept"),
                 ("calendar", "indicators", "gdp", "match"),
                 ("collector", "sec_view_items")):
        assert cs.locate(cfg, path)[1] is not None, path
        assert not cs.is_readonly(cfg, path), path
