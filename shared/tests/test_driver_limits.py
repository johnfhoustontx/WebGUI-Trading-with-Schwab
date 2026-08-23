"""The driver risk envelope is config, and BOTH services read the same file."""
import pytest

from shared import driver_limits


@pytest.fixture(autouse=True)
def _fresh():
    driver_limits.reset_cache()
    yield
    driver_limits.reset_cache()


def test_shipped_toml_matches_the_documented_envelope():
    """config/driver.toml is committed, so these are the values that actually run."""
    t, r, d = driver_limits.targets(), driver_limits.risk(), driver_limits.decision()
    assert (t["daily_target"], t["target_cap"], t["target_floor"]) == (500.0, 1000.0, 250.0)
    assert r["per_trade_max_risk"] == 3000.0
    assert r["daily_risk_budget"] == 12000.0
    assert (r["max_concurrent"], r["max_trades_per_cycle"]) == (10, 5)
    assert (r["vix_max"], r["daily_loss_halt"]) == (35.0, 1500.0)
    assert (d["menu_top_n"], d["checkpoint_min"], d["max_tokens"]) == (15, 30, 2000)


def test_per_trade_max_risk_is_the_cross_service_accessor():
    assert driver_limits.per_trade_max_risk() == 3000.0


def test_the_two_services_resolve_the_SAME_per_trade_cap():
    """The whole point of the extraction. driver_svc.settings and
    options_svc.compute used to hold 3000.0 twice, kept in step by a comment;
    when they disagreed the driver approved a size the sizer zeroed to
    RISK_TOO_HIGH and the log said "Executed" while nothing opened."""
    from services.driver_svc import settings
    from services.options_svc import compute

    assert settings.PER_TRADE_MAX_RISK == compute._DRIVER_MAX_RISK_PER_TRADE
    assert settings.PER_TRADE_MAX_RISK == driver_limits.per_trade_max_risk()


def test_both_services_actually_READ_the_config(monkeypatch):
    """Asserting the two constants are equal is NOT enough - they were equal
    before this change too, both hard-coded 3000.0. The discriminating test moves
    the config and requires both to follow.

    They are module-level constants resolved at import (the house "edit + restart"
    contract), so the check is a reload rather than a live re-read.
    """
    import importlib

    from services.driver_svc import settings
    from services.options_svc import compute

    monkeypatch.setattr(driver_limits, "per_trade_max_risk", lambda: 1234.0)
    monkeypatch.setattr(driver_limits, "risk",
                        lambda: {**driver_limits.DEFAULTS["risk"],
                                 "per_trade_max_risk": 1234.0})
    try:
        importlib.reload(settings)
        importlib.reload(compute)
        assert settings.PER_TRADE_MAX_RISK == 1234.0, \
            "driver_svc.settings is not reading config/driver.toml"
        assert compute._DRIVER_MAX_RISK_PER_TRADE == 1234.0, \
            "options_svc.compute is not reading config/driver.toml"
    finally:
        monkeypatch.undo()
        importlib.reload(settings)
        importlib.reload(compute)


def test_settings_limits_dict_is_built_from_the_config():
    from services.driver_svc import settings

    lim = settings.limits()
    assert lim["per_trade_max_risk"] == driver_limits.risk()["per_trade_max_risk"]
    assert lim["daily_target"] == driver_limits.targets()["daily_target"]
    assert lim["daily_loss_halt"] == driver_limits.risk()["daily_loss_halt"]


def test_a_missing_section_falls_back_to_defaults(monkeypatch):
    monkeypatch.setattr(driver_limits, "load", lambda: {"risk": "not-a-table"})
    assert driver_limits.risk() == driver_limits.DEFAULTS["risk"]
    assert driver_limits.per_trade_max_risk() == 3000.0


def test_a_junk_value_does_not_raise(monkeypatch):
    monkeypatch.setattr(driver_limits, "load",
                        lambda: {"risk": {"per_trade_max_risk": "lots"}})
    assert driver_limits.per_trade_max_risk() == 3000.0
