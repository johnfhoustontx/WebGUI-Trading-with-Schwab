"""
SchwabProxy - Trade registry + OSI resolution
Version: 1.0.0
Last Updated: 2026-05-30

Version 1.0.0 Changes:
- Initial implementation

In-memory registry of tracked trades (the proxy's source of truth for what to
subscribe to on the stream) plus a pure resolver that maps spread legs to Schwab
OSI option symbols using a fetched option chain. No I/O, no threading lock here;
the stream worker task marshals access onto its event loop.
"""
from typing import Dict, List, Optional, Set, Tuple

#############################################
# OSI RESOLUTION (PURE)
#############################################


def _find_symbol(exp_map: dict, strike: float) -> str:
    """Find the OSI symbol for the contract whose strike float-equals `strike`.

    `exp_map` is a putExpDateMap or callExpDateMap: keyed by "YYYY-MM-DD:DTE",
    then by a strike string ("5200.0") -> list of contract dicts. Strike keys are
    matched by float equality to tolerate "5200" vs "5200.0" formatting.

    Raises KeyError if no matching strike is found.
    """
    target = float(strike)
    for strikes in exp_map.values():
        for strike_str, contracts in strikes.items():
            try:
                if float(strike_str) == target and contracts:
                    return contracts[0]["symbol"]
            except (TypeError, ValueError):
                continue
    raise KeyError(f"strike {strike} not found in chain map")


def resolve_legs(chain_json: dict, strategy: str, short_strike: float,
                 long_strike: float, call_short: Optional[float] = None,
                 call_long: Optional[float] = None) -> Dict[str, str]:
    """Map spread legs to OSI option symbols using a Schwab /chains response.

    Returns {leg_name: osi_symbol}. Leg names match trade_detector:
      - PCS: put_short/put_long from putExpDateMap.
      - CCS: put_short/put_long resolved from callExpDateMap (the detector reads
             put_short/put_long for both PCS and CCS).
      - IC:  put_short/put_long from putExpDateMap + call_short/call_long from
             callExpDateMap.

    Raises KeyError if a required strike is absent from the chain.
    """
    put_map = chain_json.get("putExpDateMap", {})
    call_map = chain_json.get("callExpDateMap", {})

    if strategy == "PCS":
        return {
            "put_short": _find_symbol(put_map, short_strike),
            "put_long": _find_symbol(put_map, long_strike),
        }
    if strategy == "CCS":
        return {
            "put_short": _find_symbol(call_map, short_strike),
            "put_long": _find_symbol(call_map, long_strike),
        }
    if strategy == "IC":
        return {
            "put_short": _find_symbol(put_map, short_strike),
            "put_long": _find_symbol(put_map, long_strike),
            "call_short": _find_symbol(call_map, call_short),
            "call_long": _find_symbol(call_map, call_long),
        }
    raise KeyError(f"unknown strategy {strategy!r}")


#############################################
# TRADE REGISTRY
#############################################


class TradeRegistry:
    """In-memory store of tracked trade states, keyed by trade_id."""

    def __init__(self):
        self._trades: Dict[str, dict] = {}

    def add(self, state: dict) -> None:
        """Store a trade state dict (must contain trade_id and legs)."""
        self._trades[state["trade_id"]] = state

    def remove(self, trade_id: str) -> None:
        """Drop a trade; safe if absent."""
        self._trades.pop(trade_id, None)

    def get(self, trade_id: str) -> Optional[dict]:
        return self._trades.get(trade_id)

    def __contains__(self, trade_id: str) -> bool:
        return trade_id in self._trades

    def all_trades(self) -> List[dict]:
        return list(self._trades.values())

    def legs_union(self) -> Set[str]:
        """Set of all OSI symbols across every tracked trade.

        Iterates a snapshot (list(...)) because this is called from the stream
        worker's event-loop thread while REST/reconcile threads may add/remove
        trades — avoids 'dict changed size during iteration'.
        """
        union: Set[str] = set()
        for state in list(self._trades.values()):
            union.update(state.get("legs", {}).values())
        return union

    def for_osi(self, osi: str) -> List[Tuple[str, str]]:
        """List of (trade_id, leg_name) pairs referencing `osi`.

        Snapshots the items for the same cross-thread reason as legs_union().
        """
        pairs: List[Tuple[str, str]] = []
        for trade_id, state in list(self._trades.items()):
            for leg_name, symbol in state.get("legs", {}).items():
                if symbol == osi:
                    pairs.append((trade_id, leg_name))
        return pairs
