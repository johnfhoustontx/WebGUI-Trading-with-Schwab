"""``config/scalp.toml``: the Opening Scalp's tunables.

Design: docs/plans/2026-10-18-opening-scalp-design.md. Read per call through the
mtime-cached loader, so a saved change applies with no restart. Every accessor
returns its built-in default for a missing or unusable value and never raises.
The per-symbol tables (``width``, ``strike_step``, ``inputs``) are keyed by the
symbol: an unknown symbol reads as the SPY-like default for a width or a strike
step (1), and as ABSENT (``None`` / empty) for an input, never borrowed from
another symbol and never zero. Stdlib, ``shared.config_toml`` and ``repo_paths``
only, so Tier 1 may read it as well.
"""
import math

from repo_paths import SCALP_TOML
from shared.config_toml import toml_loader

#: What an unlisted symbol's width and strike step read as: the SPY-like 1.00.
SPY_LIKE_STEP = 1.0

_BUCKETS = [0.4, 0.7]
_IV_SCALES = [0.8, 1.0, 1.25, 1.5]

DEFAULTS = {
    "universe": {"symbols": ["$SPX", "SPY", "QQQ"]},
    "bars": {"symbols": ["$SPX", "SPY", "QQQ", "/ES", "/NQ", "IGV", "SMH", "EWY",
                         "XLE", "XLK", "XLV", "$VIX", "$VXN"]},
    "strike_step": {"$SPX": 5, "SPY": 1, "QQQ": 1},
    "width": {"$SPX": 5, "SPY": 1, "QQQ": 1},
    "inputs": {
        "$SPX": {"futures": "/ES", "etfs": ["XLE", "XLK", "XLV"], "vol": "$VIX"},
        "SPY": {"futures": "/ES", "etfs": ["XLE", "XLK", "XLV"], "vol": "$VIX"},
        "QQQ": {"futures": "/NQ", "etfs": ["IGV", "SMH", "EWY", "XLK"], "vol": "$VXN"},
    },
    "trade": {"otm_steps": 0, "target_pct": 50, "stop_pct": 50, "time_stop_min": 60,
              "iv_scale": 1.0, "min_premium": 0.05, "chain_strikes": 10},
    "direction": {"dead_zone_pct": 0.05, "dead_zone_frac": 0.1,
                  "open_dead_zone_pct": 0.03, "buckets": _BUCKETS},
    "study": {"iv_scales": _IV_SCALES, "min_samples": 20, "scorecard_days": 20},
    "news": {"items": 5},
}

load, reset_cache = toml_loader(SCALP_TOML, DEFAULTS, label="scalp.toml")


def _section(name) -> dict:
    cfg = load()
    sec = cfg.get(name) if isinstance(cfg, dict) else None
    return sec if isinstance(sec, dict) else {}


def _real(raw) -> bool:
    """A finite number that is not a bool."""
    return (not isinstance(raw, bool) and isinstance(raw, (int, float))
            and math.isfinite(raw))


def _number(section, key, *, floor=0.0, strict=True) -> float:
    """A finite number above ``floor`` (``strict``) or at it, else the default."""
    raw = _section(section).get(key)
    default = float(DEFAULTS[section][key])
    if not _real(raw) or (raw <= floor if strict else raw < floor):
        return default
    return float(raw)


def _count(section, key, *, minimum=1) -> int:
    """A whole number of at least ``minimum``, else the default."""
    raw = _section(section).get(key)
    if not _real(raw) or raw != int(raw) or raw < minimum:
        return DEFAULTS[section][key]
    return int(raw)


def _clean(symbol) -> str:
    return symbol.strip().upper() if isinstance(symbol, str) else ""


def _symbol_list(raw) -> list:
    """Upper-cased, de-duplicated tickers from a list; non-strings and empties
    are skipped. Not a list gives an empty one."""
    out: list = []
    for item in raw if isinstance(raw, list) else []:
        sym = _clean(item)
        if sym and sym not in out:
            out.append(sym)
    return out


# ── the universe ─────────────────────────────────────────────────────────────
def symbols() -> list:
    """The symbols the page calls: ``["$SPX", "SPY", "QQQ"]``."""
    return _symbol_list(_section("universe").get("symbols")) \
        or list(DEFAULTS["universe"]["symbols"])


def inputs_for(symbol) -> dict:
    """What the direction call reads for ``symbol``:
    ``{"futures": str | None, "etfs": [str], "vol": str | None}``.

    An unknown symbol, or an entry field that is not usable, is ABSENT (``None``
    / empty). Returns a fresh dict each call."""
    entry = _section("inputs").get(_clean(symbol))
    entry = entry if isinstance(entry, dict) else {}
    return {"futures": _clean(entry.get("futures")) or None,
            "etfs": _symbol_list(entry.get("etfs")),
            "vol": _clean(entry.get("vol")) or None}


def bar_symbols() -> list:
    """Every symbol whose bars are stored: ``[bars] symbols``, then any symbol or
    input the other tables name that list forgot, so a changed input is always
    fetched."""
    out = _symbol_list(_section("bars").get("symbols")) \
        or list(DEFAULTS["bars"]["symbols"])
    for sym in symbols():
        wanted = [sym]
        got = inputs_for(sym)
        wanted += [got["futures"], *got["etfs"], got["vol"]]
        for w in wanted:
            if w and w not in out:
                out.append(w)
    return out


# ── per-symbol sizes ─────────────────────────────────────────────────────────
def _per_symbol(table, symbol) -> float:
    raw = _section(table).get(_clean(symbol))
    if _real(raw) and raw > 0:
        return float(raw)
    default = DEFAULTS[table].get(_clean(symbol))
    return float(default) if default is not None else SPY_LIKE_STEP


def strike_step(symbol) -> float:
    """Dollars between listed strikes ($SPX 5, SPY 1, QQQ 1)."""
    return _per_symbol("strike_step", symbol)


def width(symbol) -> float:
    """Credit-spread width in dollars ($SPX 5, SPY 1, QQQ 1)."""
    return _per_symbol("width", symbol)


# ── the trade ────────────────────────────────────────────────────────────────
def otm_steps() -> int:
    """Strikes out of the money for the long option; 0 is at the money."""
    return _count("trade", "otm_steps", minimum=0)


def target_pct() -> float:
    return _number("trade", "target_pct")


def stop_pct() -> float:
    return _number("trade", "stop_pct")


def time_stop_min() -> int:
    return _count("trade", "time_stop_min")


def iv_scale() -> float:
    return _number("trade", "iv_scale")


def min_premium() -> float:
    return _number("trade", "min_premium", floor=0.0, strict=False)


def chain_strikes() -> int:
    return _count("trade", "chain_strikes")


# ── the direction call ───────────────────────────────────────────────────────
def dead_zone_pct() -> float:
    """Returns (percent) below this are flat."""
    return _number("direction", "dead_zone_pct", strict=False)


def dead_zone_frac() -> float:
    """A 0..1 position within this of the middle is neutral."""
    return _number("direction", "dead_zone_frac", strict=False)


def open_dead_zone_pct() -> float:
    return _number("direction", "open_dead_zone_pct", strict=False)


def buckets() -> list:
    """Two ascending cut points in (0, 1); anything else is ``[0.4, 0.7]``."""
    raw = _section("direction").get("buckets")
    if (isinstance(raw, list) and len(raw) == 2 and all(_real(v) for v in raw)
            and 0 < raw[0] < raw[1] < 1):
        return [float(raw[0]), float(raw[1])]
    return list(_BUCKETS)


# ── the study and the page ───────────────────────────────────────────────────
def iv_scales() -> list:
    """A non-empty list of positive finite numbers, else the default."""
    raw = _section("study").get("iv_scales")
    if isinstance(raw, list) and raw and all(_real(v) and v > 0 for v in raw):
        return [float(v) for v in raw]
    return list(_IV_SCALES)


def min_samples() -> int:
    return _count("study", "min_samples")


def scorecard_days() -> int:
    return _count("study", "scorecard_days")


def news_items() -> int:
    return _count("news", "items", minimum=0)
