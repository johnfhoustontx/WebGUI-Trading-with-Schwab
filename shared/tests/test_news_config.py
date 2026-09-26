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


def test_defaults_match_the_shipped_file():
    """The built-in DEFAULTS and the tracked TOML are two copies of the same
    values; pin them together so an edit to one cannot silently drift."""
    with open(nc.NEWS_TOML, "rb") as fh:
        shipped = tomllib.load(fh)
    assert nc.DEFAULTS["collector"] == shipped["collector"]
    assert nc.DEFAULTS["trending"] == shipped["trending"]


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
