"""``config/model_health.toml``: how Model Health scores the app's stated
chances. Read per call by ``services/options_svc/model_health.py``, so a saved
change applies at the next run. Stdlib, ``shared.config_toml`` and
``repo_paths`` only. Design: docs/plans/2026-11-14-model-health-design.md
"""
import math
import re

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
    "drift_permutations": 200,
    "drift_p": 0.05,
    "range": {
        "enabled": True,
        "symbols": {"SPY": "$VIX", "QQQ": "$VXN", "$SPX": "$VIX"},
        "volume_from": {"$SPX": "SPY"},
        "horizons_min": [30, 60, 0],
        "first": "08:45",
        "last": "14:30",
        "step_min": 15,
        "min_train_days": 40,
        "min_days": 40,
        "ridge": 1.0,
        "floor": 1e-8,
        "volume_sessions": 20,
    },
    "exit": {
        "enabled": True,
        "types": ["SWING", "0DTE", "INCOME"],
        "thin_min": 30,
        "min_train_days": 15,
        "min_days": 40,
        "penalty": 1.0,
        "buckets": [-1.0, -0.5, -0.25, 0.0, 0.25],
    },
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


def drift_permutations() -> int:
    return _number("drift_permutations", minimum=20, maximum=5000, integer=True)


def drift_p() -> float:
    return _number("drift_p", minimum=0.001, maximum=0.5)


# ── [range]: the range forecast (docs/plans/2026-11-14-range-forecast-design.md)
_SYMBOL_OK = re.compile(r"^\$?[A-Z][A-Z0-9.]{0,9}$")


def _range() -> dict:
    cfg = load()
    raw = cfg.get("range") if isinstance(cfg, dict) else None
    return raw if isinstance(raw, dict) else {}


def _range_number(key, *, minimum, maximum, integer=False):
    default = DEFAULTS["range"][key]
    raw = _range().get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    if integer and raw != int(raw):
        return default
    if not minimum <= raw <= maximum:
        return default
    return int(raw) if integer else float(raw)


def _symbol_map(key) -> dict:
    raw = _range().get(key)
    if not isinstance(raw, dict):
        return dict(DEFAULTS["range"][key])
    out = {k: v for k, v in raw.items()
           if isinstance(k, str) and isinstance(v, str)
           and _SYMBOL_OK.match(k) and _SYMBOL_OK.match(v)}
    return out if out or not raw else dict(DEFAULTS["range"][key])


def range_enabled() -> bool:
    raw = _range().get("enabled")
    return raw if isinstance(raw, bool) else DEFAULTS["range"]["enabled"]


def range_symbols() -> dict:
    """``{symbol: its volatility index}``, in the file's order."""
    return _symbol_map("symbols")


def range_volume_from() -> dict:
    """``{symbol: the symbol whose volume stands in}`` for one with no volume."""
    raw = _range().get("volume_from")
    if isinstance(raw, dict) and not raw:
        return {}
    return _symbol_map("volume_from")


def range_horizons() -> list:
    """Minutes ahead; 0 is the rest of the session. Duplicates dropped."""
    raw = _range().get("horizons_min")
    if not isinstance(raw, list):
        return list(DEFAULTS["range"]["horizons_min"])
    out = []
    for v in raw:
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 390                 and v % 5 == 0 and v not in out:
            out.append(v)
    return out or list(DEFAULTS["range"]["horizons_min"])


def _clock(key) -> int:
    """Minutes after midnight Central of an ``HH:MM`` setting."""
    raw = _range().get(key, DEFAULTS["range"][key])
    try:
        hh, mm = str(raw).split(":")
        value = int(hh) * 60 + int(mm)
        if 0 <= value < 24 * 60 and value % 5 == 0:
            return value
    except ValueError:
        pass
    hh, mm = DEFAULTS["range"][key].split(":")
    return int(hh) * 60 + int(mm)


def range_first() -> int:
    return _clock("first")


def range_last() -> int:
    """At least ``range_first``."""
    value, first = _clock("last"), range_first()
    return value if value >= first else first


def range_step_min() -> int:
    return _range_number("step_min", minimum=5, maximum=120, integer=True)


def range_min_train_days() -> int:
    return _range_number("min_train_days", minimum=10, maximum=500, integer=True)


def range_min_days() -> int:
    return _range_number("min_days", minimum=1, maximum=500, integer=True)


def range_ridge() -> float:
    return _range_number("ridge", minimum=0.0, maximum=1e6)


def range_floor() -> float:
    return _range_number("floor", minimum=1e-12, maximum=1e-3)


def range_volume_sessions() -> int:
    return _range_number("volume_sessions", minimum=5, maximum=120, integer=True)


# ── [exit]: the exit chance (docs/plans/2026-11-14-exit-chance-design.md) ────
_TYPE_OK = re.compile(r"^[A-Z0-9_]{1,12}$")


def _exit() -> dict:
    cfg = load()
    raw = cfg.get("exit") if isinstance(cfg, dict) else None
    return raw if isinstance(raw, dict) else {}


def _exit_number(key, *, minimum, maximum, integer=False):
    default = DEFAULTS["exit"][key]
    raw = _exit().get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    if integer and raw != int(raw):
        return default
    if not minimum <= raw <= maximum:
        return default
    return int(raw) if integer else float(raw)


def exit_enabled() -> bool:
    raw = _exit().get("enabled")
    return raw if isinstance(raw, bool) else DEFAULTS["exit"]["enabled"]


def exit_types() -> list:
    """Scanner types scored, in the file's order; a bad entry is dropped."""
    raw = _exit().get("types")
    if not isinstance(raw, list):
        return list(DEFAULTS["exit"]["types"])
    out = []
    for v in raw:
        if isinstance(v, str) and _TYPE_OK.match(v) and v not in out:
            out.append(v)
    return out or list(DEFAULTS["exit"]["types"])


def exit_thin_min() -> int:
    return _exit_number("thin_min", minimum=5, maximum=240, integer=True)


def exit_min_train_days() -> int:
    return _exit_number("min_train_days", minimum=5, maximum=120, integer=True)


def exit_min_days() -> int:
    return _exit_number("min_days", minimum=10, maximum=250, integer=True)


def exit_penalty() -> float:
    return _exit_number("penalty", minimum=1e-6, maximum=1e3)


def exit_buckets() -> list:
    """The P&L-fraction bucket edges of the distance baseline, sorted; a list
    holding anything but finite numbers (or empty, or over 12) is refused."""
    raw = _exit().get("buckets")
    ok = (isinstance(raw, list) and 1 <= len(raw) <= 12
          and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                  and math.isfinite(v) for v in raw))
    if not ok:
        return list(DEFAULTS["exit"]["buckets"])
    return sorted({float(v) for v in raw})


__all__ = ["DEFAULTS", "load", "reset_cache", "enabled", "min_days", "recent_sessions",
           "min_reference_sessions", "psi_moved", "psi_shifted", "bootstrap_draws",
           "drift_permutations", "drift_p", "range_enabled", "range_symbols",
           "range_volume_from", "range_horizons", "range_first", "range_last",
           "range_step_min", "range_min_train_days", "range_min_days", "range_ridge",
           "range_floor", "range_volume_sessions", "exit_enabled", "exit_types",
           "exit_thin_min", "exit_min_train_days", "exit_min_days", "exit_penalty",
           "exit_buckets"]
