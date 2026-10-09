"""``config/gamma_heat.toml``: the Dealer Positioning heatmap's tunables.

Read by the page (the balanced-strike markers) and by options_svc (the scale
lock). Stdlib, ``shared.config_toml`` and ``repo_paths`` only: Tier 1 imports it, and
``shared/tests/test_gamma_heat_config.py`` pins that set. Design:
docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md
"""
from repo_paths import GAMMA_HEAT_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "balanced": {
        # A strike is "balanced" when abs(net) / (abs(calls) + abs(puts)) is at
        # or under this...
        "max_polarity": 0.15,
        # ...and its size is at or above this quantile of the strikes on screen.
        "min_size_quantile": 0.8,
        # At most this many markers, largest first. 0 turns them off.
        "max_marks": 3,
    },
    "lock": {
        # The colour scale is fixed from the session's first this-many minutes.
        "minutes": 60,
        # The quantile of the absolute cell taken over those minutes.
        "quantile": 0.95,
        # Multiplied on, so the first hour's largest cells are not already at
        # the top of the scale.
        "headroom": 1.5,
    },
}

load, reset_cache = toml_loader(GAMMA_HEAT_TOML, DEFAULTS, label="gamma_heat.toml")


def _setting(section, key, *, minimum, maximum=None):
    """A config number of the default's own type inside its range, or the
    default. A bool is refused: ``True`` is an int and would read as 1."""
    default = DEFAULTS[section][key]
    raw = (load().get(section) or {}).get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    value = type(default)(raw)
    if value < minimum or (maximum is not None and value > maximum):
        return default
    return value


def balanced() -> dict:
    """``{"max_polarity", "min_size_quantile", "max_marks"}``: which strikes the
    heatmap marks as holding large calls and large puts that nearly cancel."""
    return {"max_polarity": _setting("balanced", "max_polarity",
                                     minimum=0.0, maximum=1.0),
            "min_size_quantile": _setting("balanced", "min_size_quantile",
                                          minimum=0.0, maximum=1.0),
            "max_marks": _setting("balanced", "max_marks", minimum=0)}


def lock() -> dict:
    """``{"minutes", "quantile", "headroom"}`` for the service's scale lock
    (``services/options_svc/gamma_window.scale_lock``): the heatmap's colour
    maximum is set once a session from its first ``minutes``."""
    return {"minutes": _setting("lock", "minutes", minimum=5, maximum=390),
            "quantile": _setting("lock", "quantile", minimum=0.5, maximum=1.0),
            "headroom": _setting("lock", "headroom", minimum=1.0, maximum=5.0)}
