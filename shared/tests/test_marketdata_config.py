"""config/marketdata.toml - the proxy's local store and the collector cadence."""
import importlib

import pytest

from shared import marketdata_config as mc


@pytest.fixture(autouse=True)
def _fresh():
    mc.reset_cache()
    yield
    mc.reset_cache()


def test_shipped_file_matches_the_built_in_defaults():
    # The TOML only overrides; a key present in one and not the other is drift.
    assert mc.load() == mc.DEFAULTS


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


def test_a_section_replaced_by_a_scalar_falls_back_to_defaults(monkeypatch):
    monkeypatch.setattr(mc, "load", lambda: {**mc.DEFAULTS, "bars": 5})
    assert mc.section("bars") == mc.DEFAULTS["bars"]


def test_an_unusable_number_falls_back_to_its_default(monkeypatch):
    cfg = {**mc.DEFAULTS, "chains": {**mc.DEFAULTS["chains"],
                                     "max_age_sec": float("nan"), "wide_days": -3}}
    monkeypatch.setattr(mc, "load", lambda: cfg)
    assert mc.section("chains")["max_age_sec"] == mc.DEFAULTS["chains"]["max_age_sec"]
    assert mc.section("chains")["wide_days"] == mc.DEFAULTS["chains"]["wide_days"]


def test_module_reloads_cleanly():
    importlib.reload(mc)
    assert mc.mode() == "shadow"
