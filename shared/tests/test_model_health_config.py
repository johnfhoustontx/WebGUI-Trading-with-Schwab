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
    assert cfg.drift_permutations() == 200
    assert cfg.drift_p() == pytest.approx(0.05)


@pytest.mark.parametrize("key,bad,getter,want", [
    ("min_days", "twenty", cfg.min_days, 20),
    ("min_days", 0, cfg.min_days, 20),
    ("min_days", 2.5, cfg.min_days, 20),
    ("recent_sessions", True, cfg.recent_sessions, 5),
    ("bootstrap_draws", 5, cfg.bootstrap_draws, 2000),
    ("psi_moved", -1.0, cfg.psi_moved, 0.10),
    ("drift_permutations", 3, cfg.drift_permutations, 200),
    ("drift_p", 0.9, cfg.drift_p, 0.05),
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


# ── [range] ────────────────────────────────────────────────────────────────
def test_shipped_range_matches_the_defaults():
    assert cfg.range_enabled() is True
    assert cfg.range_symbols() == {"SPY": "$VIX", "QQQ": "$VXN", "$SPX": "$VIX"}
    assert cfg.range_volume_from() == {"$SPX": "SPY"}
    assert cfg.range_horizons() == [30, 60, 0]
    assert (cfg.range_first(), cfg.range_last()) == (525, 870)
    assert cfg.range_step_min() == 15
    assert (cfg.range_min_train_days(), cfg.range_min_days()) == (40, 40)
    assert cfg.range_ridge() == pytest.approx(1.0)
    assert cfg.range_volume_sessions() == 20


def _with_range(monkeypatch, **over):
    rng = {**cfg.DEFAULTS["range"], **over}
    monkeypatch.setattr(cfg, "load", lambda: {**cfg.DEFAULTS, "range": rng})


def test_bad_range_values_fall_back(monkeypatch):
    _with_range(monkeypatch, horizons_min=[30, 7, -5, True, 30], first="9am", last="07:00",
                step_min=0, ridge=-1, symbols={"spy lower": "$VIX"})
    assert cfg.range_horizons() == [30]
    assert cfg.range_first() == 525
    assert cfg.range_last() == 525          # before the first: held at the first
    assert cfg.range_step_min() == 15
    assert cfg.range_ridge() == pytest.approx(1.0)
    assert cfg.range_symbols() == {"SPY": "$VIX", "QQQ": "$VXN", "$SPX": "$VIX"}


def test_an_empty_volume_map_is_allowed(monkeypatch):
    _with_range(monkeypatch, volume_from={})
    assert cfg.range_volume_from() == {}
