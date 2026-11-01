"""The Market read scorecard's settings, from ``config/market_read.toml``.

Read by ``services/market_svc`` on every poll, so a saved override (a switch
above all) applies within seconds and without a restart. Tier 1 does not read it: what the Desk needs (the interval,
and how long its two header buttons glow after an update) rides in the published view.

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
    "dashboard_stale_after_sec": 60,
    "retry_sec": 30,
    # Minutes the Desk's Market read and Market report buttons glow after a
    # new reading or report. 0 = never.
    "glow_min": 5,
    "direction": {"move_pct": 0.25},
    "breadth": {"strong_share": 0.60, "weak_share": 0.40, "min_tiles": 10},
    # ``stale_after_sec`` mirrors the Desk's dealer panel (desk.STALE_AFTER_SEC,
    # pinned by test_cross_tier_mirrors): the two must grey the same levels.
    "structure": {"symbols": ["SPY", "QQQ"], "room_pct": 0.50, "near_pct": 0.25,
                  "stale_after_sec": 150},
    "volatility": {"vix_move_pct": 1.0},
    "flow": {"lean_pts": 5.0, "min_contracts": 10},
    # Reads the market service's own ``cache:market:volume``, which the daily
    # market reports read too. ``baseline_sessions`` tops out at 9: Schwab serves
    # ten days of minute bars, and one of them is today.
    "volume": {"enabled": True, "symbols": ["SPY", "QQQ"],
               "sector_symbols": ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP",
                                  "XLRE", "XLU", "XLV", "XLY"],
               "baseline_sessions": 9, "min_sessions": 5, "min_bars": 2,
               "heavy_ratio": 1.2, "light_ratio": 0.8,
               "up_share_strong": 0.55, "up_share_weak": 0.45, "min_sectors": 8,
               "refresh_min": 5, "settle_sec": 90, "stale_after_sec": 600},
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


def _whole(value, default, low, high):
    """``value`` as an int when it is a finite number in ``low..high``, else ``default``."""
    f = finite(value)
    return int(f) if f is not None and low <= f <= high else default


def _names(value, default):
    names = ([x.strip() for x in value if isinstance(x, str) and x.strip()]
             if isinstance(value, list) else [])
    return names or list(default)


def _volume(raw) -> dict:
    d = DEFAULTS["volume"]
    v = _table(raw, "volume")
    heavy = finite(v.get("heavy_ratio"))
    light = finite(v.get("light_ratio"))
    # Each pair together or not at all, like the breadth shares: crossed bounds
    # would make one reading both.
    if heavy is None or light is None or not 0 < light < heavy:
        heavy, light = d["heavy_ratio"], d["light_ratio"]
    strong = finite(v.get("up_share_strong"))
    weak = finite(v.get("up_share_weak"))
    if strong is None or weak is None or not 0 <= weak < strong <= 1:
        strong, weak = d["up_share_strong"], d["up_share_weak"]
    sessions = _whole(v.get("baseline_sessions"), d["baseline_sessions"], 1, 9)
    least = _whole(v.get("min_sessions"), d["min_sessions"], 1, 9)
    limit = finite(v.get("stale_after_sec"))
    return {
        "enabled": v.get("enabled") is True,
        "symbols": _names(v.get("symbols"), d["symbols"]),
        "sector_symbols": _names(v.get("sector_symbols"), d["sector_symbols"]),
        "baseline_sessions": sessions,
        # More required than are ever asked for would be "No reading" for ever.
        "min_sessions": min(least, sessions),
        "min_bars": _whole(v.get("min_bars"), d["min_bars"], 1, 78),
        "heavy_ratio": heavy, "light_ratio": light,
        "up_share_strong": strong, "up_share_weak": weak,
        "min_sectors": _whole(v.get("min_sectors"), d["min_sectors"], 1, 50),
        "refresh_min": _whole(v.get("refresh_min"), d["refresh_min"], 5, 30),
        # Under a minute would land in the minute after the hour, where Schwab
        # refuses calls.
        "settle_sec": _whole(v.get("settle_sec"), d["settle_sec"], 60, 240),
        "stale_after_sec": (limit if limit is not None and limit > 0
                            else d["stale_after_sec"]),
    }


def load() -> dict:
    """The settings, cleaned. Every switch is on only for a literal ``true``."""
    raw = load_raw()
    if not isinstance(raw, dict):
        raw = DEFAULTS
    out = {"enabled": raw.get("enabled") is True, "public": raw.get("public") is True}

    interval = finite(raw.get("interval_min"))
    out["interval_min"] = int(interval) if interval in INTERVALS else DEFAULTS["interval_min"]
    for key in ("stale_after_sec", "dashboard_stale_after_sec", "retry_sec"):
        secs = finite(raw.get(key))
        out[key] = secs if secs is not None and secs > 0 else DEFAULTS[key]

    out["glow_min"] = _at_least(raw.get("glow_min"), DEFAULTS["glow_min"])

    d = DEFAULTS
    out["direction"] = {"move_pct": _at_least(
        _table(raw, "direction").get("move_pct"), d["direction"]["move_pct"])}

    b = _table(raw, "breadth")
    strong, weak = finite(b.get("strong_share")), finite(b.get("weak_share"))
    usable = (strong is not None and weak is not None and 0 <= weak < strong <= 1)
    out["breadth"] = ({"strong_share": strong, "weak_share": weak} if usable
                      else {k: d["breadth"][k] for k in ("strong_share", "weak_share")})
    tiles = finite(b.get("min_tiles"))
    out["breadth"]["min_tiles"] = (int(tiles) if tiles is not None and tiles >= 1
                                   else d["breadth"]["min_tiles"])

    s = _table(raw, "structure")
    names = s.get("symbols")
    symbols = ([x.strip() for x in names if isinstance(x, str) and x.strip()]
               if isinstance(names, list) else [])
    room = _at_least(s.get("room_pct"), d["structure"]["room_pct"])
    near = _at_least(s.get("near_pct"), d["structure"]["near_pct"])
    if not near < room:
        # Crossed bounds leave no neutral band and make "near" win over "room":
        # the pair is taken together or not at all, like the breadth shares.
        room, near = d["structure"]["room_pct"], d["structure"]["near_pct"]
    limit = finite(s.get("stale_after_sec"))
    out["structure"] = {
        "symbols": symbols or list(d["structure"]["symbols"]),
        "room_pct": room, "near_pct": near,
        "stale_after_sec": (limit if limit is not None and limit > 0
                            else d["structure"]["stale_after_sec"])}

    out["volatility"] = {"vix_move_pct": _at_least(
        _table(raw, "volatility").get("vix_move_pct"), d["volatility"]["vix_move_pct"])}

    f = _table(raw, "flow")
    floor = finite(f.get("min_contracts"))
    out["flow"] = {
        "lean_pts": _at_least(f.get("lean_pts"), d["flow"]["lean_pts"]),
        "min_contracts": (int(floor) if floor is not None and floor >= 1
                          else d["flow"]["min_contracts"])}
    out["volume"] = _volume(raw)
    return out
