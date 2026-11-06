"""config/live_orders.toml: the switch and limits the proxy, the service and the
ticket page read before anything about a live order happens."""
import ast
import pathlib
import tomllib

import pytest

from repo_paths import LIVE_ORDERS_TOML
from shared import live_orders_config as cfg


def test_the_shipped_file_carries_the_defaults():
    with open(LIVE_ORDERS_TOML, "rb") as fh:
        assert tomllib.load(fh) == cfg.DEFAULTS


def test_it_ships_off_and_with_no_accounts():
    assert cfg.DEFAULTS["mode"] == "off"
    assert cfg.mode() == "off"
    assert cfg.preview_allowed() is False and cfg.place_allowed() is False
    assert cfg.cancel_allowed() is False
    assert "accounts" not in cfg.DEFAULTS
    assert cfg.accounts() == [] and cfg.aliases() == []


@pytest.mark.parametrize("mode, preview, place", [
    ("off", False, False), ("preview", True, False), ("live", True, True)])
def test_each_mode(monkeypatch, mode, preview, place):
    monkeypatch.setattr(cfg, "load", lambda: {"mode": mode})
    assert cfg.mode() == mode
    assert cfg.preview_allowed() is preview
    assert cfg.place_allowed() is place
    # A cancel pulls an order already sent: allowed whenever not off (Phase 3).
    assert cfg.cancel_allowed() is preview


def test_anything_but_an_exact_mode_is_off(monkeypatch):
    for bad in ("Live", "live ", "on", "LIVE", True, 1, None, ["live"]):
        monkeypatch.setattr(cfg, "load", lambda bad=bad: {"mode": bad})
        assert cfg.mode() == "off", bad
        assert cfg.place_allowed() is False and cfg.preview_allowed() is False, bad
        assert cfg.cancel_allowed() is False, bad


def test_the_limits_default_and_bad_values_fall_back(monkeypatch):
    assert (cfg.default_risk_usd(), cfg.max_contracts(), cfg.max_risk_usd()) == (1000.0, 50, 5000.0)
    assert (cfg.ticket_ttl_sec(), cfg.max_command_age_sec(), cfg.status_poll_sec()) == (120, 30, 5)
    assert cfg.max_drift_pct() == 10.0 and cfg.totp_mode() == "daily"
    monkeypatch.setattr(cfg, "load", lambda: {
        "default_risk_usd": -5, "max_contracts": 2.5, "max_risk_usd": float("nan"),
        "ticket_ttl_sec": True, "max_command_age_sec": 0, "status_poll_sec": "5",
        "max_drift_pct": 500, "totp": "never"})
    assert (cfg.default_risk_usd(), cfg.max_contracts(), cfg.max_risk_usd()) == (1000.0, 50, 5000.0)
    assert (cfg.ticket_ttl_sec(), cfg.max_command_age_sec(), cfg.status_poll_sec()) == (120, 30, 5)
    assert cfg.max_drift_pct() == 10.0 and cfg.totp_mode() == "daily"


def test_overrides_are_read(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {
        "default_risk_usd": 500, "max_contracts": 10, "totp": "every_send",
        "prompt": {"banner": False, "banner_max_age_min": 15}})
    assert cfg.default_risk_usd() == 500.0 and cfg.max_contracts() == 10
    assert cfg.totp_mode() == "every_send"
    assert cfg.prompt() == {"banner": False, "banner_max_age_min": 15}


def test_the_banner_switch_must_be_literally_true(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"prompt": {"banner": "yes"}})
    assert cfg.prompt()["banner"] is False


ACCOUNTS = [{"last4": "6763", "alias": "IRA"},
            {"last4": "1191", "alias": "Trading account", "default": True}]


def test_accounts_and_the_default(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"accounts": ACCOUNTS})
    assert cfg.accounts() == [{"last4": "6763", "alias": "IRA", "default": False},
                              {"last4": "1191", "alias": "Trading account", "default": True}]
    assert cfg.aliases() == ["Trading account", "IRA"]
    assert cfg.last4_for("IRA") == "6763"
    assert cfg.last4_for("Trading account") == "1191"
    assert cfg.last4_for("ira") is None and cfg.last4_for("Brokerage") is None


def test_with_no_default_marked_the_first_listed_is(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"accounts": [
        {"last4": "6763", "alias": "IRA"}, {"last4": "1191", "alias": "Trading account"}]})
    assert [a["default"] for a in cfg.accounts()] == [True, False]


def test_two_defaults_keep_the_first(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"accounts": [
        {"last4": "6763", "alias": "IRA", "default": True},
        {"last4": "1191", "alias": "Trading account", "default": True}]})
    assert [a["default"] for a in cfg.accounts()] == [True, False]


def test_bad_account_entries_are_dropped(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"accounts": [
        {"last4": "12345", "alias": "Too long"}, {"last4": 1191, "alias": "Not text"},
        {"last4": "1191", "alias": "  "}, {"last4": "1191", "alias": "x" * 41},
        "1191", {"last4": "6763", "alias": "IRA"},
        {"last4": "6763", "alias": "Again"}, {"last4": "0001", "alias": "ira"}]})
    assert [a["alias"] for a in cfg.accounts()] == ["IRA"]


def test_accounts_not_a_list_is_none(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"accounts": {"last4": "1191"}})
    assert cfg.accounts() == []


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
    assert names == {"math", "re", "repo_paths", "shared.config_toml"}
