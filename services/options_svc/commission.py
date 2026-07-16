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
from shared.symbols import is_index_symbol  # noqa: E402

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


def futures_commission(qty: int) -> float:
    """Round-turn futures commission (per side x 2) + exchange passthrough."""
    fut = _RATES["futures"]
    if qty <= 0:
        return 0.0
    return round(qty * float(fut["standard"]) * 2 + float(fut.get("exchange_fee", 0.0)), 4)
