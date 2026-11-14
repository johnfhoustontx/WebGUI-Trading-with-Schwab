"""``config/model_health.toml``: how Model Health scores the app's stated
chances. Read per call by ``services/options_svc/model_health.py``, so a saved
change applies at the next run. Stdlib, ``shared.config_toml`` and
``repo_paths`` only. Design: docs/plans/2026-11-14-model-health-design.md
"""
from repo_paths import MODEL_HEALTH_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "enabled": True,
    "min_days": 20,
    "recent_sessions": 5,
    "min_reference_sessions": 8,
    "psi_moved": 0.10,
    "psi_shifted": 0.25,
    "bootstrap_draws": 2000,
}

load, reset_cache = toml_loader(MODEL_HEALTH_TOML, DEFAULTS, label="model_health.toml")


def _number(key, *, minimum, maximum, integer=False):
    """The setting, or its default when missing, not a number, or out of range."""
    default = DEFAULTS[key]
    cfg = load()
    raw = cfg.get(key, default) if isinstance(cfg, dict) else default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    if integer and raw != int(raw):
        return default
    if not minimum <= raw <= maximum:
        return default
    return int(raw) if integer else float(raw)


def enabled() -> bool:
    cfg = load()
    raw = cfg.get("enabled") if isinstance(cfg, dict) else None
    return raw if isinstance(raw, bool) else DEFAULTS["enabled"]


def min_days() -> int:
    return _number("min_days", minimum=1, maximum=500, integer=True)


def recent_sessions() -> int:
    return _number("recent_sessions", minimum=1, maximum=60, integer=True)


def min_reference_sessions() -> int:
    return _number("min_reference_sessions", minimum=1, maximum=250, integer=True)


def psi_moved() -> float:
    return _number("psi_moved", minimum=0.0, maximum=5.0)


def psi_shifted() -> float:
    """At least ``psi_moved``: a shifted band below the moved one is refused."""
    value = _number("psi_shifted", minimum=0.0, maximum=5.0)
    return value if value >= psi_moved() else max(DEFAULTS["psi_shifted"], psi_moved())


def bootstrap_draws() -> int:
    return _number("bootstrap_draws", minimum=100, maximum=20000, integer=True)


__all__ = ["DEFAULTS", "load", "reset_cache", "enabled", "min_days", "recent_sessions",
           "min_reference_sessions", "psi_moved", "psi_shifted", "bootstrap_draws"]
