"""One collected minute of option chains, as fetched and before anything is
derived from it.

The GEX collector fetches a minute (``gex_collector.fetch_minute``) and derives
rows from it (``gex_collector.derive_minute``). This is the value that passes
between the two, and the value the raw feed stores, so a minute derived later
from the store is the minute that was derived live
(docs/plans/2026-10-14-raw-feed-and-catch-up-design.md).

Stdlib only: the options-scanner engines, the options service and the feed
service all import it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RawMinute:
    #: Minute-floored unix seconds: the ``ts`` every row of this minute carries.
    ts: int
    #: ``(symbol, chain)`` in fetch order, AFTER the off-hours spot re-anchor and
    #: the carry -- the chains the Greek pass prices.
    chains: list = field(default_factory=list)
    #: Symbols whose chain is the proxy's stored one carried to the live price.
    #: Never handed to ``on_chain``: a carried chain holds no new volume.
    carried: set = field(default_factory=set)
    #: A carried chain's age in seconds (None when the proxy did not say).
    carried_age: dict = field(default_factory=dict)
    #: A carried symbol's chain AS STORED, which the skew readings and the
    #: premium grid read. Only where it differs from the chain in ``chains``.
    stored: dict = field(default_factory=dict)
    #: The 10-day ``$SPX`` term chain, on the minutes it is polled.
    term: dict | None = None
    #: The universe asked this minute (a failed fetch is in here and not in
    #: ``chains``).
    symbols: list = field(default_factory=list)

    def chain_for_skew(self, symbol):
        """The chain the skew, volume, premium and per-strike premium read: the
        stored one for a carried symbol, else the chain itself (None if absent)."""
        if symbol in self.stored:
            return self.stored[symbol]
        for sym, chain in self.chains:
            if sym == symbol:
                return chain
        return None
