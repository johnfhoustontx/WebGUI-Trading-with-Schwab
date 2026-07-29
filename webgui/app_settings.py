"""JSON-backed, single-user GUI settings store (pure stdlib, unit-testable).

Persists to webgui/data/settings.json (data/ is gitignored — regenerates from
DEFAULTS on a fresh clone). No engine imports; the GUI tier stays thin.
"""
import json
import pathlib

DEFAULTS = {
    "alert_enabled": True,
    "alert_sound": "chime",          # chime | bell | ping
    "alert_volume": 0.6,             # 0.0–1.0
    "alert_market_hours_only": True,
    "alert_min_score": 0,            # only alert on signals with score >= this
    "desktop_notifications": False,
    "flow_alerts_enabled": True,     # put/call premium crossover + unusual-activity toasts
    "ticker_enabled": True,          # bottom market-summary marquee on every page
    "ticker_speed": 60,              # marquee duration seconds (higher = slower)
    "nav_pinned": False,             # nav drawer locked open (else a hover icon rail)
    "gamma_level_tracks": False,     # heatmap overlay: intraday flip/wall movement
}

_PATH = pathlib.Path(__file__).resolve().parent / "data" / "settings.json"

# Single-user, single-process: the only writer is set() (the Settings page), so an
# in-memory cache is safe and lets the 2s alert watcher read settings without a
# disk read every tick. Invalidated by set() (updates it) and reset_cache() (tests).
_cache = {"data": None}


def _load_from_disk():
    try:
        raw = json.loads(_PATH.read_text())
        if not isinstance(raw, dict):
            raise ValueError("settings.json is not an object")
        return {**DEFAULTS, **raw}
    except Exception:
        return dict(DEFAULTS)


def load():
    """Full settings dict: file values merged over DEFAULTS; DEFAULTS on any error.

    Cached in memory after the first read (see ``_cache``); a fresh copy is
    returned each call so callers can't mutate the cache.
    """
    if _cache["data"] is None:
        _cache["data"] = _load_from_disk()
    return dict(_cache["data"])


def reset_cache():
    """Drop the in-memory cache so the next ``load()`` re-reads disk (test helper)."""
    _cache["data"] = None


def get(key):
    """Single setting value (None for unknown keys)."""
    return load().get(key)


def all():
    """Alias for load() — the full merged settings dict."""
    return load()


def set(key, value):
    """Persist one setting (writes the full merged dict back to disk + cache)."""
    data = load()
    data[key] = value
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data, indent=2))
    _cache["data"] = data  # keep the in-memory cache in lockstep with disk
    return data
