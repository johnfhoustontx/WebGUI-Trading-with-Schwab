"""The live-order settings, from ``config/live_orders.toml``.

Design: docs/plans/2026-11-05-live-order-to-thinkorswim-design.md.

Read by the proxy, the options service and the ticket page on EVERY request,
so a saved change (turning the switch off above all) applies to the next one
with no restart. Stdlib + ``shared.config_toml`` + ``repo_paths`` only, so Tier 1
may import it.

Missing file, bad TOML or a bad value -> the built-in defaults, never a raise.
The default mode is ``off``, and anything that is not exactly one of
:data:`MODES` reads as ``off``: a typo in this file must never let an order out.
"""
import math
import re

from repo_paths import LIVE_ORDERS_TOML
from shared.config_toml import toml_loader

#: ``off`` refuses everything. ``preview`` lets the proxy ask Schwab to CHECK
#: an order. ``live`` also lets Send place one.
MODES = ("off", "preview", "live")
TOTP_MODES = ("daily", "every_send")

DEFAULTS = {
    "mode": "off",
    "default_risk_usd": 1000.0,
    "max_contracts": 50,
    "max_risk_usd": 5000.0,
    "max_drift_pct": 10.0,
    "totp": "daily",
    "ticket_ttl_sec": 120,
    "max_command_age_sec": 30,
    "status_poll_sec": 5,
    "prompt": {"banner": True, "banner_max_age_min": 30},
    "ticks": {
        "under_3": 0.05,
        "from_3": 0.10,
        "penny_under_3": 0.01,
        "penny_from_3": 0.05,
        "penny": ["SPY", "QQQ", "IWM"],
    },
}

#: (low, high) for each number; outside it the default is used.
_RANGES = {
    "default_risk_usd": (1.0, 1_000_000.0),
    "max_contracts": (1, 1000),
    "max_risk_usd": (1.0, 1_000_000.0),
    "max_drift_pct": (0.1, 100.0),
    "ticket_ttl_sec": (10, 600),
    "max_command_age_sec": (5, 300),
    "status_poll_sec": (1, 60),
}
_INTS = {"max_contracts", "ticket_ttl_sec", "max_command_age_sec", "status_poll_sec"}
_LAST4_RE = re.compile(r"^\d{4}$")
_ALIAS_MAX = 40

load, reset_cache = toml_loader(LIVE_ORDERS_TOML, DEFAULTS, label="live_orders.toml")


def mode() -> str:
    """``off`` / ``preview`` / ``live``. Anything else is ``off``."""
    m = load().get("mode")
    return m if isinstance(m, str) and m in MODES else "off"


def preview_allowed() -> bool:
    """Whether the proxy may send Schwab an order to CHECK."""
    return mode() in ("preview", "live")


def place_allowed() -> bool:
    """Whether the proxy may PLACE an order."""
    return mode() == "live"


def _number(key):
    raw = load().get(key)
    lo, hi = _RANGES[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULTS[key]
    if not math.isfinite(raw) or not lo <= raw <= hi:
        return DEFAULTS[key]
    if key in _INTS:
        return int(raw) if raw == int(raw) else DEFAULTS[key]
    return float(raw)


def default_risk_usd() -> float:
    return _number("default_risk_usd")


def max_contracts() -> int:
    return _number("max_contracts")


def max_risk_usd() -> float:
    return _number("max_risk_usd")


def max_drift_pct() -> float:
    return _number("max_drift_pct")


def ticket_ttl_sec() -> int:
    return _number("ticket_ttl_sec")


def max_command_age_sec() -> int:
    return _number("max_command_age_sec")


def status_poll_sec() -> int:
    return _number("status_poll_sec")


def totp_mode() -> str:
    """``daily`` or ``every_send``; anything else is ``daily``."""
    v = load().get("totp")
    return v if isinstance(v, str) and v in TOTP_MODES else "daily"


def prompt() -> dict:
    """``{"banner": bool, "banner_max_age_min": int}``."""
    got = load().get("prompt")
    got = got if isinstance(got, dict) else {}
    base = DEFAULTS["prompt"]
    banner = got.get("banner", base["banner"])
    age = got.get("banner_max_age_min", base["banner_max_age_min"])
    if isinstance(age, bool) or not isinstance(age, int) or not 1 <= age <= 1440:
        age = base["banner_max_age_min"]
    return {"banner": banner is True, "banner_max_age_min": age}


def accounts() -> list:
    """The accounts orders may go to: ``[{"last4", "alias", "default"}]``, in
    file order, with exactly one ``default`` (the first marked, else the first
    listed). An entry with a bad ``last4`` or no alias is dropped, and so is a
    repeat of a ``last4`` or an alias. Empty when none are configured, which is
    how the tracked file ships."""
    raw = load().get("accounts")
    if not isinstance(raw, list):
        return []
    out, seen_last4, seen_alias = [], set(), set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        last4 = entry.get("last4")
        alias = entry.get("alias")
        if not isinstance(last4, str) or not _LAST4_RE.match(last4):
            continue
        if not isinstance(alias, str) or not alias.strip() or len(alias.strip()) > _ALIAS_MAX:
            continue
        alias = alias.strip()
        if last4 in seen_last4 or alias.lower() in seen_alias:
            continue
        seen_last4.add(last4)
        seen_alias.add(alias.lower())
        out.append({"last4": last4, "alias": alias, "default": entry.get("default") is True})
    marked = [a for a in out if a["default"]]
    first = marked[0] if marked else (out[0] if out else None)
    for a in out:
        a["default"] = a is first
    return out


def aliases() -> list:
    """The configured aliases, default first. Safe for Tier 1: no digits."""
    accts = accounts()
    return [a["alias"] for a in sorted(accts, key=lambda a: not a["default"])]


def last4_for(alias: str):
    """The ``last4`` configured for ``alias``, or None. Tier 2 only."""
    for a in accounts():
        if a["alias"] == alias:
            return a["last4"]
    return None


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
