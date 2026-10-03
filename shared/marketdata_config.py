"""The proxy's local market-data store settings, from ``config/marketdata.toml``.

Read by three things that cannot import each other: ``schwab-proxy`` (the
store), ``options-scanner`` (the collector's tiers and the scan's wide fetch)
and ``services/options_svc``. Every accessor reads at CALL time, so a saved
override applies without a restart.

Missing file / bad TOML / missing key -> the built-in defaults, never a raise.
"""
import math

from repo_paths import MARKETDATA_TOML
from shared.config_toml import toml_loader

MODES = ("off", "shadow", "on")
TODAY_BARS = ("ttl", "quote")

DEFAULTS = {
    "mode": "shadow",
    "chains": {
        "enabled": True,
        "max_age_sec": 45,
        "closed_max_age_sec": 1800,
        "max_entries": 400,
        "shadow_compare_max_age_sec": 120,
        "wide_refetch_max_days": 10,
    },
    "quotes": {"enabled": True, "max_age_sec": 5, "max_symbols": 5000},
    "bars": {
        "enabled": True,
        "today_bar": "ttl",
        "session_ttl_sec": 1740,
        "today_quote_max_age_sec": 120,
        "settle_min": 10,
        "max_entries": 4000,
    },
    "scan": {"wide_fetch": False, "wide_fetch_exclude": ["$SPX", "$NDX", "SPY", "QQQ"]},
    "collection": {"tail_interval_min": 1, "fresh_max_age_sec": 20},
}

load, reset_cache = toml_loader(MARKETDATA_TOML, DEFAULTS, label="marketdata.toml")


def mode() -> str:
    """``off`` / ``shadow`` / ``on``. Anything else is ``off``: a typo in this
    file must never switch local answers on."""
    m = load().get("mode")
    return m if isinstance(m, str) and m in MODES else "off"


def _usable(value, default):
    """``value`` if it is the same kind of thing as ``default`` and usable."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        try:
            finite = math.isfinite(value)
        except OverflowError:       # an int too large to be a float
            return default
        return value if finite and value >= 0 else default
    if isinstance(default, list):
        return value if isinstance(value, list) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return value


def section(name: str) -> dict:
    """One table of the file with every value checked against its default's
    type. A table an override replaced with a scalar reads as the defaults,
    except a store's ``enabled``, which is always :func:`store_on`'s answer so
    the two readers of one switch cannot disagree."""
    base = DEFAULTS.get(name)
    if not isinstance(base, dict):
        return {}
    got = load().get(name)
    if not isinstance(got, dict):
        got = {}
    out = {k: _usable(got.get(k, d), d) for k, d in base.items()}
    if "enabled" in base:
        out["enabled"] = store_on(name)
    return out


def store_on(name: str) -> bool:
    """Whether one store (``chains`` / ``quotes`` / ``bars``) is switched on.
    Must be literally ``true``: a hand-typed ``"false"`` is a truthy string."""
    base = DEFAULTS.get(name)
    if not isinstance(base, dict) or "enabled" not in base:
        return False
    got = load().get(name)
    return isinstance(got, dict) and got.get("enabled") is True


def today_bar() -> str:
    """How today's daily bar is handled: one of :data:`TODAY_BARS`. Anything
    else is ``ttl``, the behaviour that needs no live quote."""
    v = section("bars")["today_bar"]
    return v if v in TODAY_BARS else "ttl"
