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
}

_PATH = pathlib.Path(__file__).resolve().parent / "data" / "settings.json"


def load():
    """Full settings dict: file values merged over DEFAULTS; DEFAULTS on any error."""
    try:
        raw = json.loads(_PATH.read_text())
        if not isinstance(raw, dict):
            raise ValueError("settings.json is not an object")
        return {**DEFAULTS, **raw}
    except Exception:
        return dict(DEFAULTS)


def get(key):
    """Single setting value (None for unknown keys)."""
    return load().get(key)


def all():
    """Alias for load() — the full merged settings dict."""
    return load()


def set(key, value):
    """Persist one setting (writes the full merged dict back to disk)."""
    data = load()
    data[key] = value
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data, indent=2))
    return data
