"""shared.news_config: the news collector's feeds, cadence and ticker extras."""
import ast
import copy
import pathlib
import subprocess
import sys
import tomllib

from shared import config_toml
from shared import news_config as nc

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_defaults_are_the_real_values():
    cfg = nc.load()
    assert cfg["collector"]["rth_poll_min"] == 5
    assert cfg["collector"]["view_items"] == 300
    assert cfg["trending"]["window_h"] == 6


def test_the_collector_carries_a_feed_user_agent_and_a_body_cap():
    col = nc.DEFAULTS["collector"]
    assert col["feed_user_agent"].startswith("Mozilla/5.0")
    assert col["feed_user_agent"] != col["sec_user_agent"]
    assert col["max_body_bytes"] == 5_000_000


def test_defaults_match_the_shipped_file():
    """The built-in DEFAULTS and the tracked TOML are two copies of the same
    values; pin them together so an edit to one cannot silently drift."""
    with open(nc.NEWS_TOML, "rb") as fh:
        shipped = tomllib.load(fh)
    assert nc.DEFAULTS["collector"] == shipped["collector"]
    assert nc.DEFAULTS["trending"] == shipped["trending"]
    assert nc.DEFAULTS["dedupe"] == shipped["dedupe"]


def test_same_feed_merge_h_reads_the_shipped_file():
    assert nc.DEFAULTS["dedupe"]["same_feed_merge_h"] == 6
    assert nc.same_feed_merge_h() == 6
    assert nc.same_feed_merge_h(nc.load()) == 6


def test_same_feed_merge_h_takes_a_real_number_including_zero(monkeypatch):
    for value in (0, 2, 1.5, 24):
        monkeypatch.setattr(nc, "load", lambda v=value: {"dedupe": {"same_feed_merge_h": v}})
        assert nc.same_feed_merge_h() == value


def test_same_feed_merge_h_is_clamped_to_the_schema_range():
    """The Settings field is 0-24; a title match needs a gap under a day
    anyway, so a hand-edited 48 means 24, and the reader says so."""
    for value, expected in ((48, 24), (24.5, 24), (1e9, 24), (24, 24), (0, 0)):
        assert nc.same_feed_merge_h({"dedupe": {"same_feed_merge_h": value}}) == expected, value


def test_a_bad_same_feed_merge_h_is_the_default(monkeypatch):
    for bad in (True, False, "6", None, -1, -0.5, float("nan"), float("inf"), [6], {"h": 6}):
        cfg = {"dedupe": {"same_feed_merge_h": bad}}
        assert nc.same_feed_merge_h(cfg) == 6, bad
    for cfg in ({}, {"dedupe": 6}, {"dedupe": None}, None, "x"):
        monkeypatch.setattr(nc, "load", lambda c=cfg: c)
        assert nc.same_feed_merge_h() == 6, cfg


def test_a_local_override_of_same_feed_merge_h_round_trips(tmp_path, monkeypatch):
    base = tmp_path / "news.toml"
    base.write_text((ROOT / "config" / "news.toml").read_text(encoding="utf-8"),
                    encoding="utf-8")
    local = tmp_path / "local"
    local.mkdir()
    (local / "news.toml").write_text("[dedupe]\nsame_feed_merge_h = 0\n", encoding="utf-8")
    monkeypatch.setenv("TRADING_CONFIG_OVERRIDES_IN_TESTS", "1")
    load, _reset = config_toml.toml_loader(base, nc.DEFAULTS, label="news.toml")
    monkeypatch.setattr(nc, "load", load)
    assert nc.same_feed_merge_h() == 0


def test_feeds_returns_exactly_the_shipped_enabled_feeds_in_order():
    """Non-vacuous: the shape test above passes on an empty list. The switch
    lives in ``[feed_flags."<name>"]`` (it moved out of ``[[feeds]]``)."""
    flags = _shipped()["feed_flags"]
    expected = [f["name"] for f in _shipped_feeds() if flags[f["name"]]["enabled"]]
    assert expected and len(expected) < len(_shipped_feeds())
    assert [f["name"] for f in nc.feeds()] == expected


def test_every_shipped_feed_has_a_name_kind_and_flags():
    for feed in nc.feeds():
        assert feed["name"] and feed["kind"] in nc.KINDS, feed
        assert isinstance(feed["enabled"], bool)
        assert isinstance(feed["public"], bool)


def test_disabled_feeds_are_not_returned(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: {
        "feeds": [{"name": "A", "kind": "rss", "url": "x", "enabled": False},
                  {"name": "B", "kind": "rss", "url": "y"}]})
    assert [f["name"] for f in nc.feeds()] == ["B"]


def test_an_unknown_kind_is_dropped_not_raised(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: {
        "feeds": [{"name": "A", "kind": "carrier_pigeon", "url": "x"}]})
    assert nc.feeds() == []


def test_ticker_set_is_the_collection_list_plus_extras(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: {"tickers": {"extras": ["zzz", "SPY", "bad symbol"]}})
    monkeypatch.setattr(nc._symbols, "collection_base", lambda: ["SPY", "QQQ"])
    assert nc.ticker_set() == ["SPY", "QQQ", "ZZZ"]


def test_ticker_set_survives_a_config_with_no_tickers_table(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: {"feeds": []})
    monkeypatch.setattr(nc._symbols, "collection_base", lambda: ["SPY"])
    assert nc.ticker_set() == ["SPY"]


def test_feeds_does_not_mutate_the_loaded_config(monkeypatch):
    """load() hands back the CACHED mapping - feeds() must fill its flags on a copy."""
    raw = {"feeds": [{"name": "B", "kind": "rss", "url": "y"}]}
    monkeypatch.setattr(nc, "load", lambda: raw)
    nc.feeds()
    assert raw == {"feeds": [{"name": "B", "kind": "rss", "url": "y"}]}


# ── malformed config fails closed, and says so ─────────────────────────────

def test_feeds_written_as_a_table_fall_back_to_none_with_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: {
        "feeds": {"name": "A", "kind": "rss", "url": "x"}})
    with caplog.at_level("WARNING", logger=nc.__name__):
        assert nc.feeds() == []
    assert "not a list" in caplog.text


def test_extras_as_a_bare_string_is_not_split_into_letters(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: {"tickers": {"extras": "NVDA"}})
    monkeypatch.setattr(nc._symbols, "collection_base", lambda: ["SPY"])
    with caplog.at_level("WARNING", logger=nc.__name__):
        assert nc.ticker_set() == ["SPY"]
    assert "not a list" in caplog.text


def test_a_non_bool_public_fails_closed(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: {"feeds": [
        {"name": "A", "kind": "rss", "url": "x", "public": "false"},
        {"name": "B", "kind": "rss", "url": "y", "public": 1},
        {"name": "C", "kind": "rss", "url": "z", "public": True}]})
    with caplog.at_level("WARNING", logger=nc.__name__):
        got = {f["name"]: f["public"] for f in nc.feeds()}
    assert got == {"A": False, "B": False, "C": True}
    assert "'A'" in caplog.text and "'B'" in caplog.text


def test_a_non_bool_enabled_is_treated_as_disabled(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: {"feeds": [
        {"name": "A", "kind": "rss", "url": "x", "enabled": "true"},
        {"name": "B", "kind": "rss", "url": "y", "enabled": 1},
        {"name": "C", "kind": "rss", "url": "z"}]})
    with caplog.at_level("WARNING", logger=nc.__name__):
        assert [f["name"] for f in nc.feeds()] == ["C"]
    assert "'A'" in caplog.text and "'B'" in caplog.text


def test_an_empty_feed_list_leaves_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: {"feeds": []})
    with caplog.at_level("WARNING", logger=nc.__name__):
        assert nc.feeds() == []
    assert "no enabled feeds" in caplog.text


def test_a_duplicate_feed_name_keeps_the_first(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: {"feeds": [
        {"name": "A", "kind": "rss", "url": "first"},
        {"name": "B", "kind": "rss", "url": "y"},
        {"name": "A", "kind": "rss", "url": "second"}]})
    with caplog.at_level("WARNING", logger=nc.__name__):
        got = nc.feeds()
    assert [(f["name"], f["url"]) for f in got] == [("A", "first"), ("B", "y")]
    assert "duplicate" in caplog.text


def test_a_duplicate_warning_says_when_the_kept_entry_is_disabled(monkeypatch, caplog):
    """An operator who "enabled the duplicate" must learn why nothing happens:
    the name keys the switch, and the FIRST entry is the one kept."""
    monkeypatch.setattr(nc, "load", lambda: {
        "feeds": [{"name": "A", "kind": "rss", "url": "first"},
                  {"name": "A", "kind": "rss", "url": "second"}],
        "feed_flags": {"A": {"enabled": False}}})
    with caplog.at_level("WARNING", logger=nc.__name__):
        assert nc.feeds() == []
    dup = [r.getMessage() for r in caplog.records if "duplicate" in r.getMessage()]
    assert dup and "disabled" in dup[0], caplog.text


def test_a_duplicate_warning_does_not_claim_disabled_when_the_kept_one_runs(
        monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: {
        "feeds": [{"name": "A", "kind": "rss", "url": "first"},
                  {"name": "A", "kind": "rss", "url": "second"}]})
    with caplog.at_level("WARNING", logger=nc.__name__):
        nc.feeds()
    dup = [r.getMessage() for r in caplog.records if "duplicate" in r.getMessage()]
    assert dup and "disabled" not in dup[0]


# ── [feed_flags."<name>"]: the per-feed switches ───────────────────────────

def _cfg(feeds, flags=None):
    out = {"feeds": feeds}
    if flags is not None:
        out["feed_flags"] = flags
    return out


def test_defaults_carry_an_empty_flag_table():
    assert nc.DEFAULTS["feed_flags"] == {}


def test_feed_flags_switch_a_feed_off_and_hide_it_from_the_public(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x"},
         {"name": "B", "kind": "rss", "url": "y"},
         {"name": "C", "kind": "rss", "url": "z"}],
        {"A": {"enabled": False, "public": True}, "B": {"public": False}}))
    got = {f["name"]: (f["enabled"], f["public"]) for f in nc.feeds()}
    assert got == {"B": (True, False), "C": (True, True)}


def test_a_flag_absent_from_feed_flags_defaults_true(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x"}], {"A": {}}))
    (feed,) = nc.feeds()
    assert feed["enabled"] is True and feed["public"] is True


def test_a_non_bool_flag_in_feed_flags_fails_closed(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x"},
         {"name": "B", "kind": "rss", "url": "y"},
         {"name": "C", "kind": "rss", "url": "z"}],
        {"A": {"enabled": "true"}, "B": {"public": 1}, "C": {"public": True}}))
    with caplog.at_level("WARNING", logger=nc.__name__):
        got = {f["name"]: f["public"] for f in nc.feeds()}
    assert got == {"B": False, "C": True}          # A: enabled "true" -> disabled
    assert "'A'" in caplog.text and "'B'" in caplog.text


def test_a_legacy_flag_inside_feeds_warns_and_is_only_a_fallback(monkeypatch, caplog):
    """A [[feeds]] entry still carrying ``enabled`` / ``public`` is honoured only
    where [feed_flags] is silent - so a legacy ``enabled = false`` still fails
    closed - and the operator is told the switch moved."""
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x", "enabled": False},
         {"name": "B", "kind": "rss", "url": "y", "public": False},
         {"name": "C", "kind": "rss", "url": "z", "public": False}],
        {"C": {"public": True}}))
    with caplog.at_level("WARNING", logger=nc.__name__):
        got = {f["name"]: f["public"] for f in nc.feeds()}
    assert got == {"B": False, "C": True}
    moved = [r.getMessage() for r in caplog.records if "feed_flags" in r.getMessage()]
    assert any("'A'" in m for m in moved) and any("'B'" in m for m in moved), moved


def test_a_legacy_non_bool_flag_still_fails_closed(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x", "enabled": "yes"}]))
    assert nc.feeds() == []


def test_a_feed_flags_entry_naming_no_feed_is_reported(monkeypatch, caplog):
    """A typo in the name would otherwise switch nothing, silently."""
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "MarketWatch", "kind": "rss", "url": "x"}],
        {"Marketwatch": {"enabled": False}}))
    with caplog.at_level("WARNING", logger=nc.__name__):
        assert [f["name"] for f in nc.feeds()] == ["MarketWatch"]
    assert "'Marketwatch'" in caplog.text and "no feed" in caplog.text


def test_a_feed_flags_value_that_is_not_a_table_is_ignored(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x"}], {"A": False}))
    with caplog.at_level("WARNING", logger=nc.__name__):
        (feed,) = nc.feeds()
    assert feed["enabled"] is True and feed["public"] is True
    assert "'A'" in caplog.text and "not a table" in caplog.text


def test_feed_flags_itself_not_a_table_is_ignored(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x"}], ["A"]))
    with caplog.at_level("WARNING", logger=nc.__name__):
        assert [f["name"] for f in nc.feeds()] == ["A"]
    assert "[feed_flags]" in caplog.text and "not a table" in caplog.text


def test_feeds_is_a_deep_copy_with_real_bool_flags(monkeypatch):
    raw = _cfg([{"name": "A", "kind": "edgar_filings", "forms": ["S-3"]}],
               {"A": {"public": False}})
    snapshot = copy.deepcopy(raw)
    monkeypatch.setattr(nc, "load", lambda: raw)
    (feed,) = nc.feeds()
    assert feed["enabled"] is True and feed["public"] is False
    feed["forms"].append("S-1")
    assert raw == snapshot


def test_a_local_override_of_one_flag_keeps_every_feed(tmp_path, monkeypatch):
    """The point of the move: a list in config/local REPLACES the shipped list,
    a table merges key by key - so switching one feed off in the override keeps
    every other feed and every other flag."""
    monkeypatch.setenv("TRADING_CONFIG_OVERRIDES_IN_TESTS", "1")
    shipped = tmp_path / "news.toml"
    shipped.write_text(
        '[feed_flags."SEC Insider Buys"]\nenabled = true\npublic = true\n'
        '[feed_flags."Federal Reserve"]\nenabled = true\npublic = true\n'
        '[[feeds]]\nname = "SEC Insider Buys"\nkind = "edgar_form4"\n'
        '[[feeds]]\nname = "Federal Reserve"\nkind = "rss"\nurl = "https://f"\n',
        encoding="utf-8")
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "news.toml").write_text(
        '[feed_flags."SEC Insider Buys"]\npublic = false\n', encoding="utf-8")
    load, _reset = config_toml.toml_loader(shipped, nc.DEFAULTS)
    monkeypatch.setattr(nc, "load", load)
    got = {f["name"]: (f["enabled"], f["public"]) for f in nc.feeds()}
    assert got == {"SEC Insider Buys": (True, False), "Federal Reserve": (True, True)}


# ── flags(name): the per-publish re-check ──────────────────────────────────

def test_flags_reads_the_current_switches_for_one_feed(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x"},
         {"name": "B", "kind": "rss", "url": "y"}],
        {"A": {"public": False}, "B": {"enabled": False}}))
    assert nc.flags("A") == {"enabled": True, "public": False}
    assert nc.flags("B") == {"enabled": False, "public": True}


def test_flags_fails_closed_on_an_unknown_or_unusable_feed(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "carrier_pigeon", "url": "x"},
         {"name": "B", "kind": "rss", "url": "y"}],
        {"B": {"public": "no"}}))
    closed = {"enabled": False, "public": False}
    assert nc.flags("nope") == closed
    assert nc.flags("A") == closed                 # a feed feeds() would skip
    assert nc.flags("B") == {"enabled": True, "public": False}


def test_flags_follows_the_first_of_two_duplicates(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x", "enabled": False},
         {"name": "A", "kind": "rss", "url": "y"}]))
    assert nc.flags("A")["enabled"] is False


def test_flags_agrees_with_feeds_on_the_shipped_file():
    by_name = {f["name"]: f for f in nc.feeds()}
    for feed in _shipped_feeds():
        got = nc.flags(feed["name"])
        assert set(got) == {"enabled", "public"}
        if feed["name"] in by_name:
            assert got == {"enabled": True, "public": by_name[feed["name"]]["public"]}
        else:
            assert got["enabled"] is False


# Every way a config can make feeds() warn, split across configs where two
# shapes cannot coexist (a [feed_flags] that is not a table has no entries).
_MALFORMED = [
    _cfg([{"name": "A", "kind": "rss", "url": "x", "enabled": True},      # legacy
          {"name": "B", "kind": "rss", "url": "y", "public": False},      # legacy
          {"name": "C", "kind": "rss", "url": "z"},
          {"name": "D", "kind": "carrier_pigeon", "url": "w"},            # bad kind
          {"name": "C", "kind": "rss", "url": "dup"},                     # duplicate
          {"name": "E", "kind": "carrier_pigeon", "url": "v"},            # bad kind,
          {"name": "E", "kind": "rss", "url": "kept"},                    # then kept
          {"kind": "rss", "url": "nameless"},
          "not a table"],
         {"A": {"enabled": "yes"}, "C": {"public": 1},                    # non-bool
          "B": False,                                                     # non-table
          "Typo": {"enabled": False}}),                                   # names none
    _cfg([{"name": "A", "kind": "rss", "url": "x", "public": "no"}], ["A"]),
    _cfg({"name": "A", "kind": "rss", "url": "x"}),                       # not a list
]


def test_flags_is_silent_on_a_malformed_config_while_feeds_still_warns(
        monkeypatch, caplog):
    """The poll cycle calls flags() at every publish, so a warning there would
    repeat once per call - one leftover legacy switch flooding the journal.
    feeds() is the one place a malformed config is reported."""
    for cfg in _MALFORMED:
        monkeypatch.setattr(nc, "load", lambda cfg=cfg: cfg)
        caplog.clear()
        with caplog.at_level("DEBUG", logger=nc.__name__):
            for _ in range(10):
                for name in ("A", "B", "C", "D", "E", "Typo", "nope", None, ""):
                    nc.flags(name)
        assert not caplog.records, caplog.text
        with caplog.at_level("WARNING", logger=nc.__name__):
            nc.feeds()
        assert caplog.records, cfg


def test_flags_resolves_the_same_values_it_did_while_it_warned(monkeypatch):
    """Silence changes the logging only: the malformed values still fail closed."""
    monkeypatch.setattr(nc, "load", lambda: _MALFORMED[0])
    assert nc.flags("A") == {"enabled": False, "public": True}   # "yes" -> False
    assert nc.flags("B") == {"enabled": True, "public": False}   # legacy fallback
    assert nc.flags("C") == {"enabled": True, "public": False}   # 1 -> False
    assert nc.flags("D") == {"enabled": False, "public": False}  # unknown kind
    assert nc.flags("E") == {"enabled": True, "public": True}    # the later entry
    assert nc.flags("Typo") == {"enabled": False, "public": False}


def test_flags_agrees_with_feeds_when_a_duplicates_first_entry_has_an_unknown_kind(
        monkeypatch):
    """feeds() skips the unknown-kind entry before recording its name, so the
    SECOND entry is the one kept and polled - flags() must report that one,
    not stop at the first match and fail closed on a feed that is running."""
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "carrier_pigeon", "url": "first"},
         {"name": "A", "kind": "rss", "url": "second"}],
        {"A": {"public": False}}))
    (feed,) = nc.feeds()
    assert feed["url"] == "second"
    assert nc.flags("A") == {"enabled": True, "public": False}


def _reference_flags(cfg):
    """The rules feeds() documents, restated independently: the FIRST table
    entry of a name with a known kind wins; each flag comes from
    [feed_flags."<name>"] (when that entry is a table), else a legacy copy in
    the [[feeds]] entry, else True; anything but a real bool is False."""
    table = cfg.get("feed_flags")
    table = table if isinstance(table, dict) else {}
    feeds = cfg.get("feeds")
    out = {}
    for raw in feeds if isinstance(feeds, list) else []:
        if not isinstance(raw, dict) or raw.get("kind") not in nc.KINDS:
            continue
        name = raw.get("name")
        if not name or name in out:
            continue
        entry = table.get(name)
        entry = entry if isinstance(entry, dict) else {}
        got = {}
        for key in ("enabled", "public"):
            value = entry[key] if key in entry else raw.get(key, True)
            got[key] = value if isinstance(value, bool) else False
        out[name] = got
    return out


def test_flags_equals_the_resolved_feed_for_every_shipped_name_disabled_included():
    cfg = nc.load()
    names = {f["name"] for f in _shipped_feeds()}
    want = _reference_flags(cfg)
    assert set(want) == names
    assert any(not w["enabled"] for w in want.values())    # a disabled one is covered
    running = {f["name"]: f for f in nc.feeds()}
    for name in names:
        assert nc.flags(name) == want[name], name
        if want[name]["enabled"]:
            assert running[name]["public"] == want[name]["public"], name
        else:
            assert name not in running, name


def test_flags_equals_the_resolved_feed_on_every_malformed_config(monkeypatch):
    for cfg in _MALFORMED:
        monkeypatch.setattr(nc, "load", lambda cfg=cfg: cfg)
        want = _reference_flags(cfg)
        running = {f["name"]: f for f in nc.feeds()}
        assert {n for n, w in want.items() if w["enabled"]} == set(running)
        for name in set(want) | {"nope", "Typo", "D"}:
            closed = {"enabled": False, "public": False}
            assert nc.flags(name) == want.get(name, closed), (cfg, name)


# ── a name that is not a non-empty str is no name ──────────────────────────

_BAD_NAMES = [["A"], {"n": "A"}, 7, True, 0, ""]


def test_a_feed_whose_name_is_not_a_string_is_nameless_and_nothing_raises(
        monkeypatch, caplog):
    """A list or table name is unhashable - keying the switch by it raised."""
    for bad in _BAD_NAMES:
        monkeypatch.setattr(nc, "load", lambda bad=bad: _cfg(
            [{"name": bad, "kind": "rss", "url": "x"},
             {"name": "B", "kind": "rss", "url": "y"}]))
        caplog.clear()
        with caplog.at_level("WARNING", logger=nc.__name__):
            assert [f["name"] for f in nc.feeds()] == ["B"], bad
        assert "has no name" in caplog.text, bad


def test_flags_treats_a_non_string_name_as_no_feed_silently(monkeypatch, caplog):
    closed = {"enabled": False, "public": False}
    for bad in _BAD_NAMES:
        monkeypatch.setattr(nc, "load", lambda bad=bad: _cfg(
            [{"name": bad, "kind": "rss", "url": "x"},
             {"name": "B", "kind": "rss", "url": "y"}]))
        caplog.clear()
        with caplog.at_level("DEBUG", logger=nc.__name__):
            assert nc.flags(bad) == closed, bad
            assert nc.flags("B") == {"enabled": True, "public": True}, bad
        assert not caplog.records, caplog.text


def test_a_non_string_key_in_feed_flags_is_ignored_with_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x"}],
        {7: {"enabled": False}, ("A",): {"public": False}, "A": {"public": False}}))
    with caplog.at_level("WARNING", logger=nc.__name__):
        (feed,) = nc.feeds()
    assert (feed["enabled"], feed["public"]) == (True, False)
    assert "7" in caplog.text and "not a string" in caplog.text
    caplog.clear()
    with caplog.at_level("DEBUG", logger=nc.__name__):
        assert nc.flags("A") == {"enabled": True, "public": False}
    assert not caplog.records, caplog.text


# ── all_feeds() / public_feed_names(): the publish-time re-check ────────────

def test_all_feeds_keeps_disabled_feeds_and_follows_the_same_rules(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x"},
         {"name": "B", "kind": "rss", "url": "y"},
         {"name": "C", "kind": "carrier_pigeon", "url": "z"},
         {"name": "A", "kind": "rss", "url": "dup"}],
        {"B": {"enabled": False, "public": False}}))
    got = nc.all_feeds()
    assert [f["name"] for f in got] == ["A", "B"]
    assert got[0]["url"] == "x"
    assert [(f["enabled"], f["public"]) for f in got] == [(True, True), (False, False)]


def test_public_feed_names_are_every_public_feed_enabled_or_not(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x"},
         {"name": "B", "kind": "rss", "url": "y"},
         {"name": "C", "kind": "rss", "url": "z"},
         {"name": "D", "kind": "carrier_pigeon", "url": "w"}],
        {"B": {"enabled": False}, "C": {"public": False}, "D": {"public": True}}))
    assert nc.public_feed_names() == ["A", "B"]


def test_public_feed_names_fail_closed_on_a_malformed_flag(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: _cfg(
        [{"name": "A", "kind": "rss", "url": "x"}], {"A": {"public": "yes"}}))
    assert nc.public_feed_names() == []


def test_the_helpers_are_silent_on_a_malformed_config(monkeypatch, caplog):
    monkeypatch.setattr(nc, "load", lambda: {
        "feeds": [{"name": "A", "kind": "rss", "url": "x", "public": "no"},
                  {"name": "A", "kind": "rss", "url": "y"},
                  "junk"],
        "feed_flags": {"ghost": {"public": True}, "A": 3}})
    with caplog.at_level("WARNING", logger=nc.__name__):
        nc.all_feeds()
        nc.public_feed_names()
    assert not caplog.records, caplog.text


def test_public_feed_names_agree_with_flags_on_the_shipped_file():
    names = [f["name"] for f in _shipped_feeds()]
    assert nc.public_feed_names() == [n for n in names if nc.flags(n)["public"]]
    assert "GlobeNewswire" in nc.public_feed_names()      # disabled, still public


def test_all_feeds_is_a_deep_copy(monkeypatch):
    cfg = _cfg([{"name": "A", "kind": "edgar_filings", "forms": ["S-1"]}])
    monkeypatch.setattr(nc, "load", lambda: cfg)
    nc.all_feeds()[0]["forms"].append("S-3")
    assert cfg["feeds"][0]["forms"] == ["S-1"]
    assert "enabled" not in cfg["feeds"][0]


# ── the shipped file ────────────────────────────────────────────────────────

def _shipped():
    """The TRACKED file alone - never the operator's config/local override."""
    with open(nc.NEWS_TOML, "rb") as fh:
        return tomllib.load(fh)


def _shipped_feeds():
    return _shipped()["feeds"]


def test_every_shipped_feed_has_an_explicit_flag_entry_and_nothing_else_does():
    names = [f["name"] for f in _shipped_feeds()]
    flags = _shipped()["feed_flags"]
    assert set(flags) == set(names)
    for name, entry in flags.items():
        assert set(entry) == {"enabled", "public"}, name
        assert all(isinstance(v, bool) for v in entry.values()), name


def test_no_shipped_feed_carries_a_switch_inside_feeds():
    """The switches moved to [feed_flags]; one left behind would be a WARNING
    on every read."""
    for feed in _shipped_feeds():
        assert "enabled" not in feed and "public" not in feed, feed["name"]


def test_the_shipped_file_reads_without_a_single_warning(caplog):
    with caplog.at_level("WARNING", logger=nc.__name__):
        nc.feeds()
        for feed in _shipped_feeds():
            nc.flags(feed["name"])
    assert not caplog.records, caplog.text


def test_every_shipped_feed_is_public_and_only_globenewswire_is_off():
    flags = _shipped()["feed_flags"]
    assert all(e["public"] for e in flags.values())
    assert {n for n, e in flags.items() if not e["enabled"]} == {"GlobeNewswire"}
    assert flags["ZeroHedge"]["public"] and flags["Truth Social"]["public"]


def test_the_shipped_file_is_the_one_the_loader_reads():
    assert nc.NEWS_TOML == ROOT / "config" / "news.toml"
    assert nc.NEWS_TOML.is_file()


def test_shipped_feeds_cover_every_kind_with_unique_names():
    """Every kind has a feed, and names are unique - the name keys the per-feed
    state table, so two feeds sharing one would overwrite each other's state."""
    feeds = _shipped_feeds()
    assert {f["kind"] for f in feeds} == set(nc.KINDS)
    names = [f["name"] for f in feeds]
    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)


def test_shipped_feeds_carry_what_their_kind_needs():
    for feed in _shipped_feeds():
        kind = feed["kind"]
        if kind in ("rss", "yahoo_ticker"):
            assert feed["url"].startswith("https://"), feed
        if kind == "yahoo_ticker":
            assert "{symbol}" in feed["url"], feed
        if kind == "google_news":
            assert feed["query"], feed
        if kind == "edgar_filings":
            assert feed["forms"], feed


# ── Tier 1 may import it ────────────────────────────────────────────────────

ALLOWED_DIRECT = {"copy", "logging", "repo_paths", "shared", "shared.symbols",
                  "shared.config_toml"}

EXPECTED = {"repo_paths", "shared", "shared.config_toml", "shared.news_config",
            "shared.symbols"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.news_config
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(m for m in new
                              if m.split(".")[0] not in sys.stdlib_module_names)))
""" % ROOT


def test_direct_imports_are_within_the_tier1_allow_list():
    tree = ast.parse((ROOT / "shared" / "news_config.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module)
    assert found <= ALLOWED_DIRECT, sorted(found - ALLOWED_DIRECT)


def test_news_config_imports_only_config_and_the_symbol_allow_list():
    """Tier 1 imports this module, so its import set is pinned, in a FRESH
    interpreter so a transitive import cannot hide behind an earlier test's."""
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    stray = {m for m in new if m not in EXPECTED and m.split(".")[0] != "tzdata"}
    assert not stray, sorted(stray)
    assert {"shared.news_config", "shared.symbols"} <= new
