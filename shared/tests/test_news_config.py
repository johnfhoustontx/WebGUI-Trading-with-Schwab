"""shared.news_config: the news collector's feeds, cadence and ticker extras."""
import ast
import pathlib
import subprocess
import sys
import tomllib

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
    """Non-vacuous: the shape test above passes on an empty list."""
    expected = [f["name"] for f in _shipped_feeds() if f.get("enabled", True)]
    assert expected
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


# ── the shipped file ────────────────────────────────────────────────────────

def _shipped_feeds():
    """The TRACKED file alone - never the operator's config/local override."""
    with open(nc.NEWS_TOML, "rb") as fh:
        return tomllib.load(fh)["feeds"]


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
