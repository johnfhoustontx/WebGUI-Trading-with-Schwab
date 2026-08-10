"""
Options service — Schwab commission model.
Version: 1.0.0

Pure helpers. Rates load once from config/commissions.toml (single source of
truth; never hard-code rates in callers). See the rescue design doc.
"""
from __future__ import annotations
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
from repo_paths import REPO_ROOT  # noqa: E402

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

#############################################
# RATES
#############################################

def _load_rates() -> dict:
    path = pathlib.Path(REPO_ROOT) / "config" / "commissions.toml"
    with open(path, "rb") as fh:
        return tomllib.load(fh)

_RATES = _load_rates()

# Index roots that carry the index option rate (+ exchange passthrough).
_INDEX_ROOTS = {"SPX", "VIX", "OEX", "NDX", "RUT", "XSP", "DJX"}


def is_index_symbol(symbol: str) -> bool:
    if not symbol:
        return False
    return symbol.lstrip("$").upper() in _INDEX_ROOTS


def _option_rate(symbol: str) -> float:
    opt = _RATES["options"]
    if is_index_symbol(symbol):
        return float(opt["index"]) + float(opt.get("index_exchange_fee", 0.0))
    return float(opt["equity"])


def commission_for(legs: int, symbol: str, qty: int) -> float:
    """Total option commission for ``legs`` option legs of ``qty`` contracts each.
    A leg is a single buy/sell of one option series. Closing a 2-leg spread = 2
    legs; a roll (close 2 + open 2) = 4 legs. Let-expire/assignment legs = pass 0.
    """
    if legs <= 0 or qty <= 0:
        return 0.0
    return round(legs * qty * _option_rate(symbol), 4)


# Leg counts per defined-risk structure (unknown → 2, a vertical).
_STRATEGY_LEGS = {"PCS": 2, "CCS": 2, "IC": 4}


def round_trip_commission(strategy: str, symbol: str, qty: int = 1) -> float:
    """Open+close option commission for a defined-risk structure, in dollars.

    A round trip = open ALL legs + close ALL legs, so it is ``commission_for(legs,
    symbol, qty)`` doubled. PCS/CCS = 2 legs (→ 4 leg-fills), IC = 4 legs (→ 8
    leg-fills); an unknown structure conservatively assumes 2 legs. Returned in
    DOLLARS, directly comparable to a position's unrealized P&L — this is the
    break-even close floor for the captured-autoclose break-even stop.
    """
    legs = _STRATEGY_LEGS.get((strategy or "").upper(), 2)
    return round(commission_for(legs, symbol, qty) * 2, 4)


def futures_commission(qty: int) -> float:
    """Round-turn futures commission (per side x 2) + exchange passthrough."""
    fut = _RATES["futures"]
    if qty <= 0:
        return 0.0
    return round(qty * float(fut["standard"]) * 2 + float(fut.get("exchange_fee", 0.0)), 4)
