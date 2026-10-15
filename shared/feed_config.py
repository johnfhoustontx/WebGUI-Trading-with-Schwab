"""``config/feed.toml``: the raw feed's mode and its catch-up tunables.

Design: docs/plans/2026-10-14-raw-feed-and-catch-up-design.md. Read per call
through the mtime-cached loader, so a saved change applies with no restart.
Every accessor returns its built-in default for a missing or unusable value and
never raises; an unrecognised ``mode`` reads as ``"off"``, the behaviour of the
app before the feed existed. Stdlib, ``shared.config_toml`` and ``repo_paths``
only.
"""
import math

from repo_paths import FEED_TOML
from shared.config_toml import toml_loader

MODES = ("off", "tee", "on")

DEFAULTS = {
    "feed": {"mode": "off", "keep_sessions": 5, "stale_alarm_min": 2, "zlib_level": 6},
    "catchup": {"push_max_age_min": 3, "lag_alarm_min": 5},
}

load, reset_cache = toml_loader(FEED_TOML, DEFAULTS, label="feed.toml")


def _section(name) -> dict:
    cfg = load()
    sec = cfg.get(name) if isinstance(cfg, dict) else None
    return sec if isinstance(sec, dict) else {}


def _count(section, key) -> int:
    """A positive whole number, else the built-in default."""
    raw = _section(section).get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULTS[section][key]
    if not math.isfinite(raw) or raw != int(raw) or raw < 1:
        return DEFAULTS[section][key]
    return int(raw)


def mode() -> str:
    """``"off"``, ``"tee"`` or ``"on"``; anything else is ``"off"``."""
    raw = _section("feed").get("mode")
    value = raw.strip().lower() if isinstance(raw, str) else None
    return value if value in MODES else "off"


def shadow_enabled() -> bool:
    """The shadow check runs in ``tee`` only."""
    return mode() == "tee"


def zlib_level() -> int:
    """The raw store's zlib level, 1 (fastest) .. 9 (smallest); else 6."""
    raw = _section("feed").get("zlib_level")
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 9:
        return DEFAULTS["feed"]["zlib_level"]
    return raw


def keep_sessions() -> int:
    return _count("feed", "keep_sessions")


def stale_alarm_min() -> int:
    return _count("feed", "stale_alarm_min")


def push_max_age_min() -> int:
    return _count("catchup", "push_max_age_min")


def lag_alarm_min() -> int:
    return _count("catchup", "lag_alarm_min")
