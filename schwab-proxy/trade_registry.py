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
import math
from typing import Dict, List, Optional, Set, Tuple

#############################################
# WHAT THE TRACKER FOLLOWS (PURE)
#############################################

# The tracker's events (target at half the credit, stop at twice it, short strike
# tested) are credit-spread rules, so these are the only structures it follows.
# The paper ledger also holds DEBIT structures (long options, debit verticals,
# butterflies, condors): their entry_credit is negative and their strikes live in
# ``legs``, not in the four strike columns, so the rules above do not apply.
TRACKED = ("PCS", "CCS", "IC")
# The Strategy Finder's name for the same four-leg structure.
_ALIASES = {"IRON_CONDOR": "IC"}


def tracked_strategy(strategy) -> Optional[str]:
    """The tracker's own name for a structure it follows (``IRON_CONDOR`` is
    ``IC``), or None for one it does not."""
    if not isinstance(strategy, str):
        return None
    name = strategy.strip().upper()
    name = _ALIASES.get(name, name)
    return name if name in TRACKED else None


def _usable_strike(value) -> bool:
    """A real, finite number above zero (a strike, or a credit received)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def track_refusal(body) -> Optional[str]:
    """Why the tracker will NOT follow this trade, or None when it can.

    Decided from the trade alone, BEFORE its option chain is fetched: a trade the
    tracker cannot follow must not cost a Schwab call to find that out. Never
    raises."""
    if not isinstance(body, dict):
        return "not a trade (expected a mapping)"
    name = tracked_strategy(body.get("strategy"))
    if name is None:
        return (f"{body.get('strategy')!r} is not a structure the tracker follows "
                f"(credit spreads only: {', '.join(TRACKED)})")
    # Without these there is no chain to ask for.
    missing = [k for k in ("symbol", "expiration")
               if not isinstance(body.get(k), str) or not body[k].strip()]
    needed = ["short_strike", "long_strike"]
    if name == "IC":
        needed += ["call_short", "call_long"]
    # The target and the stop are fractions of the credit, so a credit spread
    # with no positive credit has neither.
    needed.append("entry_credit")
    missing += [k for k in needed if not _usable_strike(body.get(k))]
    if missing:
        return f"no usable {', '.join(missing)}"
    return None


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
# RECONCILE MEMORY
#############################################

# Built-in timings, used when the caller's are unusable. The two caps are also
# config/marketdata.toml [tracker]; shared/tests pins them equal.
RETRY_BASE_SEC = 30.0
RETRY_CAP_SEC = 1800.0          # a failure Schwab recovering will not fix
FETCH_RETRY_CAP_SEC = 300.0     # Schwab failed to send the chain: recover quickly
_MAX_DOUBLINGS = 20     # 30 s doubled 20 times is far past any cap; stops 2**n overflowing


class TrackAttempts:
    """What the reconcile loop remembers about trades it could not start tracking.

    Without it the loop asked again every cycle, so one trade that could never be
    tracked cost a Schwab chain call and an ERROR line every 30 seconds for as
    long as it stayed open.

    - ``ok``: forgotten.
    - ``skipped`` (the tracker will never follow this trade): not tried again.
    - ``error`` (might clear: a failed chain fetch, a strike not listed yet): tried
      again after ``base_sec``, then double that, and so on up to the cap.

    ``last_key`` lets the caller report a failure loudly once and quietly while it
    repeats. The key is the result's ``key`` (a stable name for the outcome) and
    not its ``detail``, which can carry text that differs on every attempt (an
    upstream error body with a request id) and would defeat "once".

    Written from the reconcile thread only. ``counts`` may be read from another
    thread: it takes a snapshot."""

    def __init__(self, base_sec: float = RETRY_BASE_SEC, cap_sec: float = RETRY_CAP_SEC):
        self._base = base_sec if self._positive(base_sec) else RETRY_BASE_SEC
        self._cap = (cap_sec if self._positive(cap_sec) and cap_sec >= self._base
                     else max(RETRY_CAP_SEC, self._base))
        self._seen: Dict[str, dict] = {}

    @staticmethod
    def _positive(value) -> bool:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value > 0)

    def due(self, trade_id, now: float) -> bool:
        seen = self._seen.get(trade_id)
        return seen is None or now >= seen["next_at"]

    def last_key(self, trade_id) -> Optional[str]:
        seen = self._seen.get(trade_id)
        return seen["key"] if seen else None

    def record(self, trade_id, result: dict, now: float,
               cap_sec: Optional[float] = None) -> None:
        """Remember one outcome. ``cap_sec`` is the longest gap to allow for THIS
        failure (never below one base interval); unusable means the instance's."""
        status = result.get("status")
        if status == "ok":
            self._seen.pop(trade_id, None)
            return
        failures = (self._seen.get(trade_id) or {}).get("failures", 0)
        if status == "skipped":
            next_at = math.inf
        else:
            failures += 1
            cap = max(float(cap_sec), self._base) if self._positive(cap_sec) else self._cap
            gap = self._base * 2 ** min(failures - 1, _MAX_DOUBLINGS)
            next_at = now + min(cap, gap)
        self._seen[trade_id] = {"key": result.get("key") or result.get("detail"),
                                "failures": failures, "next_at": next_at}

    def counts(self) -> dict:
        """``{"not_followed": n, "failing": m}`` — the signal that something is
        being refused or retried, since each is logged loudly only once."""
        seen = list(self._seen.values())
        waiting = sum(1 for e in seen if e["next_at"] == math.inf)
        return {"not_followed": waiting, "failing": len(seen) - waiting}

    def prune(self, keep_ids) -> None:
        """Forget every trade not in ``keep_ids`` (closed, or now tracked)."""
        for trade_id in [t for t in self._seen if t not in keep_ids]:
            del self._seen[trade_id]


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
