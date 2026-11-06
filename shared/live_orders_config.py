"""The live-order settings, from ``config/live_orders.toml``.

Design: docs/plans/2026-11-05-live-order-to-thinkorswim-design.md.

Read by the proxy on EVERY request (``mode()``), so turning the switch off takes
effect on the next request, with no restart. Stdlib + ``shared.config_toml`` +
``repo_paths`` only.

Missing file, bad TOML or a bad value -> the built-in defaults, never a raise.
The default is ``off``, and anything that is not exactly one of :data:`MODES`
reads as ``off``: a typo in this file must never let an order through.
"""
import math

from repo_paths import LIVE_ORDERS_TOML
from shared.config_toml import toml_loader

#: Phase 0 has two modes. ``preview`` lets the proxy ask Schwab to CHECK an
#: order (``previewOrder``). No mode places one: that route does not exist yet.
MODES = ("off", "preview")

DEFAULTS = {
    "mode": "off",
    "ticks": {
        "under_3": 0.05,
        "from_3": 0.10,
        "penny_under_3": 0.01,
        "penny_from_3": 0.05,
        "penny": ["SPY", "QQQ", "IWM"],
    },
}

load, reset_cache = toml_loader(LIVE_ORDERS_TOML, DEFAULTS, label="live_orders.toml")


def mode() -> str:
    """``off`` / ``preview``. Anything else is ``off``."""
    m = load().get("mode")
    return m if isinstance(m, str) and m in MODES else "off"


def preview_allowed() -> bool:
    """Whether the proxy may send Schwab an order to CHECK."""
    return mode() == "preview"


def _step(value, default):
    """A usable price step: a finite number above 0 and at most $1."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(value) or not 0 < value <= 1:
        return default
    return float(value)


def ticks() -> dict:
    """The ``[ticks]`` table with every value checked; never raises."""
    base = DEFAULTS["ticks"]
    got = load().get("ticks")
    got = got if isinstance(got, dict) else {}
    out = {k: _step(got.get(k, d), d) for k, d in base.items() if k != "penny"}
    penny = got.get("penny", base["penny"])
    if not isinstance(penny, list) or not all(isinstance(s, str) for s in penny):
        penny = base["penny"]
    out["penny"] = [s.strip().upper() for s in penny if s.strip()]
    return out
