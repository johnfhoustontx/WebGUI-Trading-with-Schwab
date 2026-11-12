"""``config/tos_order.toml``: the paste script's size, exits' time in force,
stop type and exit percentages, validated once for both tiers.

The Trade detail panel's Copy ThinkorSwim order (``webgui/pages/options/
tos_order.py``, Tier 1) and the live-order service (``services/options_svc/
live_order_handlers.py``, Tier 2) must price the same exits from the same file,
so the validation lives here and each tier hands it the dict it loaded.

Stdlib + ``repo_paths`` + ``shared.config_toml`` only (it is on the Tier-1
allow-list). Never raises.
"""
import math

from repo_paths import TOS_ORDER_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "quantity": 1,
    "exit_tif": "GTC",
    "stop_type": "trailing",
    "debit": {"target_pct": 100.0, "stop_pct": 50.0, "trail_pct": 50.0},
    "credit": {"target_pct": 50.0, "stop_pct": 100.0, "trail_pct": 100.0},
}

# The exits' time in force. DAY writes no word in ThinkorSwim's text.
EXIT_TIFS = ("GTC", "DAY")

# The paste script's stop: "trailing" follows the mark by ``trail_pct``;
# "fixed" sits at ``stop_pct`` from the fill. The live-order service always
# prices its stop from ``stop_pct`` and never reads this.
STOP_TYPES = ("trailing", "fixed")

load, reset_cache = toml_loader(TOS_ORDER_TOML, DEFAULTS, label="tos_order.toml")


def _pct(raw_cfg, table, key, *, below_100):
    """A percentage from ``[table]``, or the default for a missing or bad one.
    A percentage BELOW the entry must stay under 100: at 100 the price is zero."""
    raw = ((raw_cfg or {}).get(table) or {}).get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) \
            or not math.isfinite(raw) or raw <= 0:
        return DEFAULTS[table][key]
    too_big = (raw >= 100) if below_100 else (raw > 1000)
    if too_big:
        return DEFAULTS[table][key]
    return float(raw)


def settings_from(raw_cfg):
    """The validated settings from a loaded ``tos_order.toml`` dict."""
    raw_cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
    q = raw_cfg.get("quantity")
    if isinstance(q, bool) or not isinstance(q, (int, float)) or q != int(q) \
            or not 1 <= q <= 1000:
        q = DEFAULTS["quantity"]
    tif = raw_cfg.get("exit_tif")
    tif = tif.strip().upper() if isinstance(tif, str) else None
    if tif not in EXIT_TIFS:
        tif = DEFAULTS["exit_tif"]
    kind = raw_cfg.get("stop_type")
    kind = kind.strip().lower() if isinstance(kind, str) else None
    if kind not in STOP_TYPES:
        kind = DEFAULTS["stop_type"]
    # A debit's stop and trail sit BELOW the price, so each stays under 100.
    return {
        "quantity": int(q),
        "exit_tif": tif,
        "stop_type": kind,
        "debit": {"target_pct": _pct(raw_cfg, "debit", "target_pct", below_100=False),
                  "stop_pct": _pct(raw_cfg, "debit", "stop_pct", below_100=True),
                  "trail_pct": _pct(raw_cfg, "debit", "trail_pct", below_100=True)},
        "credit": {"target_pct": _pct(raw_cfg, "credit", "target_pct", below_100=True),
                   "stop_pct": _pct(raw_cfg, "credit", "stop_pct", below_100=False),
                   "trail_pct": _pct(raw_cfg, "credit", "trail_pct", below_100=False)},
    }


def settings():
    """The validated settings, read from the file."""
    return settings_from(load())
