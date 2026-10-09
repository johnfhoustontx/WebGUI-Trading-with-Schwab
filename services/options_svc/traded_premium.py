"""Traded premium, booked as it trades: per strike and side, a running total of
new volume priced ONCE, at the mark of the reading it first appeared in.

The stored ``prem`` view is the day's volume at the CURRENT mark, so it falls
when marks fall. This figure cannot: a sixth view string, ``tprem``, in
``gex_history.db``, with the same ``{call, put, net}`` cell.

``advance``     pure: books one chain's new volume into a symbol's state.
``on_chain``    every fetched chain, on the collector's thread. In memory only
                -- it never opens the database, so it cannot slow or break a
                poll.
``write_rows``  once a poll, after ``poll_once``, on its write connection:
                resumes a symbol's total from the store the first time it is
                written after a start, writes this poll's rows in one commit,
                and logs the day's check once after the regular close.

Off unless ``config/marketdata.toml [collection] traded_premium`` is true, and
nothing reads the view yet. An ESTIMATE: unsigned, priced at the mid of the
minute the volume was first seen. It imports nothing from ``compute``.
Design: docs/plans/2026-10-09-traded-premium-increment-design.md.
"""
import sys

from repo_paths import OPTIONS_SCANNER
from shared.numeric import finite as _finite
from shared.numeric import parsed_finite as _parsed

# ``flow_skew`` lives under options-scanner; ``compute`` puts it on the path
# too, repeated here so this module imports on its own (a test, a tool).
if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import flow_skew  # noqa: E402  (the ONE definition of a contract's mark)

VIEW = "tprem"
CALL, PUT = 0, 1

_MULTIPLIER = 100           # one option contract = 100 shares
_SIDES = (("callExpDateMap", CALL), ("putExpDateMap", PUT))
# A strike is keyed at three decimals. The store keeps strikes as float32, so
# an odd strike (17.63) comes back as 17.6299991607666; unrounded, a total
# resumed after a restart and the same strike's new volume would sit in two
# cells that pack to ONE float32 key, and one of them would be lost.
_STRIKE_DECIMALS = 3


def new_state() -> dict:
    """One symbol's session state."""
    return {
        "seeded": False,        # one usable reading taken this session
        "since_ts": None,       # when the watch began: the first usable reading
        "last_ts": None,        # the latest usable reading
        "hw": {},               # {contract symbol: highest volume already booked}
        "total": {},            # {strike: [call dollars, put dollars]}
        # The day's check. The chain's own volume, first and latest, is summed
        # straight off the chain so it does not depend on the booking below.
        "first_vol": 0.0, "chain_vol": 0.0,
        "booked_vol": 0.0, "late_vol": 0.0, "booked": 0.0,
        # The same new volume priced at each contract's ``last``, where it has
        # one, beside the mark dollars for exactly that volume.
        "last_vol": 0.0, "at_last": 0.0, "at_mark_same": 0.0,
    }


def _walk(chain):
    """Yield ``(side, strike, contract)`` for every contract dict in a chain.

    The strike is the MAP KEY, as ``flow_skew._accumulate_by_strike`` reads it:
    the key is always present, while the contract's own field can be absent."""
    for mapkey, side in _SIDES:
        exp_map = chain.get(mapkey)
        if not isinstance(exp_map, dict):
            continue
        for strike_map in exp_map.values():
            if not isinstance(strike_map, dict):
                continue
            for raw_strike, contracts in strike_map.items():
                strike = _parsed(raw_strike)
                if strike is None or not isinstance(contracts, (list, tuple)):
                    continue
                strike = round(strike, _STRIKE_DECIMALS)
                for c in contracts:
                    if isinstance(c, dict):
                        yield side, strike, c


def advance(state, chain, now_ts, *, late_sec):
    """Book one fetched chain's new volume into ``state``, IN PLACE. Returns
    the dollars booked by this reading, or None for a chain with no contracts
    (an error body), which changes nothing.

    The symbol's FIRST usable reading only sets each contract's baseline: after
    a restart the day's volume must not land in one minute. On a later reading
    a contract with no baseline stood at zero volume before, so all of its
    volume is new.

    The baseline is a HIGH-WATER mark: volume never falls within a session, so
    a glitch read of 0 books nothing and cannot re-book the day. A contract
    with new volume and no usable mark books nothing and KEEPS its baseline, so
    that volume is booked when a mark returns."""
    if not isinstance(chain, dict):
        return None
    seeded = state["seeded"]
    last_ts = state["last_ts"]
    late = last_ts is not None and now_ts - last_ts > late_sec
    hw, total = state["hw"], state["total"]
    contracts, chain_vol, dollars_now = 0, 0.0, 0.0
    for side, strike, c in _walk(chain):
        osi = c.get("symbol")
        vol = _finite(c.get("totalVolume"))
        if not osi or vol is None or vol < 0:
            continue
        contracts += 1
        chain_vol += vol
        before = hw.get(osi)
        if before is None:
            if not seeded:
                if vol > 0:
                    hw[osi] = vol
                continue
            before = 0.0
        dv = vol - before
        if dv <= 0:
            continue
        mark = _finite(flow_skew._contract_mark(c))
        if mark is None:
            continue
        hw[osi] = vol
        dollars = dv * mark * _MULTIPLIER
        cell = total.get(strike)
        if cell is None:
            cell = total[strike] = [0.0, 0.0]
        cell[side] += dollars
        dollars_now += dollars
        state["booked_vol"] += dv
        if late:
            state["late_vol"] += dv
        last = _finite(c.get("last"))
        if last is not None and last > 0:
            state["last_vol"] += dv
            state["at_last"] += dv * last * _MULTIPLIER
            state["at_mark_same"] += dollars
    if not contracts:
        return None
    if not seeded:
        state["seeded"], state["since_ts"] = True, now_ts
        state["first_vol"] = chain_vol
    state["chain_vol"] = chain_vol
    state["booked"] += dollars_now
    state["last_ts"] = now_ts
    return dollars_now


def grid(state) -> dict:
    """The running totals as the store's cell shape, strikes ascending. Exactly
    ``{call, put, net}`` floats: what the columnar packer gates on."""
    return {strike: {"call": cell[CALL], "put": cell[PUT],
                     "net": cell[CALL] - cell[PUT]}
            for strike, cell in sorted(state["total"].items())}


def _resume(state, prior) -> None:
    """Add the session's last stored totals to ``state``: what a restart must
    not lose. ``prior`` is ``gex_history_db.latest_grid_row``'s answer.

    Everything is read BEFORE anything is added, so a bad cell cannot leave
    half a total behind for the retry to add a second time."""
    if not prior or not isinstance(prior[3], dict):
        return
    adds = []
    for raw_strike, cell in prior[3].items():
        strike = _parsed(raw_strike)
        if strike is None or not isinstance(cell, dict):
            continue
        adds.append((round(strike, _STRIKE_DECIMALS),
                     max(_finite(cell.get("call")) or 0.0, 0.0),
                     max(_finite(cell.get("put")) or 0.0, 0.0)))
    total = state["total"]
    for strike, call, put in adds:
        cell = total.get(strike)
        if cell is None:
            cell = total[strike] = [0.0, 0.0]
        cell[CALL] += call
        cell[PUT] += put

