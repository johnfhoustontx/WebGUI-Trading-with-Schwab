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
    if last <= 0 or bid < 0 or ask <= 0 or ask < bid:
        return 0
    if last >= ask:
        return 1
    if last <= bid:
        return -1
    mid = (bid + ask) / 2.0
    if last > mid:
        return 1
    if last < mid:
        return -1
    return 0
