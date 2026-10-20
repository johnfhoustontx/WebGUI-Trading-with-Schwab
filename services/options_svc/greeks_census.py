"""Did Schwab send any Greeks with this chain?

Schwab marks a Greek it did not compute with ``-999``. ``shared.greeks``
refuses that value, so the engine counts the contract's gamma as zero. When
EVERY contract comes back that way, every cell of every Dealer Positioning view
is 0.0. The bars then have no length, and the wall picker returns whichever
strike ties first: on 2026-10-19 the SPY "put wall" was 550 with spot at 767.
That morning every contract on every symbol had gamma, delta and volatility at
-999, before and after the open, while open interest and last trades were live.

This module names that state, so the snapshot can say it rather than draw
zeros. A sibling of ``compute`` and never an importer of it: ``compute.py`` has
a line ceiling (tests/test_compute_module_shape.py).
"""
from shared import greeks as _greeks

_MAPS = ("callExpDateMap", "putExpDateMap")


def _contracts(chain):
    """Every contract dict in the chain's call and put maps. Junk-tolerant."""
    for side in _MAPS:
        exp_map = (chain or {}).get(side)
        if not isinstance(exp_map, dict):
            continue
        for strikes in exp_map.values():
            if not isinstance(strikes, dict):
                continue
            for contracts in strikes.values():
                for c in contracts if isinstance(contracts, list) else ():
                    if isinstance(c, dict):
                        yield c


def greeks_missing(chain) -> bool:
    """True when the chain lists contracts and not one of them carries a usable
    gamma.

    A gamma of 0.0 is a reading (a far strike), so a real chain is never
    "missing". A chain with no contracts at all is not "missing Greeks" either:
    that is a different absence, and its own message on the page."""
    seen = False
    for c in _contracts(chain):
        seen = True
        if _greeks.gamma(c.get("gamma")) is not None:
            return False
    return seen


def mark_no_greeks(views) -> None:
    """Mark every view as drawn from a chain with no Greeks, in place.

    Its walls and flip are cleared: they were picked from an all-zero grid, so
    they are ties, not levels. The per-strike data stays, so nothing that reads
    the payload's shape breaks; the page reads ``no_greeks`` and draws a sentence
    in place of the bars."""
    for entry in (views or {}).values():
        if not isinstance(entry, dict):
            continue
        entry["no_greeks"] = True
        entry["walls"] = []
        entry["flip"] = None
