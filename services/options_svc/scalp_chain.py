"""The Opening Scalp's morning chain capture: today's 0DTE chain -> ``scalp.db``.

At 08:36 CT (``[slots.scalp]`` in ``config/sessions.toml``) each scalp symbol's
0DTE chain is fetched once, cut to the strikes near the money and kept in
``scalp_store.chain_slices``. The slice is what later calibrates the volatility
the backtest assumes (``atm_iv``) and what a live call is priced from.

Only the near-the-money strikes are kept (``[trade] chain_strikes`` either side
of spot): a whole 0DTE chain is thousands of contracts and the scalp trades the
at-the-money strike. The slice keeps Schwab's own nested shape, so every reader
of a chain can read it, plus the chain's top-level header (symbol,
``underlyingPrice``, timestamps).

The call is an ordinary chain fetch for one expiry through the proxy, on the
normal lane: never ``priority=True`` (that is the one-minute GEX poll's), and
08:36 sits outside the autoscan's :32-:35 fetch burst and Schwab's refused first
minute after the half hour. This module imports nothing from ``compute``, and
the proxy client is built lazily so importing it constructs nothing.
"""
import logging
import time

from services import _degrade
from shared import greeks as _greeks
from shared import scalp_config
from shared.numeric import finite as _finite

from services.options_svc import scalp_fetch, scalp_store

log = logging.getLogger("options_svc.scalp_chain")

_MAPS = ("callExpDateMap", "putExpDateMap")
#: What a kept contract carries besides its identity (strike, side, symbol).
_FIELDS = ("bid", "ask", "mark", "delta", "gamma", "theta", "vega", "volatility",
           "totalVolume", "openInterest")
_IDENTITY = ("strikePrice", "putCall", "symbol")


def _default_client():
    # Lazy on purpose: ``services._proxy`` builds the proxy client at import.
    from services import _proxy
    return _proxy.schwab_py_client


def _strike_of(key):
    """A chain map's strike key (``"571.0"``) as a float, or None."""
    try:
        return _finite(float(key))
    except (TypeError, ValueError):
        return None


def _strikes(chain: dict) -> list:
    """Every strike either side of the chain lists, ascending."""
    found = set()
    for name in _MAPS:
        for by_strike in (chain.get(name) or {}).values():
            if isinstance(by_strike, dict):
                found.update(s for s in map(_strike_of, by_strike) if s is not None)
    return sorted(found)


def _trim(contract):
    if not isinstance(contract, dict):
        return None
    return {k: contract[k] for k in _IDENTITY + _FIELDS if k in contract}


def near_money_slice(chain: dict, spot: float, n: int) -> dict:
    """``chain`` cut to the ``n`` strikes either side of ``spot`` (and the strike
    nearest it), from both the call and the put map, in the same nested shape
    (``{expiry: {"571.0": [contract, ...]}}``).

    The strikes counted are the ones the chain lists, so a chain with a gap in it
    still keeps ``n`` real strikes a side; near either end it keeps what exists.
    Each contract keeps its identity and ``bid, ask, mark, delta, gamma, theta,
    vega, volatility, totalVolume, openInterest``; the chain's other top-level
    fields (symbol, ``underlyingPrice``, timestamps) are carried over."""
    out = {k: v for k, v in chain.items() if k not in _MAPS}
    strikes = _strikes(chain)
    keep = set()
    if strikes:
        nearest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
        keep = set(strikes[max(0, nearest - n): nearest + n + 1])
    for name in _MAPS:
        sliced = {}
        for expiry, by_strike in (chain.get(name) or {}).items():
            if not isinstance(by_strike, dict):
                continue
            rows = {}
            for key, contracts in by_strike.items():
                if _strike_of(key) not in keep or not isinstance(contracts, list):
                    continue
                kept = [c for c in map(_trim, contracts) if c is not None]
                if kept:
                    rows[key] = kept
            if rows:
                sliced[expiry] = rows
        out[name] = sliced
    return out


def capture(symbol, day, client=None, db_path=None):
    """Fetch ``symbol``'s 0DTE chain for ``day`` (``YYYY-MM-DD``), keep the near-
    the-money slice and save it with the capture's own wall-clock epoch seconds.

    Returns the slice, or ``None`` when nothing was saved: a non-200, an
    exception, or a body with no spot or no strikes. Nothing is saved on a
    failure, and the failure is logged (and counted when it was an exception)."""
    try:
        client = client if client is not None else _default_client()
        resp = client.get_option_chain(symbol, from_date=day, to_date=day)
        if getattr(resp, "status_code", None) != 200:
            log.warning("scalp chain %s: HTTP %s", symbol,
                        getattr(resp, "status_code", None))
            return None
        chain = resp.json()
        spot = _finite(chain.get("underlyingPrice")) if isinstance(chain, dict) else None
        if spot is None or spot <= 0:
            log.warning("scalp chain %s: no usable spot", symbol)
            return None
        sliced = near_money_slice(chain, spot, scalp_config.chain_strikes())
        if not (sliced["callExpDateMap"] and sliced["putExpDateMap"]):
            log.warning("scalp chain %s: no strikes near the money", symbol)
            return None
        scalp_store.save_chain_slice(day, symbol, int(time.time()), sliced,
                                     db_path=db_path)
        return sliced
    except Exception:
        _degrade.degraded("options.scalp_chain", detail=symbol)
        return None


def capture_all(day=None, client=None, db_path=None) -> dict:
    """``capture`` for every scalp symbol (``[symbols] list``); ``day`` defaults to
    today in Central. One symbol failing never stops the others: its entry is
    ``None``. Returns ``{symbol: slice | None}``."""
    day = day if day is not None else scalp_fetch.ct(int(time.time())).date().isoformat()
    return {s: capture(s, day, client=client, db_path=db_path)
            for s in scalp_config.symbols()}


#: The 0DTE weekly root. A $SPX chain can list the AM-settled monthly (``SPX``)
#: and the PM-settled weekly (``SPXW``) under ONE strike; the scalp trades SPXW.
_WEEKLY_ROOT = "SPXW"


def preferred_contract(contracts, ok=None):
    """The contract a scalp reads from one strike's list: among the dicts that
    pass ``ok`` (all of them when ``ok`` is None), the first whose ``symbol``
    starts with ``SPXW``, else the first. None when none passes."""
    usable = [c for c in (contracts if isinstance(contracts, list) else [])
              if isinstance(c, dict) and (ok is None or ok(c))]
    for c in usable:
        if str(c.get("symbol") or "").strip().upper().startswith(_WEEKLY_ROOT):
            return c
    return usable[0] if usable else None


def _usable_vol(contract):
    vol = _greeks.usable(contract.get("volatility"))
    return vol if vol is not None and vol > 0 else None


def _atm_volatility(by_strike: dict, spot: float):
    """The usable ``volatility`` (percent) of the contract at the strike nearest
    ``spot`` in one map's expiry (``preferred_contract``: SPXW first), or None."""
    best = None
    for key, contracts in by_strike.items():
        strike = _strike_of(key)
        if strike is None or not contracts:
            continue
        if best is None or abs(strike - spot) < abs(best[0] - spot):
            best = (strike, contracts)
    if best is None:
        return None
    chosen = preferred_contract(best[1], ok=lambda c: _usable_vol(c) is not None)
    return None if chosen is None else _usable_vol(chosen)


def atm_iv(slice_: dict, spot: float):
    """The at-the-money implied volatility as a fraction: the mean of the call's
    and the put's ``volatility`` / 100 at the strike nearest ``spot``. ``None``
    when either is missing or unusable (Schwab's ``-999`` is "not computed").
    Where a strike lists more than one root, the SPXW contract is read, else the
    first with a usable volatility."""
    try:
        legs = []
        for name in _MAPS:
            expiries = list((slice_.get(name) or {}).values())
            vol = _atm_volatility(expiries[0], spot) if expiries else None
            if vol is None:
                return None
            legs.append(vol)
        return sum(legs) / len(legs) / 100.0
    except (AttributeError, TypeError, IndexError):
        return None
