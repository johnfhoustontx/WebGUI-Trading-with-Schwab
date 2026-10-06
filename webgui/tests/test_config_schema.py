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


def test_rth_polling_cannot_go_below_one_minute():
    """The floor was 3 until 2026-09-26, when the operator set RTH to 2. One
    minute is the scheduler's own hard floor (``MIN_INTERVAL_S``), so the
    catalogue refuses only what the service would refuse anyway."""
    _sec, fld = cs.locate(NEWS, ("collector", "rth_poll_min"))
    assert fld.min == 1
    with pytest.raises(ValueError, match="at least 1"):
        cs.parse(fld, 0)
    assert cs.parse(fld, 2) == 2


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


def test_every_hiro_key_needs_no_restart():
    """options_svc re-reads [hiro] every 1-minute tick, so a saved change needs
    no restart -- and the page must not offer one (a restart mid-session costs
    GEX slots). The other flow_alerts.toml keys still restart options."""
    cfg = cs.BY_NAME["flow_alerts.toml"]
    hiro_fields = [(sec, f) for sec in cfg.sections for f in sec.fields
                   if f.key.startswith("hiro.")]
    assert len(hiro_fields) >= 15
    for sec, f in hiro_fields:
        assert tuple(cs.restart_for(cfg, sec, f)) == (), f.key
    sec, fld = cs.locate(cfg, ("big_delta", "top_n"))
    assert tuple(cs.restart_for(cfg, sec, fld)) == (cs.OPTIONS,)
    _, sym = cs.locate(cfg, ("hiro", "symbols"))
    assert "GEX collection list" in sym.help


def test_the_dividend_retry_restarts_trade_only_and_is_bounded():
    cfg = cs.BY_NAME["news.toml"]
    sec, fld = cs.locate(cfg, ("calendar", "dividends", "retry_min"))
    assert fld is not None and fld.kind == "int"
    assert set(cs.restart_for(cfg, sec, fld)) == {cs.TRADE}
    assert (fld.min, fld.max) == (1, 1440)


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
    # exact: same order, no duplicates (a set comparison hides both)
    assert tuple(tr.choices) == tuple(news_config.TRANSFORMS)
    assert tuple(sc.choices) == tuple(news_config.SCHEDULES)


def test_no_new_news_section_is_read_only():
    cfg = cs.BY_NAME["news.toml"]
    for path in (("impact", "high_at"), ("impact", "source_points", "WSJ"),
                 ("impact", "filings", "424B5"), ("impact", "filings", "untracked"),
                 ("calendar", "sources", "nasdaq_ipo", "accept"),
                 ("calendar", "indicators", "gdp", "match"),
                 ("collector", "sec_view_items")):
        assert cs.locate(cfg, path)[1] is not None, path
        assert not cs.is_readonly(cfg, path), path


# ── news review fixes ────────────────────────────────────────────────────────
def test_impact_med_must_be_below_high():
    """The loader drops BOTH to defaults when med_at >= high_at, silently to
    the operator - so the editor refuses the pair."""
    errs = cs.cross_check("news.toml", {("impact", "high_at"): 5,
                                        ("impact", "med_at"): 5})
    assert "Impact: High must be above Med." in errs
    assert "Impact: High must be above Med." in cs.cross_check(
        "news.toml", {("impact", "high_at"): 3, ("impact", "med_at"): 4})
    assert not cs.cross_check("news.toml", {("impact", "high_at"): 6,
                                            ("impact", "med_at"): 3})


def test_form4_bands_must_rise():
    ok = {("impact", "form4", "small_usd"): 250_000,
          ("impact", "form4", "large_usd"): 1_000_000,
          ("impact", "form4", "huge_usd"): 10_000_000}
    assert not cs.cross_check("news.toml", ok)
    for bad in ({**ok, ("impact", "form4", "large_usd"): 250_000},
                {**ok, ("impact", "form4", "huge_usd"): 500_000},
                {**ok, ("impact", "form4", "small_usd"): 20_000_000}):
        errs = cs.cross_check("news.toml", bad)
        assert any("insider" in e.lower() for e in errs), bad
    # a missing band is not checked
    assert not cs.cross_check("news.toml", {("impact", "form4", "small_usd"): 9e9,
                                            ("impact", "form4", "large_usd"): 1})


def test_the_shipped_news_file_passes_its_own_cross_checks():
    shipped = tomllib.loads((pathlib.Path(__file__).resolve().parents[2]
                             / "config" / "news.toml").read_text(encoding="utf-8"))
    assert cs.cross_check("news.toml", store.flatten(shipped)) == []


@pytest.mark.parametrize("leaf", ["url", "user_agent", "accept"])
def test_no_api_key_can_be_typed_into_a_calendar_source(leaf):
    cfg = cs.BY_NAME["news.toml"]
    _s, fld = cs.locate(cfg, ("calendar", "sources", "fred_api", leaf))
    for text in ("https://api.stlouisfed.org/fred?api_key=abc123",
                 "x API_KEY=1", "Api_Key"):
        with pytest.raises(ValueError, match="FRED_API_KEY"):
            cs.parse(fld, text)
    # a placeholder the collector fills is fine only when it is not api_key
    assert cs.parse(fld, "https://example.org/{series}") == \
        "https://example.org/{series}"


def test_the_api_key_refusal_is_only_on_calendar_sources():
    assert cs.parse(F("t", "t", kind="text"), "api_key") == "api_key"


def test_phrases_skip_items_that_are_not_strings():
    """The loader keeps only strings (news_config._tiers), so the editor skips
    everything else - ints included - rather than storing "None" or "[...]"."""
    f = F("w", "w", kind="phrases")
    assert cs.parse(f, ["FOMC", None, ["x"], {"a": 1}, True, 11, 1.5, "CPI"]) == \
        ["FOMC", "CPI"]


def test_phrases_list_items_are_split_on_commas_too():
    f = F("w", "w", kind="phrases")
    assert cs.parse(f, ["rate cut, FOMC", "Fed chair ,, rate cut"]) == \
        ["rate cut", "FOMC", "Fed chair"]


def test_indicator_match_help_says_it_is_a_trimmed_prefix():
    _s, fld = cs.locate(cs.BY_NAME["news.toml"],
                        ("calendar", "indicators", "gdp", "match"))
    assert "prefix" in fld.help and "trimmed" in fld.help


def test_marketdata_mode_and_today_bar_are_choices_from_the_loader():
    from shared import marketdata_config
    cfg = cs.BY_NAME["marketdata.toml"]
    _s, mode = cs.locate(cfg, ("mode",))
    _s, bar = cs.locate(cfg, ("bars", "today_bar"))
    # exact: same order, no duplicates (a set comparison hides both)
    assert mode.kind == "choice"
    assert tuple(mode.choices) == tuple(marketdata_config.MODES)
    assert bar.kind == "choice"
    assert tuple(bar.choices) == tuple(marketdata_config.TODAY_BARS)


def test_the_futures_roll_offset_is_bounded_live_and_matches_the_loader():
    """The Macro Board's /ES and /NQ tiles switch contract this many days before
    expiry. market_svc works the contract out on every poll, so a saved change
    needs no restart; the bounds are the loader's own, so the editor cannot
    accept a value the service would then throw away."""
    from shared import symbols as shared_symbols
    cfg = cs.BY_NAME["symbols.toml"]
    sec, roll = cs.locate(cfg, ("futures", "roll_days_before_expiry"))
    assert roll.kind == "int" and roll.unit == "days"
    assert (roll.min, roll.max) == (0, shared_symbols.FUTURES_ROLL_DAYS_MAX)
    assert tuple(cs.restart_for(cfg, sec, roll)) == ()
    assert cs.parse(roll, 8) == 8
    with pytest.raises(ValueError, match="at most"):
        cs.parse(roll, shared_symbols.FUTURES_ROLL_DAYS_MAX + 1)
    assert _shipped("symbols.toml")["futures"]["roll_days_before_expiry"] == 8
    # the rest of the file still restarts the services that cache it at import
    sec, base = cs.locate(cfg, ("collection", "base"))
    assert set(cs.restart_for(cfg, sec, base)) == {cs.OPTIONS, cs.MARKET, cs.WEBGUI}


def test_the_collector_settings_are_bounded_and_say_why():
    cfg = cs.BY_NAME["marketdata.toml"]
    _s, fresh = cs.locate(cfg, ("collection", "fresh_max_age_sec"))
    _s, interval = cs.locate(cfg, ("collection", "tail_interval_min"))
    # One poll interval (60 s) less the 30 s slack: above it a one-minute
    # symbol is answered with the previous minute's chain and treated as new.
    assert (fresh.min, fresh.max) == (0, 30)
    assert "carried" in fresh.help and "every minute" in fresh.help
    with pytest.raises(ValueError, match="at most 30"):
        cs.parse(fresh, 31)
    # The Opportunity Board's flow acceleration reads a 15-minute window.
    assert (interval.min, interval.max) == (1, 5)
    assert "15-minute" in interval.help and "3 or 5" in interval.help
    with pytest.raises(ValueError, match="at most 5"):
        cs.parse(interval, 6)


def test_the_carrys_two_limits_are_in_the_catalogue():
    cfg = cs.BY_NAME["marketdata.toml"]
    _s, ratio = cs.locate(cfg, ("collection", "max_gamma_ratio"))
    _s, slack = cs.locate(cfg, ("collection", "carry_slack_sec"))
    assert ratio.kind == "float" and ratio.min == 1 and ratio.label
    assert "gamma" in ratio.help and "between fetches" in ratio.help
    with pytest.raises(ValueError, match="at least 1"):
        cs.parse(ratio, 0.5)                       # under 1 it would shrink gammas
    assert slack.kind == "int" and (slack.min, slack.max) == (0, 60)
    assert "added" in slack.help and "stored chain" in slack.help


def test_the_fresh_limit_and_the_slack_must_fit_in_one_poll_minute():
    """The collector clamps the fresh limit to 60 seconds less the slack, so a
    pair that does not fit would be saved and then quietly not used."""
    key = lambda k: ("collection", k)  # noqa: E731
    assert not cs.cross_check("marketdata.toml", {key("fresh_max_age_sec"): 20,
                                                  key("carry_slack_sec"): 30})
    assert not cs.cross_check("marketdata.toml", {key("fresh_max_age_sec"): 30,
                                                  key("carry_slack_sec"): 30})
    errs = cs.cross_check("marketdata.toml", {key("fresh_max_age_sec"): 30,
                                              key("carry_slack_sec"): 45})
    assert len(errs) == 1 and "60 seconds" in errs[0]
    assert not cs.cross_check("marketdata.toml", {key("carry_slack_sec"): 45})


def test_the_shipped_marketdata_file_passes_its_own_cross_checks():
    assert cs.cross_check("marketdata.toml",
                          store.flatten(_shipped("marketdata.toml"))) == []


def test_the_market_data_age_limits_stop_where_the_loader_stops():
    """``shared.marketdata_config.AGE_CEILINGS`` clamps these whatever the file
    says (audit AC-104). The form must not offer a value the loader will not
    use: it allowed 24 hours for a closed-market chain."""
    from shared import marketdata_config as mc
    cfg = next(c for c in cs.EDITABLE if c.name == "marketdata.toml")
    fields = {f.key: f for sec in cfg.sections for f in sec.fields}
    for table, keys in mc.AGE_CEILINGS.items():
        for key, ceiling in keys.items():
            assert fields[f"{table}.{key}"].max == ceiling, (table, key)


# ── the site Blog — config/blog.toml ─────────────────────────────────────────

def test_the_blog_numbers_are_bounded_exactly_as_the_loader_bounds_them():
    """``shared.blog_inbox.BOUNDS`` is what the validators enforce: a value
    outside it reads as the shipped one, with no sign on this page. So the form
    must offer exactly that range - not a wider one (a saved value quietly not
    used) and not a narrower one (a value the service honours, refused here).
    The catalogue stays import-free, so the numbers are mirrored and pinned."""
    from shared import blog_inbox
    cfg = cs.BY_NAME["blog.toml"]
    fields = {tuple(cs.split_key(f.key)): f
              for sec in cfg.sections for f in sec.fields}
    for path, (low, high) in blog_inbox.BOUNDS.items():
        fld = fields[path]
        assert fld.kind == "int", path
        assert (fld.min, fld.max) == (low, high), path
    # ...and nothing numeric is offered here that the loader does not bound.
    numeric = {p for p, f in fields.items() if f.kind in ("int", "float", "money")}
    assert numeric == set(blog_inbox.BOUNDS)


def test_a_blog_value_at_either_bound_is_one_the_loader_uses(monkeypatch):
    """End to end over every number: what the form accepts at its lowest and
    its highest is what the service then reads back - never the shipped value
    in its place."""
    from shared import blog_inbox
    cfg = cs.BY_NAME["blog.toml"]
    read = {"site": blog_inbox.site, "limits": blog_inbox.limits,
            "fonts": blog_inbox.fonts}
    for (table, key), bounds in blog_inbox.BOUNDS.items():
        _sec, fld = cs.locate(cfg, (table, key))
        for edge in bounds:
            saved = cs.parse(fld, edge, shipped=blog_inbox.DEFAULTS[table][key])
            monkeypatch.setattr(blog_inbox, "load",
                                lambda t=table, k=key, v=saved: {t: {k: v}})
            assert read[table]()[key] == edge, (table, key, edge)
        for outside in (bounds[0] - 1, bounds[1] + 1):
            with pytest.raises(ValueError):
                cs.parse(fld, outside)


def test_the_blog_file_restarts_the_blog_service_and_nothing_else():
    cfg = cs.BY_NAME["blog.toml"]
    assert cs.BLOG == "blog_svc"
    assert cs.RESTART_LABELS[cs.BLOG] == "Blog service"
    assert cfg.editable and cfg in cs.EDITABLE
    for sec in cfg.sections:
        for f in sec.fields:
            assert tuple(cs.restart_for(cfg, sec, f)) == (cs.BLOG,), f.key
    assert "blog.toml" not in cs.NOT_HERE


def test_the_blog_typeface_subsets_keep_their_lower_case(monkeypatch):
    """Google names a subset in lower case ("latin-ext") and the loader keeps
    only names spelled that way. Typed as tickers they would be upper-cased on
    save, every one refused, and the shipped list used with no sign of it."""
    from shared import blog_inbox
    cfg = cs.BY_NAME["blog.toml"]
    _sec, fld = cs.locate(cfg, ("fonts", "subsets"))
    saved = cs.parse(fld, "cyrillic, latin-ext")
    assert saved == ["cyrillic", "latin-ext"]
    assert cs.parse(fld, ["latin", "latin-ext"]) == ["latin", "latin-ext"]
    monkeypatch.setattr(blog_inbox, "load", lambda: {"fonts": {"subsets": saved}})
    assert blog_inbox.fonts()["subsets"] == saved


# ── how much of each command queue is kept — config/services.toml ────────────

def test_the_queue_caps_are_catalogued_and_bounded_as_the_loader_bounds_them():
    """``shared.service_limits.stream_keep`` reads a cap outside the stream's
    own bounds as the fallback, so the form must offer that range and no other:
    10..100000 for a queue of ordinary commands, 10..500 for the two whose
    commands carry a whole document. Catalogue and loader are pinned against
    each other, per stream, and then run end to end."""
    from shared import service_limits as sl
    cfg = cs.BY_NAME["services.toml"]
    shipped = _shipped("services.toml")["stream_keep"]
    assert shipped == sl.DEFAULTS["stream_keep"]
    for name in shipped:
        _sec, fld = cs.locate(cfg, ("stream_keep", name))
        assert fld is not None and "*" not in fld.key, name    # its OWN entry
        assert fld.label and fld.help, name
        assert fld.kind == "int"
        low, high = sl.stream_keep_bounds(name)
        assert (fld.min, fld.max) == (low, high), name
        for outside in (low - 1, high + 1):
            with pytest.raises(ValueError):
                cs.parse(fld, outside)
    assert sl.stream_keep_bounds("default") == (10, 100000)
    assert sl.stream_keep_bounds("cmd:blog") == (10, 500)
    assert sl.stream_keep_bounds("cmd:blog_inbox") == (10, 500)


def test_a_queue_cap_at_either_bound_is_one_the_loader_uses(monkeypatch):
    """What the form accepts at its lowest and highest for each queue is what
    the bus then asks Redis for - never the fallback in its place."""
    from shared import service_limits as sl
    cfg = cs.BY_NAME["services.toml"]
    for name, stream in (("default", "cmd:options"), ("cmd:blog", "cmd:blog"),
                         ("cmd:blog_inbox", "cmd:blog_inbox"),
                         ("cmd:options", "cmd:options")):
        _sec, fld = cs.locate(cfg, ("stream_keep", name))
        for edge in (fld.min, fld.max):
            saved = cs.parse(fld, edge, shipped=1000)
            monkeypatch.setattr(sl, "load",
                                lambda n=name, v=saved: {"stream_keep": {n: v}})
            assert sl.stream_keep(stream) == edge, (name, edge)


def test_a_queue_someone_names_by_hand_is_still_shown_and_bounded():
    """Any stream may be given a cap in config/local. One with no catalogue
    entry would be a setting in force that this page never shows."""
    from shared import service_limits as sl
    cfg = cs.BY_NAME["services.toml"]
    sec, fld = cs.locate(cfg, ("stream_keep", "cmd:options"))
    assert fld is not None and fld.key == "stream_keep.*"
    assert fld.kind == "int"
    assert (fld.min, fld.max) == (sl.STREAM_KEEP_MIN, sl.STREAM_KEEP_MAX)
    assert cs.parse(fld, 250) == 250
    # ...and the shipped names keep their own entries, with their own words.
    _s, default = cs.locate(cfg, ("stream_keep", "default"))
    _s, blog = cs.locate(cfg, ("stream_keep", "cmd:blog"))
    _s, inbox = cs.locate(cfg, ("stream_keep", "cmd:blog_inbox"))
    assert len({default.key, blog.key, inbox.key, fld.key}) == 4


def test_a_queue_cap_needs_no_restart_and_the_rest_of_the_file_still_does():
    """The cap is read at every enqueue (shared/bus/tests pins that), by
    whichever process is sending. Offering a restart here would bounce every
    service for a change that had already taken effect."""
    cfg = cs.BY_NAME["services.toml"]
    for name in ("default", "cmd:blog", "cmd:blog_inbox", "cmd:options"):
        sec, fld = cs.locate(cfg, ("stream_keep", name))
        assert tuple(cs.restart_for(cfg, sec, fld)) == (), name
    sec, fld = cs.locate(cfg, ("dead_letters", "keep"))
    assert tuple(cs.restart_for(cfg, sec, fld)) == tuple(cfg.restart) != ()


def test_the_document_queues_say_why_they_are_small():
    cfg = cs.BY_NAME["services.toml"]
    for name in ("cmd:blog", "cmd:blog_inbox"):
        _sec, fld = cs.locate(cfg, ("stream_keep", name))
        assert "document" in fld.help, name
        assert "cmd:" not in fld.label, "a label is words, not a stream name"


def test_a_queue_cap_override_round_trips_through_the_writer(tmp_path):
    """The key has a colon in it, so the writer must quote it. What is written
    is what the loader reads back, and only the changed cap is written."""
    from shared import service_limits as sl
    from shared.config_toml import toml_loader
    shipped = _shipped("services.toml")
    values = store.flatten(shipped)
    values[("stream_keep", "cmd:blog")] = 20
    values[("stream_keep", "cmd:options")] = 300          # named by hand
    over = store.build_overrides(shipped, values)
    assert over == {"stream_keep": {"cmd:blog": 20, "cmd:options": 300}}
    text = config_toml.dumps(over)
    assert '"cmd:blog" = 20' in text
    (tmp_path / "services.toml").write_text(
        config_toml.dumps(store.effective(shipped, over)), encoding="utf-8")
    load, _reset = toml_loader(tmp_path / "services.toml", sl.DEFAULTS)
    table = load()["stream_keep"]
    assert (table["cmd:blog"], table["cmd:options"], table["cmd:blog_inbox"],
            table["default"]) == (20, 300, 50, 1000)


def test_the_dead_letter_field_limit_is_catalogued_and_bounded_as_the_loader():
    """``shared.service_limits.dead_letter_field_kb`` reads a value outside
    1..4096 as the shipped one. It is read each time a command is kept, so a
    change needs no restart and the page must not offer one."""
    from shared import service_limits as sl
    cfg = cs.BY_NAME["services.toml"]
    sec, fld = cs.locate(cfg, ("dead_letters", "max_field_kb"))
    assert fld is not None and fld.label and fld.help
    assert fld.kind == "int" and fld.unit == "KB"
    assert (fld.min, fld.max) == (sl.DEAD_FIELD_KB_MIN, sl.DEAD_FIELD_KB_MAX)
    assert tuple(cs.restart_for(cfg, sec, fld)) == ()
    assert _shipped("services.toml")["dead_letters"]["max_field_kb"] == \
        sl.DEFAULTS["dead_letters"]["max_field_kb"] == 64
    for outside in (sl.DEAD_FIELD_KB_MIN - 1, sl.DEAD_FIELD_KB_MAX + 1):
        with pytest.raises(ValueError):
            cs.parse(fld, outside)


def test_the_blog_page_calls_an_address_an_address():
    """Labels are written from the reader's side: the operator sees "Address"
    on the Blog page, never the developer's word for it."""
    cfg = cs.BY_NAME["blog.toml"]
    for sec in cfg.sections:
        for f in sec.fields:
            assert "slug" not in f.label.lower(), f.key
            assert "slug" not in f.help.lower(), f.key
            assert f.help, f"{f.key} has no help"
