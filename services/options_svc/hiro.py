"""HIRO-style dealer hedging flow — measurement + detection. PURE (stdlib only).

A MODEL of SpotGamma's HIRO, not SpotGamma's number. Schwab publishes no
time-and-sales tape, so each contract gets ONE buy/sell label per minute, read
from where its latest trade price sits against this minute's bid/ask.
Design: docs/plans/2026-10-01-hiro-alert-design.md.

Hedge impact of a trade = side x delta x contracts x 100 x spot, with the
contract's SIGNED delta: a customer buying a call (+1 x +delta) makes the dealer
buy stock (positive); buying a put (+1 x -delta) makes the dealer sell (negative).
"""
import math


def _finite(v):
    """A real finite float, or None (rejects bool, NaN, inf, non-numbers)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def classify_side(last, bid, ask) -> int:
    """+1 customer bought, -1 customer sold, 0 unclassified.

    At/through the ask = bought, at/through the bid = sold, otherwise the side of
    the midpoint. Exactly at the midpoint, or any unusable quote, is 0 — a reading
    with no label must never be guessed into one."""
    last, bid, ask = _finite(last), _finite(bid), _finite(ask)
    if last is None or bid is None or ask is None:
        return 0
    if last <= 0 or bid < 0 or ask <= 0 or ask <= bid:   # crossed or LOCKED quote
        return 0
    if last >= ask:
        return 1
    if last <= bid:
        return -1
    mid = (bid + ask) / 2.0
    # A print meant to be AT the mid can miss it by an ulp in binary (0.15 vs
    # (0.10 + 0.20) / 2), and must not be labelled by that rounding.
    if math.isclose(last, mid, rel_tol=1e-9, abs_tol=1e-9):
        return 0
    if last > mid:
        return 1
    if last < mid:
        return -1
    return 0


def _contracts(chain):
    """Yield ``(is_call, contract)`` for every contract dict in a Schwab chain,
    ``is_call`` from the map it came from. Total over malformed input."""
    for mapkey, is_call in (("callExpDateMap", True), ("putExpDateMap", False)):
        exp_map = chain.get(mapkey)
        if not isinstance(exp_map, dict):
            continue
        for strike_map in exp_map.values():
            if not isinstance(strike_map, dict):
                continue
            for contracts in strike_map.values():
                if not isinstance(contracts, list):
                    continue
                for c in contracts:
                    if isinstance(c, dict):
                        yield is_call, c


def measure_chain(chain, prev_vol):
    """One minute of hedge impact for one symbol.

    ``prev_vol`` is ``{contract symbol: totalVolume}`` from the previous poll.
    Returns ``(row, new_prev)``; ``row`` is ``{"spot", "impact",
    "classified_vol", "unclassified_vol"}`` or None when the chain has no usable
    spot (then ``new_prev`` is ``prev_vol`` unchanged, so the next good minute
    books the volume rather than losing it).

    A contract's FIRST reading only seeds the baseline: after a restart it must
    never book the whole day's volume into one minute.

    The stored baseline is a HIGH-WATER mark (``max(vol, before)``): volume
    never falls within a session, so a one-off glitch read of 0 books nothing
    and cannot re-book the day later. A legitimate daily reset is unaffected --
    the caller clears ``prev_vol`` when the session date changes.

    Volume that cannot be turned into impact -- no buy/sell label, an unusable
    delta, or a delta of the wrong sign for its right -- is counted as
    ``unclassified_vol``, so the window never looks better measured than it was.
    The caller's ``prev_vol`` is never mutated."""
    new_prev = dict(prev_vol or {})
    if not isinstance(chain, dict):
        return None, new_prev
    spot = _finite(chain.get("underlyingPrice"))
    if spot is None or spot <= 0:
        return None, new_prev
    impact = classified = unclassified = 0.0
    for is_call, c in _contracts(chain):
        osi = c.get("symbol")
        vol = _finite(c.get("totalVolume"))
        if not osi or vol is None:
            continue
        before = new_prev.get(osi)
        if before is None:
            new_prev[osi] = vol
            continue
        # High-water mark: cumulative volume never falls within a session, so a
        # glitch read (totalVolume = 0) must not reset the baseline and book the
        # contract's whole day into the next minute.
        new_prev[osi] = max(vol, before)
        dv = vol - before
        if dv <= 0:
            continue
        delta = _finite(c.get("delta"))
        if (delta is None or abs(delta) > 1      # NaN / Schwab's -999 sentinel
                or (is_call and delta < 0) or (not is_call and delta > 0)):
            unclassified += dv
            continue
        side = classify_side(c.get("last"), c.get("bid"), c.get("ask"))
        if side == 0:
            unclassified += dv
            continue
        classified += dv
        impact += side * delta * dv * 100.0 * spot
    return ({"spot": spot, "impact": impact, "classified_vol": classified,
             "unclassified_vol": unclassified}, new_prev)
