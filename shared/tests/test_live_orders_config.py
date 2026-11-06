"""config/live_orders.toml: the switch the proxy reads before it sends Schwab
anything about an order."""
import ast
import pathlib
import tomllib

from repo_paths import LIVE_ORDERS_TOML
from shared import live_orders_config as cfg


def test_the_shipped_file_carries_the_defaults():
    with open(LIVE_ORDERS_TOML, "rb") as fh:
        assert tomllib.load(fh) == cfg.DEFAULTS


def test_it_ships_off():
    assert cfg.DEFAULTS["mode"] == "off"
    assert cfg.mode() == "off"
    assert cfg.preview_allowed() is False


def test_preview_is_read(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"mode": "preview"})
    assert cfg.mode() == "preview"
    assert cfg.preview_allowed() is True


def test_anything_but_an_exact_mode_is_off(monkeypatch):
    for bad in ("Preview", "preview ", "on", "live", True, 1, None, ["preview"]):
        monkeypatch.setattr(cfg, "load", lambda bad=bad: {"mode": bad})
        assert cfg.mode() == "off", bad
        assert cfg.preview_allowed() is False, bad


def test_no_mode_places_an_order():
    """Phase 0 has no route that places an order, so no mode may name one."""
    assert cfg.MODES == ("off", "preview")


def test_ticks_default_and_bad_values_fall_back(monkeypatch):
    assert cfg.ticks() == {"under_3": 0.05, "from_3": 0.10, "penny_under_3": 0.01,
                           "penny_from_3": 0.05, "penny": ["SPY", "QQQ", "IWM"]}
    monkeypatch.setattr(cfg, "load", lambda: {"ticks": {
        "under_3": 0, "from_3": "dime", "penny_under_3": True, "penny_from_3": 2.0,
        "penny": "SPY"}})
    assert cfg.ticks() == cfg.DEFAULTS["ticks"]


def test_a_tick_override_is_read(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"ticks": {"under_3": 0.01,
                                                        "penny": [" spy ", "", "AMD"]}})
    t = cfg.ticks()
    assert t["under_3"] == 0.01 and t["from_3"] == 0.10
    assert t["penny"] == ["SPY", "AMD"]


def test_the_module_imports_only_stdlib_and_its_two_repo_modules():
    tree = ast.parse(pathlib.Path(cfg.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module)
    assert names == {"math", "repo_paths", "shared.config_toml"}
