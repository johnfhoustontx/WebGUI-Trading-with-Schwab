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


def max_risk_per_trade() -> float:
    """The automatic Account's per-trade cap (and the scanner's width budget)."""
    return _dollars("max_risk_per_trade")


def ledger_max_risk_per_trade() -> float:
    """The Paper Ledger's per-trade cap."""
    return _dollars("ledger_max_risk_per_trade")
