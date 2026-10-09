"""config/gamma_heat.toml: the Dealer Positioning heatmap's tunables."""
import ast
import pathlib

from shared import gamma_heat_config as cfg


def test_defaults_when_nothing_is_overridden():
    assert cfg.balanced() == {"max_polarity": 0.15, "min_size_quantile": 0.8,
                              "max_marks": 3}


def test_the_shipped_file_carries_the_defaults():
    """The file and the built-in defaults are two copies of one set of numbers."""
    import tomllib

    from repo_paths import GAMMA_HEAT_TOML
    with open(GAMMA_HEAT_TOML, "rb") as fh:
        assert tomllib.load(fh) == cfg.DEFAULTS


def test_a_bad_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"balanced": {
        "max_polarity": "wide", "min_size_quantile": True, "max_marks": -2}})
    assert cfg.balanced() == {"max_polarity": 0.15, "min_size_quantile": 0.8,
                              "max_marks": 3}


def test_a_value_outside_its_range_falls_back(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"balanced": {"max_polarity": 1.5}})
    assert cfg.balanced()["max_polarity"] == 0.15


def test_an_override_is_read(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"balanced": {"max_polarity": 0.3,
                                                           "max_marks": 0}})
    assert cfg.balanced()["max_polarity"] == 0.3
    assert cfg.balanced()["max_marks"] == 0


def test_a_missing_section_is_the_defaults(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {})
    assert cfg.balanced() == cfg.DEFAULTS["balanced"]
    assert cfg.lock() == cfg.DEFAULTS["lock"]


def test_lock_defaults():
    assert cfg.lock() == {"minutes": 60, "quantile": 0.95, "headroom": 1.5}
    assert isinstance(cfg.lock()["minutes"], int)


def test_lock_overrides_are_read_and_bad_ones_fall_back(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"lock": {
        "minutes": 30, "quantile": 0.2, "headroom": 0.5}})
    # 30 minutes is allowed; a quantile under one half and a headroom under 1
    # are not, and each falls back to its own default.
    assert cfg.lock() == {"minutes": 30, "quantile": 0.95, "headroom": 1.5}


def test_it_imports_only_the_config_loader_and_the_paths():
    """Tier 1 imports this module, so its import set is pinned (CLAUDE.md, the
    Tier-1 allow-list): stdlib, shared.config_toml and repo_paths."""
    tree = ast.parse(pathlib.Path(cfg.__file__).read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module)
    assert mods == {"repo_paths", "shared.config_toml"}
