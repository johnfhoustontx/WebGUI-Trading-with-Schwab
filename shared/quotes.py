"""One option quote's mid price: the ONE definition of "has a market".

Measured 2026-11-28: ``(bid + ask) / 2`` was written out at 24 sites in 17
files, with five different ideas of a market. Over one battery of quotes,
several took a CROSSED quote (bid above the offer) as a price, three passed a
NaN or an infinity through, one read a 0 x 0 quote as a mid of 0.00, and two
read a bid with no offer as half the bid.

What stays the caller's choice, named and passed on purpose:

* ``zero_bid_max_ask`` - whether a ZERO bid is still a market. A far
  out-of-the-money option near expiry is quoted 0.00 x 0.05, and that is
  exactly the long leg of a spread that has won (CLAUDE.md: "a zero bid under
  a small offer is still a market"); 0.00 x 1.20 is a broken quote. A zero bid
  is a market only when the offer is at or below this. The default ``0.0``
  refuses every zero bid (an entry gate); ``math.inf`` accepts any (a mark
  that must exist for a worthless leg); the paper books pass their configured
  limit.
* whether the published ``mark`` is preferred: :func:`mark_or_mid`.

What is never a market: a missing, non-finite or bool side, a non-positive
offer, a negative bid, and a crossed quote. A LOCKED quote (bid == ask) is one.

``math`` and ``shared.numeric`` only, so every tier may import it; pinned for
Tier 1 by ``webgui/tests/test_quotes_tier1.py``.
"""
from __future__ import annotations

from shared.numeric import finite, positive_finite


def mid(bid, ask, *, zero_bid_max_ask: float = 0.0) -> float | None:
    """``(bid + ask) / 2`` when the two sides are a market, else None."""
    b, a = finite(bid), finite(ask)
    if b is None or a is None or a <= 0 or b < 0 or b > a:
        return None
    if b == 0 and a > zero_bid_max_ask:
        return None
    return (b + a) / 2.0


def contract_mid(contract, *, zero_bid_max_ask: float = 0.0) -> float | None:
    """:func:`mid` of a chain contract's ``bid`` / ``ask``."""
    if not isinstance(contract, dict):
        return None
    return mid(contract.get("bid"), contract.get("ask"),
               zero_bid_max_ask=zero_bid_max_ask)


def mark_or_mid(contract, *, zero_bid_max_ask: float = 0.0) -> float | None:
    """The contract's published ``mark`` when it is a positive finite number,
    else its :func:`contract_mid`."""
    if not isinstance(contract, dict):
        return None
    mark = positive_finite(contract.get("mark"))
    if mark is not None:
        return mark
    return contract_mid(contract, zero_bid_max_ask=zero_bid_max_ask)
