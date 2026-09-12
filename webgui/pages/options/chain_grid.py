"""Pure readers over the (thinned) option chain, shared by the Calculator and
the Simulator, plus the entry panel's chain-grid builders.

The readers moved here from ``calculator.py`` on 2026-09-12 so the Simulator can
read the same chain without importing another PAGE; ``calculator`` re-exports
them by name. No nicegui import — everything here is unit-tested without a
browser.
"""
import datetime as dt
import math


def _finite(v):
    """``v`` as a float when it is a real finite number, else None.

    Bools are rejected: ``True`` is not a premium, and ``isinstance(True, int)``
    would otherwise let one through as 1.0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


# Past this, a "delta" is the chain's missing-greek SENTINEL rather than a
# reading: Schwab sends -999.0 for a greek it does not have, and a real option
# delta never leaves [-1, 1]. ``options_svc.flow_alerts.detect_big_delta`` drops
# the same contracts for the same reason.
_DELTA_LIMIT = 1.0


def extract_atm_iv(chain, spot, expiry=None):
    """ATM implied vol (as a percentage) from an option-chain payload.

    Picks the contract whose strike is closest to ``spot`` and reads its
    ``volatility`` (Schwab returns it as a percentage or a decimal — normalize).
    When ``expiry`` is given, only contracts under that expiry are considered
    (chain exp keys look like ``"2026-06-19:5"``). Returns None if no usable
    volatility is found.
    """
    if not isinstance(chain, dict) or not isinstance(spot, (int, float)):
        return None
    exp_iso = None
    if expiry is not None:
        exp_iso = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    best_diff = float("inf")
    best = None
    for map_key in ("callExpDateMap", "putExpDateMap"):
        for exp_key, strikes in (chain.get(map_key) or {}).items():
            if exp_iso and exp_key.split(":")[0] != exp_iso:
                continue
            for strike_str, contracts in (strikes or {}).items():
                try:
                    strike = float(strike_str)
                except (ValueError, TypeError):
                    continue
                if not (isinstance(contracts, list) and contracts):
                    continue
                vol = contracts[0].get("volatility")
                if vol is None:
                    continue
                diff = abs(strike - spot)
                if diff < best_diff:
                    best_diff = diff
                    best = vol if vol < 5.0 else vol / 100.0
    return None if best is None else best * 100.0


def _find_contract(chain, option_type, strike, expiry=None):
    """The first chain contract matching one leg: call/put map, strike within
    0.51, and — when ``expiry`` is given — that expiry only. None if not found.

    The shared walk behind ``extract_premium`` and ``extract_delta``, so the
    premium and the delta on one leg row are always read off the SAME contract
    and can never come from different strikes.
    """
    if not isinstance(chain, dict) or not isinstance(strike, (int, float)):
        return None
    exp_iso = None
    if expiry is not None:
        exp_iso = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    map_key = "callExpDateMap" if option_type == "call" else "putExpDateMap"
    for exp_key, strikes in (chain.get(map_key) or {}).items():
        if exp_iso and exp_key.split(":")[0] != exp_iso:
            continue
        for strike_str, contracts in (strikes or {}).items():
            try:
                sk = float(strike_str)
            except (ValueError, TypeError):
                continue
            if abs(sk - strike) < 0.51 and isinstance(contracts, list) and contracts:
                return contracts[0]
    return None


def extract_premium(chain, option_type, strike, expiry=None):
    """Premium for one leg (mark, else bid/ask mid) from the chain.

    Matches the strike within 0.51 in the call/put map for ``option_type``.
    When ``expiry`` is given, only that expiry is considered. None if not found.
    """
    c = _find_contract(chain, option_type, strike, expiry)
    if c is None:
        return None
    mark = c.get("mark")
    if mark and mark > 0:
        return mark
    bid = c.get("bid", 0) or 0
    ask = c.get("ask", 0) or 0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return None


def extract_delta(chain, option_type, strike, expiry=None):
    """Per-contract delta for one leg from the cached chain, or ``None``.

    Reads the chain's OWN ``delta`` (the field ``flow_alerts`` uses), so this is
    market delta rather than a second pricing model living in Tier 1.

    ⚠ Returns ``None`` — never ``0.0`` — when the contract carries no usable
    delta. Index option chains read hollow outside regular hours, and a ``0.00``
    on an otherwise live-looking row is a confident wrong number. A genuine
    ``0.0`` (a far out-of-the-money contract) IS kept; what is rejected is the
    missing-greek sentinel, i.e. anything outside ``[-1, 1]``.
    """
    c = _find_contract(chain, option_type, strike, expiry)
    d = _finite(c.get("delta")) if isinstance(c, dict) else None
    if d is None or abs(d) > _DELTA_LIMIT:
        return None
    return d


def position_delta(delta, side):
    """Contract delta signed for the POSITION: a short leg inverts it.

    PER CONTRACT — deliberately not multiplied by ``qty``. The leg row shows it
    beside its own QTY cell, and this is the number that compares directly with
    a broker's chain. An aggregate would be ``sum(position_delta(d, side) * qty)``
    and belongs in its own function, not folded in here."""
    if isinstance(delta, bool) or not isinstance(delta, (int, float)):
        return None
    return -delta if side == "short" else delta


def leg_delta(chain, leg):
    """One leg's POSITION delta from the cached chain, or ``None``.

    TOTAL by construction — no chain, no strike, an unknown option type or a
    strike the chain does not carry all return ``None``, and the leg card's
    DELTA cell renders an em-dash. It runs inside the leg editor's ``_render``,
    where an exception would propagate out of the page build and leave a blank
    screen, so it must not raise rather than being wrapped in a try/except.

    ⚠ Reads the leg's OWN expiry and does NOT fall back to a cross-expiry
    match. ``extract_delta`` returns ``None`` both for "no such contract" and
    for "the contract carries Schwab's -999.0 sentinel", so a fallback cannot
    tell them apart — and ``_find_contract`` answers with the FIRST expiry in
    dict order carrying that strike. A December leg would silently render
    August's delta. (The fallback bought nothing either: ``leg_editor._render``
    coerces every leg's expiry into ``expiries_for()`` before the body reads it,
    so a leg dated off the loaded ladder cannot reach here.)
    """
    leg = leg or {}
    strike = leg.get("strike")
    otype = leg.get("option_type")
    if not chain or otype not in ("call", "put"):
        return None
    if isinstance(strike, bool) or not isinstance(strike, (int, float)):
        return None
    d = extract_delta(chain, otype, float(strike), leg.get("expiry") or None)
    return position_delta(d, leg.get("side"))


def chain_expiries(chain):
    """Sorted unique expiry strings (YYYY-MM-DD) from an option-chain payload."""
    out = set()
    for map_key in ("callExpDateMap", "putExpDateMap"):
        for exp_key in (chain or {}).get(map_key) or {}:
            out.add(exp_key.split(":")[0])
    return sorted(out)


def chain_strikes(chain, expiry, option_type):
    """Sorted strikes for one expiry + option_type (call/put)."""
    map_key = "callExpDateMap" if option_type == "call" else "putExpDateMap"
    out = set()
    for exp_key, strikes in ((chain or {}).get(map_key) or {}).items():
        if exp_key.split(":")[0] != str(expiry):
            continue
        for s in strikes or {}:
            try:
                out.add(float(s))
            except (ValueError, TypeError):
                continue
    return sorted(out)
