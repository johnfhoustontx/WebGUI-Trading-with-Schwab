"""Extended-trading-hours eligibility, harvested from the option chain.

Schwab publishes a root-level ``ethOptionEligible`` boolean on every chain
response, so the ETH-eligible symbol list is READ, never hardcoded -- which
matters because Cboe re-balances that list semi-annually and may grow it from
21 names toward a 100-class cap. Probing the live proxy showed the field
discriminates correctly (NVDA/TSLA/AAPL/PLTR/AMD/AVGO/MU True; SPY/QQQ/IWM/KO/XOM
False), and every True lands in Cboe's published launch list. See
docs/plans/2026-08-02-options-extended-hours-design.md section 5.3.

Because the 1-minute GEX poll already fetches those chains, harvesting costs
**zero additional API calls** -- the design's central economy.

PURE: no I/O, no clock. The handler wires the cache.
"""

CACHE_KEY = "cache:options:eth_eligible"


def chain_eth_eligible(chain) -> bool:
    """Whether this chain's symbol trades in extended hours.

    Degrades to False on a missing field or a malformed chain -- NEVER True,
    since a false positive would widen GTH collection to a symbol that does not
    quote, wasting API budget on empty responses."""
    try:
        return bool(chain.get("ethOptionEligible", False))
    except Exception:
        # Not a mapping (None, list, str, ...) -- no evidence of eligibility.
        return False


def merge_eligibility(prior, symbol, eligible, *, date_iso):
    """Fold one symbol's eligibility into the cached {date, symbols} envelope.

    A new session date RESETS the map -- eligibility is re-harvested daily, so a
    semi-annual Cboe list change is picked up within one session (a symbol
    dropped from the list simply never reappears in the new day's map). ``prior``
    is never mutated; a malformed ``prior`` is treated as absent."""
    symbols: dict = {}
    if isinstance(prior, dict) and prior.get("date") == date_iso:
        prior_symbols = prior.get("symbols")
        if isinstance(prior_symbols, dict):
            symbols = dict(prior_symbols)
    symbols[symbol] = bool(eligible)
    return {"date": date_iso, "symbols": symbols}


def eligible_symbols(payload, *, date_iso=None) -> set:
    """The eligible symbol set from a cached envelope. Empty on a stale date.

    ``date_iso=None`` skips the staleness gate on purpose: that is the cold-start
    read (before the day's first harvest), where the design falls back to the
    previous session's map rather than guessing. A caller that must not act on
    stale data passes today's session date and gets an empty set instead."""
    if not isinstance(payload, dict):
        return set()
    if date_iso is not None and payload.get("date") != date_iso:
        return set()
    symbols = payload.get("symbols")
    if not isinstance(symbols, dict):
        return set()
    # ``is True`` (not truthiness): merge_eligibility stores real bools and JSON
    # round-trips them, so anything else is corruption -- and the fail-safe
    # direction is "not eligible".
    return {sym for sym, ok in symbols.items() if ok is True}
