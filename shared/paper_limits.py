"""The paper books' per-trade loss caps, from ``config/paper.toml``.

Two books, two caps, one file:

* ``max_risk_per_trade`` - the automatic Paper Account's cap on one trade. It
  also sizes the scanner's width search (``scanner_engine.DEFAULT_MAX_RISK_DOLLARS``),
  so a width whose single contract busts it is never emitted.
* ``ledger_max_risk_per_trade`` - the Paper Ledger's cap, the book the webgui
  Paper button opens into. The Strategy Finder and the Income Window size their
  credit spreads against THIS one, because that is where their trades land.

Both shipped at $750 (operator decision, 2026-09-22; the Account's was $250).
They stay two keys because they are two books, and the operator may want them
apart again.

Read by ``options-scanner/config_paper.py``, which keeps the module-level
constants every consumer already imports. Missing file, bad TOML or a missing or
unusable value -> the built-in default, never a raise.
"""
import math

from repo_paths import PAPER_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "risk": {
        "max_risk_per_trade": 750.0,
        "ledger_max_risk_per_trade": 750.0,
    },
    # A leg quoted with NO BID is still a market when its offer is at or under
    # this many dollars a share: the option is nearly worthless, which is what
    # the long leg of a winning spread looks like near expiry. Above it, a zero
    # bid is a broken quote and the position is left unmarked for that cycle.
    "marks": {"zero_bid_max_ask": 0.25, "chain_max_age_sec": 45},
    # Every posted hourly trade idea is booked into the Paper Ledger as a
    # measurement, and a weekly review reads how they did.
    "idea_trades": {
        "enabled": True,
        "quantity": 1,
        "mark_interval_min": 5,
        "debit_stop_frac": 0.60,
        "clean_mae_pct": 25.0,
        "min_n": 5,
        "review_model": "sonnet",
    },
}

load, reset_cache = toml_loader(PAPER_TOML, DEFAULTS, label="paper.toml")


def _dollars(key):
    """``[risk].<key>`` as a positive finite float, else the shipped default.

    A zero, negative, NaN or non-number cap would refuse every trade (or, as a
    NaN, silently switch the cap off), so it degrades rather than binds."""
    sec = load().get("risk")
    raw = sec.get(key) if isinstance(sec, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULTS["risk"][key]
    val = float(raw)
    return val if math.isfinite(val) and val > 0 else DEFAULTS["risk"][key]


def zero_bid_max_ask() -> float:
    """The largest offer at which a leg with no bid still counts as quoted.
    Zero switches it off (a zero bid is never a market). A negative, NaN or
    non-number value reads as the shipped default."""
    sec = load().get("marks")
    raw = sec.get("zero_bid_max_ask") if isinstance(sec, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULTS["marks"]["zero_bid_max_ask"]
    val = float(raw)
    return val if math.isfinite(val) and val >= 0 else DEFAULTS["marks"]["zero_bid_max_ask"]


def mark_chain_max_age_sec() -> int:
    """How old a stored chain a position MARK may be priced from, in seconds.
    (A fill always asks for one fetched now.) A negative, NaN or non-number
    value reads as the shipped default."""
    sec = load().get("marks")
    raw = sec.get("chain_max_age_sec") if isinstance(sec, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULTS["marks"]["chain_max_age_sec"]
    val = float(raw)
    if not (math.isfinite(val) and val >= 0):
        return DEFAULTS["marks"]["chain_max_age_sec"]
    return int(val)


def _idea_int(raw, lo, hi, default):
    """A whole number in ``lo..hi``; a bool, float or string is not one."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return default
    return raw if lo <= raw <= hi else default


def _idea_float(raw, lo, hi, default):
    """A finite number in ``(lo, hi]``: ``lo`` itself is refused."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    val = float(raw)
    return val if math.isfinite(val) and lo < val <= hi else default


def idea_trades() -> dict:
    """``[idea_trades]``: how the hourly trade ideas are booked into the Paper
    Ledger and reviewed. Always a full dict. Each key that is missing or unusable
    reads as its shipped default on its own - a bad ``quantity`` does not take
    the other keys with it - and nothing here raises.

    ``enabled`` must be a real boolean (a string such as ``"yes"`` is not one).
    ``quantity`` and ``min_n`` are positive whole numbers; ``mark_interval_min``
    is 1..60 minutes; ``debit_stop_frac`` is a fraction in (0, 1];
    ``clean_mae_pct`` a percentage in (0, 100]; ``review_model`` a non-empty
    string."""
    d = DEFAULTS["idea_trades"]
    sec = load().get("idea_trades")
    if not isinstance(sec, dict):
        sec = {}
    enabled = sec.get("enabled")
    model = sec.get("review_model")
    return {
        "enabled": enabled if isinstance(enabled, bool) else d["enabled"],
        "quantity": _idea_int(sec.get("quantity"), 1, 10**6, d["quantity"]),
        "mark_interval_min": _idea_int(sec.get("mark_interval_min"), 1, 60,
                                       d["mark_interval_min"]),
        "debit_stop_frac": _idea_float(sec.get("debit_stop_frac"), 0.0, 1.0,
                                       d["debit_stop_frac"]),
        "clean_mae_pct": _idea_float(sec.get("clean_mae_pct"), 0.0, 100.0,
                                     d["clean_mae_pct"]),
        "min_n": _idea_int(sec.get("min_n"), 1, 10**6, d["min_n"]),
        "review_model": (model.strip() if isinstance(model, str) and model.strip()
                         else d["review_model"]),
    }


def max_risk_per_trade() -> float:
    """The automatic Account's per-trade cap (and the scanner's width budget)."""
    return _dollars("max_risk_per_trade")


def ledger_max_risk_per_trade() -> float:
    """The Paper Ledger's per-trade cap."""
    return _dollars("ledger_max_risk_per_trade")
