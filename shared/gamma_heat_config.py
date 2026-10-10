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
    "show": {
        # The heatmap's "Change over N min" view: each cell less the same
        # strike's cell this many minutes earlier.
        "change_window_min": 30,
    },
    "window": {
        # Strikes shown each side of spot on the bars and the heatmap, and kept
        # each side of the current spot in the published history. One number
        # for both tiers (it was a literal 20 in each until 2026-10-09).
        "n_side": 20,
        # Strikes drawn each side of price in the heatmap's "From spot" frame,
        # and kept each side of the session's LOW and HIGH in the published
        # history so that frame is never short of data. Every strike here is
        # paid for in each view's history, every minute: 10 measured about +12%
        # on $SPX's widest stored day and 20 about +40%
        # (tools/measure_gamma_crop.py). 0 keeps no extra strikes.
        "spot_side": 10,
    },
    "contours": {
        # Contour lines each side of zero: the top of the colour scale and this
        # many levels in all, each half the one above (3 = full, half, quarter).
        "steps": 3,
        # A line spanning fewer columns (minutes) than this is a speck and is
        # not drawn. 0 draws everything.
        "min_columns": 3,
        # The most points one sign's lines may hold; past it the shortest lines
        # are dropped. It bounds what each repaint sends to the browser.
        "max_points": 6000,
    },
    "well": {
        # The gravity well's height: "root" draws each strike at the signed
        # square root of its net gamma, so the largest strike does not flatten
        # the rest; "linear" draws it in proportion.
        "height": "root",
    },
    "ridge": {
        # The ridge plot draws one by-strike profile every this many minutes.
        "every_min": 30,
        # How many rows the TYPICAL profile's tallest peak spans (the median
        # across the profiles drawn). Higher overlaps the ridges more. A
        # profile several times the rest is drawn in proportion, past them.
        "overlap": 2.0,
        # As the well's: "root" draws the square root of each value's size, so
        # the largest strike does not flatten the rest; "linear" is in proportion.
        "height": "root",
    },
}

WELL_HEIGHTS = ("root", "linear")

load, reset_cache = toml_loader(GAMMA_HEAT_TOML, DEFAULTS, label="gamma_heat.toml")


def _setting(section, key, *, minimum, maximum=None):
    """A config number of the default's own type inside its range, or the
    default. A bool is refused: ``True`` is an int and would read as 1."""
    default = DEFAULTS[section][key]
    table = load().get(section)
    raw = table.get(key, default) if isinstance(table, dict) else default
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


def change_window_min() -> int:
    """Minutes the heatmap's "Change over N min" view looks back. Read by the
    page each time it draws."""
    return _setting("show", "change_window_min", minimum=5, maximum=240)


def n_side() -> int:
    """Strikes each side of spot in the display window. Read at CALL time by the
    page and by options_svc, so the two tiers agree within a minute of a change
    and neither needs a restart."""
    return _setting("window", "n_side", minimum=4, maximum=60)


def spot_side() -> int:
    """Strikes each side of price in the "From spot" frame, and each side of the
    session's low and high in the published history. Never more than
    ``n_side``. 0 turns the wider history crop off."""
    return min(_setting("window", "spot_side", minimum=0, maximum=60), n_side())


def contours() -> dict:
    """``{"steps", "min_columns", "max_points"}`` for the heatmap's contour
    lines (``pages/options/gamma_heat.contours``). Read by the page each time it
    draws with the Contours switch on."""
    return {"steps": _setting("contours", "steps", minimum=1, maximum=5),
            "min_columns": _setting("contours", "min_columns", minimum=0, maximum=60),
            "max_points": _setting("contours", "max_points",
                                   minimum=500, maximum=40000)}


def well_height() -> str:
    """How the gravity well draws height: one of ``WELL_HEIGHTS``. Anything
    else is the default. Read by the page each time it draws."""
    table = load().get("well")
    value = table.get("height") if isinstance(table, dict) else None
    return value if value in WELL_HEIGHTS else DEFAULTS["well"]["height"]


def ridge() -> dict:
    """``{"every_min", "overlap", "height"}`` for the ridge plot
    (``pages/options/gamma_ridge``). Read by the page each time it draws."""
    table = load().get("ridge")
    height = table.get("height") if isinstance(table, dict) else None
    return {"every_min": _setting("ridge", "every_min", minimum=5, maximum=120),
            "overlap": _setting("ridge", "overlap", minimum=0.5, maximum=8.0),
            "height": height if height in WELL_HEIGHTS
            else DEFAULTS["ridge"]["height"]}


def lock() -> dict:
    """``{"minutes", "quantile", "headroom"}`` for the service's scale lock
    (``services/options_svc/gamma_window.scale_lock``): the heatmap's colour
    maximum is set once a session from its first ``minutes``."""
    return {"minutes": _setting("lock", "minutes", minimum=5, maximum=390),
            "quantile": _setting("lock", "quantile", minimum=0.5, maximum=1.0),
            "headroom": _setting("lock", "headroom", minimum=1.0, maximum=5.0)}
