"""Carry a fetched option chain forward to the live price.

The GEX collector can fetch watchlist-only symbols every Nth minute
(config/marketdata.toml [collection]). On the minutes between, it re-uses the
last fetched chain and calls ``carry_chain`` so the engine prices it at the
LIVE spot.

What changes: ``underlyingPrice``, and ``gamma`` and ``delta`` on the contracts
of the nearest expiration — the only expiration ``GammaEngine`` reads. Schwab's
own value stays the base and only the Black-Scholes CHANGE between the fetch
price and the live price is applied, so the series does not step when the next
real fetch arrives. Charm and vanna need nothing: the engine computes them from
spot and volatility itself.

What never changes: volume, open interest, marks, volatility. A carried chain
has no new trades, and nothing here pretends otherwise.

Pure: no I/O, and the input chain is never mutated.
"""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from gamma_tool import GammaEngine
from options_calculator import RISK_FREE_RATE, bs_delta, bs_gamma, expiry_time_to_years

# The engine's own zone: its "today" is the Central date (gamma_tool.TZ).
_CT = ZoneInfo("America/Chicago")
_SECONDS_PER_YEAR = 365 * 24 * 3600
# Below this the fetch-price gamma is rounding noise and a ratio over it means
# nothing: Schwab's value is kept.
_TINY_GAMMA = 1e-12
# The most a carried gamma may grow over Schwab's own value. Near the close on
# expiration day a strike the price walks onto has a Black-Scholes ratio in the
# thousands, and Schwab's base need not be as small as the model's: uncapped,
# the product is a GEX wall that does not exist. A real move inside one carry
# interval stays well under ten times. Shrinking needs no bound: the ratio
# cannot go below zero.
MAX_GAMMA_RATIO = 10.0
_SIDES = (("callExpDateMap", "call"), ("putExpDateMap", "put"))


def _real(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def years_to_expiry(now, expiry_iso: str) -> float:
    """Years from ``now`` to that expiration's settlement, through the one
    helper every pricer in this folder uses."""
    return expiry_time_to_years(now, datetime.strptime(expiry_iso, "%Y-%m-%d").date())


def _central_date(now) -> str:
    """``now``'s date as the engine would name it. A naive ``now`` is Central
    already (the project convention)."""
    if getattr(now, "tzinfo", None) is not None:
        now = now.astimezone(_CT)
    return now.strftime("%Y-%m-%d")


def _moved(contract, kind, spot0, spot1, t0, t1):
    """One contract with gamma and delta moved from ``spot0`` to ``spot1``.
    Returns ``contract`` itself when there is nothing to model the change with.
    Never writes a number that is not finite."""
    iv = contract.get("volatility")
    strike = contract.get("strikePrice")
    if not _real(iv) or not _real(strike) or strike <= 0:
        return contract
    sigma = iv / 100.0
    if sigma <= 0:                # Schwab's -999 sentinel, zero, or an underflow
        return contract
    try:
        g0 = bs_gamma(spot0, strike, t0, RISK_FREE_RATE, sigma, kind)
        g1 = bs_gamma(spot1, strike, t1, RISK_FREE_RATE, sigma, kind)
        d0 = bs_delta(spot0, strike, t0, RISK_FREE_RATE, sigma, kind)
        d1 = bs_delta(spot1, strike, t1, RISK_FREE_RATE, sigma, kind)
    except (ArithmeticError, ValueError):    # sigma * sqrt(T) rounded to zero
        return contract
    out = dict(contract)

    # A gamma is never negative: anything else is Schwab's "not computed"
    # sentinel, and it is not ours to scale.
    gamma = contract.get("gamma")
    if _real(gamma) and gamma > 0 and _real(g0) and _real(g1) \
            and g0 > _TINY_GAMMA and g1 >= 0:
        new_gamma = gamma * min(g1 / g0, MAX_GAMMA_RATIO)
        if _real(new_gamma):
            out["gamma"] = new_gamma

    # Same for a delta outside [-1, 1].
    delta = contract.get("delta")
    if _real(delta) and -1.0 <= delta <= 1.0 and _real(d0) and _real(d1):
        lo, hi = (0.0, 1.0) if kind == "call" else (-1.0, 0.0)
        out["delta"] = min(hi, max(lo, delta + (d1 - d0)))
    return out


def carry_chain(chain, live_spot, *, age_sec: float, now):
    """``chain`` as it would read at ``live_spot``, ``age_sec`` after it was
    fetched. Returns a new dict. Returns ``chain`` itself, unchanged, when there
    is no usable live price or the chain has no usable price of its own —
    a stale chain is better than an invented one.

    ``now`` is the Central wall clock (aware in any zone, or naive Central)."""
    if not isinstance(chain, dict) or not _real(live_spot) or live_spot <= 0:
        return chain
    spot0 = chain.get("underlyingPrice")
    if not _real(spot0) or spot0 <= 0:
        return chain
    age = age_sec if _real(age_sec) and age_sec > 0 else 0.0
    today = _central_date(now)

    out = dict(chain)
    out["underlyingPrice"] = float(live_spot)
    for map_key, kind in _SIDES:
        exp_map = chain.get(map_key)
        if not isinstance(exp_map, dict) or not exp_map:
            continue
        # The SAME rule the engine uses, so the expiration adjusted here is the
        # one it will read.
        exp_key, _dte = GammaEngine._find_nearest_exp_key(exp_map, today)
        strikes = exp_map.get(exp_key) if exp_key else None
        if not isinstance(strikes, dict):
            continue
        try:
            t1 = years_to_expiry(now, exp_key.split(":")[0])
        except ValueError:                   # a key whose date cannot be read
            continue
        if t1 <= 0:
            # Settled. There is no time left to model a change over, and a
            # floor here would make every carried minute after the close step
            # away from Schwab's own values. Its greeks stand; the price moves.
            continue
        t0 = t1 + age / _SECONDS_PER_YEAR
        new_map = dict(exp_map)
        new_map[exp_key] = {
            strike: ([_moved(c, kind, spot0, live_spot, t0, t1)
                      if isinstance(c, dict) else c for c in contracts]
                     if isinstance(contracts, list) else contracts)
            for strike, contracts in strikes.items()}
        out[map_key] = new_map
    return out
