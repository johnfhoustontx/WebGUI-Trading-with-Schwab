"""config/new_highs.toml - the Macro Board's new highs / lows counts."""
import pytest

from shared import new_highs_config as nh

_DEFAULT_LOAD = {
    "enabled": True,
    "url": "https://scanner.tradingview.com/america/scan",
    "exchanges": ["NYSE", "NASDAQ", "AMEX"],
    "min_price": 5.0,
    "rth_refresh_sec": 60,
    "after_close_min": 10,
    "timeout_sec": 10,
    "retry_sec": 120,
    "list_max": 5000,
    "public_show": True,
}


@pytest.fixture(autouse=True)
def _fresh():
    nh.reset_cache()
    yield
    nh.reset_cache()


def _with(monkeypatch, raw):
    """Patch the RAW loader, so a test proves the value is read, not merely equal."""
    monkeypatch.setattr(nh, "load_raw", lambda: raw)


def test_defaults_when_file_missing(monkeypatch):
    _with(monkeypatch, {})
    assert nh.load() == _DEFAULT_LOAD


def test_shipped_file_loads_the_defaults():
    # The tracked file ships the built-in values (the override layer is off
    # under pytest), so the two never drift apart unnoticed.
    assert nh.load() == _DEFAULT_LOAD


@pytest.mark.parametrize("raw, key, want", [
    ({"rth_refresh_sec": 0}, "rth_refresh_sec", 60),
    ({"rth_refresh_sec": 5}, "rth_refresh_sec", 60),          # floor is 15
    ({"rth_refresh_sec": 90}, "rth_refresh_sec", 90),
    ({"min_price": -1}, "min_price", 5.0),
    ({"min_price": "5"}, "min_price", 5.0),
    ({"min_price": 10}, "min_price", 10.0),
    ({"exchanges": ["NYSE", 3, ""]}, "exchanges", ["NYSE"]),
    ({"exchanges": []}, "exchanges", ["NYSE", "NASDAQ", "AMEX"]),
    ({"exchanges": "NYSE"}, "exchanges", ["NYSE", "NASDAQ", "AMEX"]),
    ({"url": "ftp://x"}, "url", "https://scanner.tradingview.com/america/scan"),
    ({"url": "https://example.test/scan"}, "url", "https://example.test/scan"),
    ({"public": {"show": "yes"}}, "public_show", True),
    ({"public": {"show": False}}, "public_show", False),
    ({"public": "off"}, "public_show", True),
    ({"enabled": False}, "enabled", False),
    ({"enabled": "no"}, "enabled", True),
    ({"after_close_min": 500}, "after_close_min", 10),
    ({"timeout_sec": 1}, "timeout_sec", 10),
    ({"retry_sec": 7200}, "retry_sec", 120),
    ({"list_max": -1}, "list_max", 5000),
    ({"list_max": 9000}, "list_max", 5000),
    ({"list_max": "200"}, "list_max", 5000),
    ({"list_max": 200}, "list_max", 200),
])
def test_bad_values_fall_back(monkeypatch, raw, key, want):
    _with(monkeypatch, raw)
    assert nh.load()[key] == want


def test_min_price_zero_is_kept(monkeypatch):
    # 0 is a real setting (no $5 row), not a missing one.
    _with(monkeypatch, {"min_price": 0})
    assert nh.load()["min_price"] == 0.0


def test_list_max_zero_is_kept(monkeypatch):
    # 0 is a real setting (counts only, no lists), not a missing one.
    _with(monkeypatch, {"list_max": 0})
    assert nh.load()["list_max"] == 0


def test_load_returns_a_fresh_mapping(monkeypatch):
    _with(monkeypatch, {})
    first = nh.load()
    first["exchanges"].append("OTC")
    assert nh.load()["exchanges"] == ["NYSE", "NASDAQ", "AMEX"]
