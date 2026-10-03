"""config/marketdata.toml - the proxy's local store and the collector cadence."""
import importlib
import tomllib

import pytest

import repo_paths
from shared import marketdata_config as mc

_MISSING = object()


@pytest.fixture(autouse=True)
def _fresh():
    mc.reset_cache()
    yield
    mc.reset_cache()


def _with(monkeypatch, name, key, value):
    """Make ``load()`` return the defaults with one key of one table replaced
    (or, for ``_MISSING``, removed)."""
    table = dict(mc.DEFAULTS[name])
    if value is _MISSING:
        table.pop(key, None)
    else:
        table[key] = value
    cfg = {**mc.DEFAULTS, name: table}
    monkeypatch.setattr(mc, "load", lambda: cfg)


def test_shipped_file_matches_the_built_in_defaults():
    # The TOML only overrides; a key present in one and not the other is drift.
    assert mc.load() == mc.DEFAULTS
    # load() merges the file OVER the defaults, so a key deleted from the file
    # still passes the line above. The raw file is the other direction.
    with open(repo_paths.MARKETDATA_TOML, "rb") as fh:
        assert tomllib.load(fh) == mc.DEFAULTS


def test_ships_dark():
    assert mc.mode() == "shadow"
    assert mc.section("scan")["wide_fetch"] is False
    assert mc.section("collection")["tail_interval_min"] == 1


@pytest.mark.parametrize("bad", ["ON", "enabled", "", None, 1, True])
def test_an_unknown_mode_is_off_never_on(monkeypatch, bad):
    monkeypatch.setattr(mc, "load", lambda: {**mc.DEFAULTS, "mode": bad})
    assert mc.mode() == "off"


def test_a_store_switch_must_be_literally_true(monkeypatch):
    cfg = {**mc.DEFAULTS, "chains": {**mc.DEFAULTS["chains"], "enabled": "false"}}
    monkeypatch.setattr(mc, "load", lambda: cfg)
    assert mc.store_on("chains") is False
    assert mc.store_on("quotes") is True
    assert mc.store_on("nonsense") is False


@pytest.mark.parametrize("store", ["chains", "quotes", "bars"])
@pytest.mark.parametrize("bad", ["false", 1, _MISSING],
                         ids=["a string", "an int", "missing"])
def test_a_section_and_its_store_switch_agree(monkeypatch, store, bad):
    # Two readers of one switch must not disagree about whether it is on.
    _with(monkeypatch, store, "enabled", bad)
    assert mc.store_on(store) is False
    assert mc.section(store)["enabled"] is False


@pytest.mark.parametrize("store", ["chains", "quotes", "bars"])
def test_a_store_that_is_on_reads_on_from_both(store):
    assert mc.store_on(store) is True
    assert mc.section(store)["enabled"] is True


def test_a_store_switch_is_off_when_its_section_is_a_scalar(monkeypatch):
    monkeypatch.setattr(mc, "load", lambda: {**mc.DEFAULTS, "chains": 5})
    assert mc.store_on("chains") is False
    assert mc.section("chains")["enabled"] is False


def test_a_section_replaced_by_a_scalar_falls_back_to_defaults(monkeypatch):
    # "scan" has no store switch, so every key is the plain default.
    monkeypatch.setattr(mc, "load", lambda: {**mc.DEFAULTS, "scan": 5})
    assert mc.section("scan") == mc.DEFAULTS["scan"]
    # a store's other keys fall back too; its switch reads off (test above)
    monkeypatch.setattr(mc, "load", lambda: {**mc.DEFAULTS, "bars": 5})
    assert mc.section("bars") == {**mc.DEFAULTS["bars"], "enabled": False}


@pytest.mark.parametrize("name", ["nonsense", "mode"])
def test_a_name_that_is_not_a_table_is_an_empty_section(name):
    assert mc.section(name) == {}


def test_an_unusable_number_falls_back_to_its_default(monkeypatch):
    cfg = {**mc.DEFAULTS, "chains": {**mc.DEFAULTS["chains"],
                                     "max_age_sec": float("nan"),
                                     "closed_max_age_sec": -3}}
    monkeypatch.setattr(mc, "load", lambda: cfg)
    assert mc.section("chains")["max_age_sec"] == mc.DEFAULTS["chains"]["max_age_sec"]
    assert (mc.section("chains")["closed_max_age_sec"]
            == mc.DEFAULTS["chains"]["closed_max_age_sec"])


@pytest.mark.parametrize("name, key, bad", [
    ("chains", "max_age_sec", True),            # a bool is not a number
    ("chains", "max_age_sec", "45"),            # nor is a string
    ("chains", "max_age_sec", float("inf")),
    ("chains", "max_age_sec", float("-inf")),
    ("scan", "wide_fetch_exclude", "SPY"),      # a string is not a list
    ("scan", "wide_fetch", "true"),             # nor a bool
    ("scan", "wide_fetch", 1),
    ("bars", "today_bar", 5),                   # a number is not a string
], ids=["bool-for-number", "string-for-number", "inf", "minus-inf",
        "string-for-list", "string-for-bool", "int-for-bool",
        "number-for-string"])
def test_a_value_of_the_wrong_kind_falls_back_to_its_default(monkeypatch, name, key, bad):
    _with(monkeypatch, name, key, bad)
    got = mc.section(name)[key]
    want = mc.DEFAULTS[name][key]
    assert got == want
    assert type(got) is type(want)


def test_an_oversized_integer_falls_back_and_never_raises(monkeypatch):
    # math.isfinite raises OverflowError on an int too large for a float.
    _with(monkeypatch, "chains", "max_age_sec", 10 ** 400)
    assert mc.section("chains")["max_age_sec"] == mc.DEFAULTS["chains"]["max_age_sec"]


def test_usable_values_are_kept(monkeypatch):
    # The guards above must not be satisfied by "always return the default".
    _with(monkeypatch, "chains", "max_age_sec", 90)
    assert mc.section("chains")["max_age_sec"] == 90
    _with(monkeypatch, "scan", "wide_fetch_exclude", ["IWM"])
    assert mc.section("scan")["wide_fetch_exclude"] == ["IWM"]
    _with(monkeypatch, "scan", "wide_fetch", True)
    assert mc.section("scan")["wide_fetch"] is True


def test_today_bar_is_one_of_the_two_known_values(monkeypatch):
    assert mc.TODAY_BARS == ("ttl", "quote")
    assert mc.today_bar() == "ttl"                      # as shipped
    _with(monkeypatch, "bars", "today_bar", "quote")
    assert mc.today_bar() == "quote"


@pytest.mark.parametrize("bad", ["quot", "QUOTE", "", 5, None, True])
def test_an_unknown_today_bar_reads_as_ttl(monkeypatch, bad):
    _with(monkeypatch, "bars", "today_bar", bad)
    assert mc.today_bar() == "ttl"


def test_module_reloads_cleanly():
    importlib.reload(mc)
    assert mc.mode() == "shadow"


# ---- the store's three remaining limits -------------------------------------

LIMITS = [("chains", "wide_refetch_max_days", 7),
          ("quotes", "max_symbols", 5000),
          ("bars", "max_entries", 4000)]


@pytest.mark.parametrize("name, key, shipped", LIMITS)
def test_the_stores_limits_are_settings(monkeypatch, name, key, shipped):
    assert mc.section(name)[key] == shipped
    _with(monkeypatch, name, key, shipped + 3)
    assert mc.section(name)[key] == shipped + 3
    for bad in ("10", True, float("nan"), -1, _MISSING):
        _with(monkeypatch, name, key, bad)
        assert mc.section(name)[key] == shipped


def _market_store():
    """``schwab-proxy/market_store.py`` by path: the folder is not a package,
    and the module imports nothing outside the standard library."""
    import importlib.util
    import sys
    path = repo_paths.REPO_ROOT / "schwab-proxy" / "market_store.py"
    spec = importlib.util.spec_from_file_location("_market_store_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module          # dataclasses look the module up
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def test_the_stores_built_in_limits_are_the_shipped_settings():
    # The store falls back to its own number when handed an unusable one. Two
    # copies of one default, in tiers that cannot import each other.
    import inspect
    ms = _market_store()
    assert ms.WIDE_REFETCH_MAX_DAYS == mc.DEFAULTS["chains"]["wide_refetch_max_days"]

    def default(cls, arg):
        return inspect.signature(cls).parameters[arg].default

    assert default(ms.ChainStore, "max_entries") == mc.DEFAULTS["chains"]["max_entries"]
    assert default(ms.QuoteStore, "max_symbols") == mc.DEFAULTS["quotes"]["max_symbols"]
    assert default(ms.BarStore, "max_entries") == mc.DEFAULTS["bars"]["max_entries"]
