"""Group one account's positions into Shares, Options and Other.

Pure (no network, no GUI), like ``view_model``. The Holdings tab shows each
account on its own, and inside an account:

* **Shares** - stocks and exchange-traded funds (Schwab's ``EQUITY`` and
  ``COLLECTIVE_INVESTMENT``).
* **Options** - each option position, except that a long leg and a short leg of
  the SAME underlying, expiration and right (call or put) are paired into ONE
  vertical spread, which is either a credit or a debit spread.
* **Other** - anything else Schwab lists (futures, bonds, money market), so no
  position is ever dropped from the page.

**How legs pair.** Schwab reports each contract as its own position and says
nothing about which legs were opened together, so the pairing is inferred, the
way a broker's own position view does it: within one account, underlying,
expiration and right, the long/short pair with the CLOSEST strikes pairs first,
then the next closest, until one side runs out. Contracts pair one for one; a
leg with more contracts than its partner pairs the partner's count and the rest
stays a single option. That remainder's market value and P&L are the leg's
share by contract count. Two expirations never pair (a calendar stays two
options), and an iron condor shows as its two spreads, one per right.

**Credit or debit is decided by the strikes, never by today's prices**: a put
spread short the HIGHER strike, or a call spread short the LOWER strike, took in
premium when it opened (credit); the other way round it paid (debit).
"""
from __future__ import annotations

import re

from shared.numeric import parsed_finite as _parsed_finite

#: Asset types the Shares group holds.
SHARE_TYPES = ("EQUITY", "COLLECTIVE_INVESTMENT", "ETF")

#: Contract multiplier: an option's quoted price is per share of 100.
OPTION_MULTIPLIER = 100

# OCC option symbol as Schwab sends it: root padded to six, YYMMDD, C/P, the
# strike times 1000 in eight digits ("SPY   261218P00600000").
_OCC_RE = re.compile(r"^\s*([A-Z0-9.$/]{1,6})\s*(\d{2})(\d{2})(\d{2})([CP])(\d{8})\s*$")

# The four verticals, by (right, credit?). The names are the ones the rest of
# the app shows (scanner_structures, tracked_view).
_SPREAD_NAME = {
    ("P", True): "Put Credit Spread",
    ("C", True): "Call Credit Spread",
    ("C", False): "Bull Call Spread",
    ("P", False): "Bear Put Spread",
}
_RIGHT_WORD = {"C": "Call", "P": "Put"}


def parse_option_symbol(symbol):
    """``{"root", "expiry", "right", "strike"}`` for an OCC symbol, else None.

    ``expiry`` is ISO (``"2026-12-18"``), ``right`` is ``"C"`` or ``"P"``, and
    ``strike`` is a float. Anything that is not an OCC symbol is None, never a
    raise: one odd position must not take the page down.
    """
    if not isinstance(symbol, str):
        return None
    m = _OCC_RE.match(symbol.upper())
    if not m:
        return None
    root, yy, mm, dd, right, strike = m.groups()
    return {"root": root, "expiry": f"20{yy}-{mm}-{dd}", "right": right,
            "strike": int(strike) / 1000.0}


def _num(value) -> float:
    """``value`` as a float, or 0.0 when it is no reading. NaN, the infinities
    and a bool are no reading: a NaN market value made the group's ``nan``."""
    out = _parsed_finite(value)
    return 0.0 if out is None else out


def _part(position: dict, contracts: float) -> dict:
    """``position`` cut to ``contracts`` of its contracts (sign kept).

    Market value and P&L scale by the share of contracts kept; the average
    price is per contract and does not.
    """
    qty = _num(position.get("quantity"))
    if not qty or abs(contracts) >= abs(qty):
        return dict(position)
    frac = abs(contracts) / abs(qty)
    out = dict(position)
    out["quantity"] = contracts if qty > 0 else -abs(contracts)
    for key in ("market_value", "day_pl", "total_pl"):
        if position.get(key) is not None:
            out[key] = _num(position.get(key)) * frac
    return out


def _single(position: dict, occ: dict) -> dict:
    qty = _num(position.get("quantity"))
    side = "Long" if qty > 0 else "Short"
    return {
        "kind": "single",
        "underlying": position.get("underlying") or occ["root"],
        "expiry": occ["expiry"],
        "right": occ["right"],
        "label": f"{side} {_RIGHT_WORD[occ['right']]}",
        "strikes": (occ["strike"],),
        "contracts": qty,
        "legs": [position],
        "credit": None,
    }


def _spread(short: dict, long_: dict, contracts: float, occ_s: dict, occ_l: dict) -> dict:
    right = occ_s["right"]
    if right == "P":
        credit = occ_s["strike"] > occ_l["strike"]
    else:
        credit = occ_s["strike"] < occ_l["strike"]
    return {
        "kind": "spread",
        "underlying": short.get("underlying") or occ_s["root"],
        "expiry": occ_s["expiry"],
        "right": right,
        "label": _SPREAD_NAME[(right, credit)],
        "strikes": tuple(sorted((occ_s["strike"], occ_l["strike"]))),
        "short_strike": occ_s["strike"],
        "long_strike": occ_l["strike"],
        "contracts": contracts,
        "legs": [_part(short, -contracts), _part(long_, contracts)],
        "credit": credit,
    }


def pair_options(options: list) -> list:
    """Pair one account's option positions into spreads and single options.

    ``options`` are position dicts (``symbol``, ``underlying``, ``quantity``,
    ``avg_price``, ``market_value``, ``day_pl``, ``total_pl``). Returns option
    GROUPS (see the module docstring), ordered by underlying, expiration, right
    and lowest strike. A position whose symbol is not an OCC symbol comes back
    as a single option with no strike, so it is still shown.
    """
    buckets: dict = {}
    unparsed: list = []
    for pos in options:
        occ = parse_option_symbol(pos.get("symbol"))
        if occ is None or not _num(pos.get("quantity")):
            unparsed.append(pos)
            continue
        key = (pos.get("underlying") or occ["root"], occ["expiry"], occ["right"])
        buckets.setdefault(key, []).append((pos, occ))

    groups: list = []
    for _key, legs in buckets.items():
        # Remaining contracts per leg, by its index in ``legs``.
        left = {i: abs(_num(p.get("quantity"))) for i, (p, _o) in enumerate(legs)}
        shorts = [i for i, (p, _o) in enumerate(legs) if _num(p.get("quantity")) < 0]
        longs = [i for i, (p, _o) in enumerate(legs) if _num(p.get("quantity")) > 0]
        pairs = sorted(
            ((abs(legs[s][1]["strike"] - legs[l_][1]["strike"]),
              legs[s][1]["strike"], legs[l_][1]["strike"], s, l_)
             for s in shorts for l_ in longs))
        for _dist, _ks, _kl, s, l_ in pairs:
            n = min(left[s], left[l_])
            if n <= 0:
                continue
            short_pos, occ_s = legs[s]
            long_pos, occ_l = legs[l_]
            groups.append(_spread(_part(short_pos, -left[s]), _part(long_pos, left[l_]),
                                  n, occ_s, occ_l))
            left[s] -= n
            left[l_] -= n
        for i, (pos, occ) in enumerate(legs):
            if left[i] > 0:
                sign = 1 if _num(pos.get("quantity")) > 0 else -1
                groups.append(_single(_part(pos, sign * left[i]), occ))

    groups.sort(key=lambda g: (g["underlying"] or "", g["expiry"], g["right"],
                               min(g["strikes"])))
    for pos in unparsed:
        groups.append({"kind": "single", "underlying": pos.get("underlying") or pos.get("symbol"),
                       "expiry": None, "right": None, "label": "Option",
                       "strikes": (), "contracts": _num(pos.get("quantity")),
                       "legs": [pos], "credit": None})
    return groups


def group_positions(positions: list) -> dict:
    """``{"shares": [...], "options": [option groups], "other": [...]}``.

    ``shares`` and ``other`` are the position dicts themselves (sorted by
    symbol); ``options`` is :func:`pair_options` over the account's options.
    """
    shares, options, other = [], [], []
    for pos in positions or []:
        kind = pos.get("asset_type")
        if kind == "OPTION":
            options.append(pos)
        elif kind in SHARE_TYPES:
            shares.append(pos)
        else:
            other.append(pos)
    by_symbol = lambda p: p.get("symbol") or ""  # noqa: E731
    return {"shares": sorted(shares, key=by_symbol),
            "options": pair_options(options),
            "other": sorted(other, key=by_symbol)}


def group_value(group: dict) -> dict:
    """An option group's summed ``market_value``, ``day_pl``, ``total_pl``, and
    its per-share ``entry`` and ``mark``.

    For a spread, ``entry`` is the net price it opened at (the credit taken, or
    the debit paid, from the legs' average prices) and ``mark`` is the net it
    is worth now from the legs' market values (for a credit spread, what it
    costs to buy back). Both are per share like a quote, and both are
    magnitudes: the group's ``credit`` flag says which way the money went. For
    a single option they are its own average price and its mark per share.
    """
    legs = group.get("legs") or []
    mv = sum(_num(p.get("market_value")) for p in legs)
    day = sum(_num(p.get("day_pl")) for p in legs)
    total = sum(_num(p.get("total_pl")) for p in legs)
    contracts = abs(_num(group.get("contracts")))
    per = contracts * OPTION_MULTIPLIER
    if group.get("kind") == "spread":
        short, long_ = legs[0], legs[1]
        entry = _num(short.get("avg_price")) - _num(long_.get("avg_price"))
        if group.get("credit") is False:
            entry = -entry          # a debit spread's entry reads as what it cost
        mark = (abs(mv) / per) if per else None
    else:
        entry = _num(legs[0].get("avg_price")) if legs else None
        mark = (abs(mv) / per) if per else None
    return {"market_value": mv, "day_pl": day, "total_pl": total,
            "entry": entry, "mark": mark}
