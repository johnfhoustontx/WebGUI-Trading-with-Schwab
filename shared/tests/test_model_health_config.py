"""``shared/model_health_config.py``: defaults, the shipped file, and the
fallback for a bad value."""
import pytest

from shared import model_health_config as cfg


@pytest.fixture(autouse=True)
def _fresh():
    cfg.reset_cache()
    yield
    cfg.reset_cache()


def test_shipped_file_matches_the_defaults():
    assert cfg.enabled() is True
    assert cfg.min_days() == 20
    assert cfg.recent_sessions() == 5
    assert cfg.min_reference_sessions() == 8
    assert cfg.psi_moved() == pytest.approx(0.10)
    assert cfg.psi_shifted() == pytest.approx(0.25)
    assert cfg.bootstrap_draws() == 2000


@pytest.mark.parametrize("key,bad,getter,want", [
    ("min_days", "twenty", cfg.min_days, 20),
    ("min_days", 0, cfg.min_days, 20),
    ("min_days", 2.5, cfg.min_days, 20),
    ("recent_sessions", True, cfg.recent_sessions, 5),
    ("bootstrap_draws", 5, cfg.bootstrap_draws, 2000),
    ("psi_moved", -1.0, cfg.psi_moved, 0.10),
])
def test_a_bad_value_falls_back_to_its_default(monkeypatch, key, bad, getter, want):
    monkeypatch.setattr(cfg, "load", lambda: {**cfg.DEFAULTS, key: bad})
    assert getter() == pytest.approx(want)


def test_enabled_must_be_a_bool(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {**cfg.DEFAULTS, "enabled": "no"})
    assert cfg.enabled() is True
    monkeypatch.setattr(cfg, "load", lambda: {**cfg.DEFAULTS, "enabled": False})
    assert cfg.enabled() is False


def test_shifted_below_moved_is_refused(monkeypatch):
    monkeypatch.setattr(cfg, "load",
                        lambda: {**cfg.DEFAULTS, "psi_moved": 0.3, "psi_shifted": 0.2})
    assert cfg.psi_shifted() >= cfg.psi_moved()
