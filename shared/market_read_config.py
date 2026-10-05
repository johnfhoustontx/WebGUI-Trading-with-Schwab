"""The Market read scorecard's settings, from ``config/market_read.toml``.

Read by ``services/market_svc`` on every slot, so a saved override applies
without a restart. Tier 1 does not read it: what the Desk needs (the interval)
rides in the published view.

Missing file / bad TOML / missing key / unusable value -> the built-in default
for that key, never a raise. ``load()`` returns a fresh, cleaned mapping.
Design: docs/plans/2026-10-05-market-read-scorecard-design.md.
"""
from repo_paths import MARKET_READ_TOML
from shared.config_toml import toml_loader
from shared.numeric import finite

# A reading is taken on clock multiples of this, and the choice is deliberately
# two values: the slots are named by the clock ("12:45"), and the Desk greys a
# reading two intervals old.
INTERVALS = (15, 30)

DEFAULTS = {
    "enabled": True,
    "public": True,
    "interval_min": 15,
    "stale_after_sec": 300,
    "direction": {"move_pct": 0.25},
    "breadth": {"strong_share": 0.60, "weak_share": 0.40},
    "structure": {"symbols": ["SPY", "QQQ"], "room_pct": 0.50, "near_pct": 0.25},
    "volatility": {"vix_move_pct": 1.0},
    "flow": {"lean_pts": 5.0, "min_contracts": 10},
}

load_raw, reset_cache = toml_loader(MARKET_READ_TOML, DEFAULTS, label="market_read.toml")


def _table(raw, name) -> dict:
    """``raw[name]`` when it is a table, else that table's defaults: a scalar
    override (``flow = 5``) must not take the whole section down."""
    got = raw.get(name)
    return got if isinstance(got, dict) else DEFAULTS[name]


def _at_least(value, default, floor=0.0):
    """``value`` as a float when it is a finite number at or above ``floor``,
    else ``default``. Text is not parsed and a bool is not a number."""
    f = finite(value)
    return default if f is None or f < floor else f


def load() -> dict:
    """The settings, cleaned. Every switch is on only for a literal ``true``."""
    raw = load_raw()
    if not isinstance(raw, dict):
        raw = DEFAULTS
    out = {"enabled": raw.get("enabled") is True, "public": raw.get("public") is True}

    interval = finite(raw.get("interval_min"))
    out["interval_min"] = int(interval) if interval in INTERVALS else DEFAULTS["interval_min"]
    stale = finite(raw.get("stale_after_sec"))
    out["stale_after_sec"] = (stale if stale is not None and stale > 0
                              else DEFAULTS["stale_after_sec"])

    d = DEFAULTS
    out["direction"] = {"move_pct": _at_least(
        _table(raw, "direction").get("move_pct"), d["direction"]["move_pct"])}

    b = _table(raw, "breadth")
    strong, weak = finite(b.get("strong_share")), finite(b.get("weak_share"))
    usable = (strong is not None and weak is not None and 0 <= weak < strong <= 1)
    out["breadth"] = ({"strong_share": strong, "weak_share": weak} if usable
                      else dict(d["breadth"]))

    s = _table(raw, "structure")
    names = s.get("symbols")
    symbols = ([x.strip() for x in names if isinstance(x, str) and x.strip()]
               if isinstance(names, list) else [])
    out["structure"] = {
        "symbols": symbols or list(d["structure"]["symbols"]),
        "room_pct": _at_least(s.get("room_pct"), d["structure"]["room_pct"]),
        "near_pct": _at_least(s.get("near_pct"), d["structure"]["near_pct"])}

    out["volatility"] = {"vix_move_pct": _at_least(
        _table(raw, "volatility").get("vix_move_pct"), d["volatility"]["vix_move_pct"])}

    f = _table(raw, "flow")
    floor = finite(f.get("min_contracts"))
    out["flow"] = {
        "lean_pts": _at_least(f.get("lean_pts"), d["flow"]["lean_pts"]),
        "min_contracts": (int(floor) if floor is not None and floor >= 1
                          else d["flow"]["min_contracts"])}
    return out
