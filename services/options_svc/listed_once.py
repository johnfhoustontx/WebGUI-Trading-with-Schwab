"""A symbol's listed expirations, fetched at most once per Rescue leg pricer.

``compute._make_leg_pricer`` attaches the callable this builds to the pricer as
``listed_expiries``. The roll builders use it (``rescue._roll_expiry``) to land a
roll out on an expiry Schwab actually lists: a calendar date such as expiry + 30
is often a weekend, whose chain is empty, and the roll then could not be priced.

Kept apart from ``compute`` (whose size only goes down) and imports nothing from
it: the fetch is handed in.
"""
from services import _degrade


def listed_once(fetch, symbol):
    """``listed_expiries(_symbol) -> list`` calling ``fetch()`` the first time
    only. A failed or empty fetch is remembered as ``[]``, which leaves the roll
    on its calendar date as before."""
    memo: list = []

    def listed_expiries(_symbol=None):
        if not memo:
            try:
                memo.append(list(fetch() or []))
            except Exception:  # noqa: BLE001 - no list keeps the calendar date
                _degrade.degraded("options.rescue_expirations", detail=symbol)
                memo.append([])
        return memo[0]

    return listed_expiries
