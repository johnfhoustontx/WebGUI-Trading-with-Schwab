"""Turn a position's legs into the order JSON the Schwab Trader API takes.

Design: docs/plans/2026-11-05-live-order-to-thinkorswim-design.md.

PHASE 0 uses this for one thing: building orders for Schwab's ``previewOrder``,
which checks an order and places nothing. The phase exists to find out what
Schwab accepts, so the builder can write three shapes of the same order
(``VARIANTS``):

* ``open``: the opening limit order alone;
* ``target``: the opening order, which triggers a profit-target limit;
* ``bracket``: the opening order, which triggers a one-cancels-the-other pair, the
  profit target and a stop. This is the API's form of the paste script's
  "1st triggers OCO" (``webgui/pages/options/tos_order.py``).

The exits are priced off the opening order's LIMIT, using the paste script's
percentages (``config/tos_order.toml``). The script's ``TRG+100%`` prices off the
FILL, which only ThinkorSwim can do, so the operator chose the limit.

Legs use the app's canonical shape, the one ``tos_order`` and the trade ideas
read: ``{"kind": "call"|"put", "side": "long"|"short", "strike", "qty",
"expiration", "symbol"}``. ``symbol`` is the contract's Schwab symbol
(``SPXW  261106C05800000``), taken from the chain, never built here: the root
(SPX or SPXW, NDX or NDXP) depends on the series, and the chain knows it.

PURE: no network, no config read, no clock. Bad input raises ``ValueError``.
"""
import math
import re

VARIANTS = ("open", "target", "bracket")

#: Schwab's option symbol: a 6-character root padded with spaces, YYMMDD, C or
#: P, and the strike times 1000 in 8 digits.
OPTION_SYMBOL_RE = re.compile(r"^[A-Z0-9 ]{6}\d{6}[CP]\d{8}$")

MAX_LEGS = 4
MAX_CONTRACTS = 1000


def _finite_positive(v):
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v) and v > 0)


def check_legs(legs):
    """The legs, each with ``sign`` (+1 long, -1 short) and an int ``qty``.
    Raises ValueError on anything it cannot read."""
    if not isinstance(legs, (list, tuple)) or not 1 <= len(legs) <= MAX_LEGS:
        raise ValueError(f"an order takes 1 to {MAX_LEGS} legs")
    out = []
    for leg in legs:
        if not isinstance(leg, dict):
            raise ValueError("a leg must be a mapping")
        kind = str(leg.get("kind", "")).lower()
        side = str(leg.get("side", "")).lower()
        if kind not in ("call", "put"):
            raise ValueError(f"a leg's kind must be call or put, not {kind!r}")
        if side not in ("long", "short"):
            raise ValueError(f"a leg's side must be long or short, not {side!r}")
        qty = leg.get("qty", 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            raise ValueError(f"a leg's qty must be a whole number of 1 or more, not {qty!r}")
        strike = leg.get("strike")
        if not _finite_positive(strike):
            raise ValueError(f"a leg's strike must be a positive number, not {strike!r}")
        symbol = leg.get("symbol")
        if not isinstance(symbol, str) or not OPTION_SYMBOL_RE.match(symbol):
            raise ValueError(f"a leg's symbol is not a Schwab option symbol: {symbol!r}")
        out.append({**leg, "kind": kind, "side": side, "qty": qty,
                    "sign": 1 if side == "long" else -1})
    return out


def complex_type(legs):
    """Schwab's ``complexOrderStrategyType`` for the legs: ``NONE`` for one leg,
    ``VERTICAL``, ``IRON_CONDOR``, or ``CUSTOM`` for anything else."""
    legs = check_legs(legs)
    if len(legs) == 1:
        return "NONE"
    exps = {str(l.get("expiration"))[:10] for l in legs}
    kinds = {l["kind"] for l in legs}
    unit = all(l["qty"] == 1 for l in legs)
    if len(legs) == 2 and unit and len(kinds) == 1 and len(exps) == 1 \
            and legs[0]["sign"] == -legs[1]["sign"] and legs[0]["strike"] != legs[1]["strike"]:
        return "VERTICAL"
    if len(legs) == 4 and unit and kinds == {"call", "put"} and len(exps) == 1:
        calls = sorted((l for l in legs if l["kind"] == "call"), key=lambda l: l["strike"])
        puts = sorted((l for l in legs if l["kind"] == "put"), key=lambda l: l["strike"])
        if len(calls) == 2 and len(puts) == 2:
            c_in, c_out = calls
            p_out, p_in = puts
            if (c_in["sign"] == -c_out["sign"] and p_in["sign"] == -p_out["sign"]
                    and c_in["sign"] == p_in["sign"]
                    and c_in["strike"] >= p_in["strike"]
                    and c_in["strike"] != c_out["strike"]
                    and p_in["strike"] != p_out["strike"]):
                return "IRON_CONDOR"
    return "CUSTOM"


def is_debit(legs):
    """True when opening the position PAYS, False when it is paid, read off the
    LEGS (never off the sign of a price), or None when the legs alone cannot
    say (a mixed CUSTOM position).

    The same answers as ``tos_order``'s side: ``+1`` there is a debit here."""
    legs = check_legs(legs)
    ctype = complex_type(legs)
    if ctype == "NONE":
        return legs[0]["sign"] > 0
    if ctype == "VERTICAL":
        long_l = next(l for l in legs if l["sign"] > 0)
        short_l = next(l for l in legs if l["sign"] < 0)
        lower_is_long = long_l["strike"] < short_l["strike"]
        return lower_is_long if long_l["kind"] == "call" else not lower_is_long
    if ctype == "IRON_CONDOR":
        inner_call = min((l for l in legs if l["kind"] == "call"), key=lambda l: l["strike"])
        return inner_call["sign"] > 0
    if all(l["sign"] > 0 for l in legs):
        return True
    if all(l["sign"] < 0 for l in legs):
        return False
    return None


# ── prices ──────────────────────────────────────────────────────────────────

def tick_for(price, root, ticks):
    """The price step for ``price`` on ``root`` (``SPY``, ``$SPX``), from the
    ``[ticks]`` table of ``config/live_orders.toml``."""
    penny = str(root or "").lstrip("$").upper() in set(ticks.get("penny") or ())
    if price < 3:
        return ticks["penny_under_3"] if penny else ticks["under_3"]
    return ticks["penny_from_3"] if penny else ticks["from_3"]


def round_to_tick(price, root, ticks):
    """``price`` on its step, never below one step. Rounded in whole cents so
    a float like 1.4999999 cannot print as 1.50 on one side and 1.49 on another."""
    if not _finite_positive(price):
        raise ValueError(f"a price must be a positive number, not {price!r}")
    step = tick_for(price, root, ticks)
    cents_step = max(1, round(step * 100))
    cents = round(round(price * 100) / cents_step) * cents_step
    return max(cents, cents_step) / 100


def exit_prices(limit, debit, pcts, root, ticks):
    """``(target, stop)`` per unit, from the opening LIMIT price and the
    paste script's percentages (``pcts`` is ``tos_order.settings()``'s
    ``debit`` or ``credit`` table).

    A debit's target is a rise and its stop a fall; a credit's target is a fall
    and its stop a rise."""
    if not _finite_positive(limit):
        raise ValueError(f"the limit must be a positive number, not {limit!r}")
    t = pcts["target_pct"] / 100
    s = pcts["stop_pct"] / 100
    if debit:
        target, stop = limit * (1 + t), limit * (1 - s)
    else:
        target, stop = limit * (1 - t), limit * (1 + s)
    return round_to_tick(target, root, ticks), round_to_tick(stop, root, ticks)


def _price_text(p):
    return f"{p:.2f}"


# ── the order ───────────────────────────────────────────────────────────────

def _instruction(sign, opening):
    buy = sign > 0 if opening else sign < 0
    return f"{'BUY' if buy else 'SELL'}_TO_{'OPEN' if opening else 'CLOSE'}"


def _leg_collection(legs, contracts, opening):
    return [{"instruction": _instruction(l["sign"], opening),
             "quantity": l["qty"] * contracts,
             "instrument": {"assetType": "OPTION", "symbol": l["symbol"]}}
            for l in legs]


def _priced(ctype, pays):
    """The order type of a limit order: a single leg is LIMIT, a spread names
    which way the money goes."""
    if ctype == "NONE":
        return "LIMIT"
    return "NET_DEBIT" if pays else "NET_CREDIT"


def _with_complex(order, ctype):
    if ctype != "NONE":
        order["complexOrderStrategyType"] = ctype
    return order


def schwab_order(legs, *, contracts, limit, variant="bracket", debit=None,
                 pcts=None, exit_tif="GTC", root="", ticks=None):
    """The order JSON for ``legs`` at ``contracts`` and the per-unit ``limit``.

    ``debit`` is required only when :func:`is_debit` cannot say. ``pcts`` and
    ``ticks`` are needed for the ``target`` and ``bracket`` variants. The
    opening order is DAY; the exits are ``exit_tif`` (``GTC`` or ``DAY``), as
    the paste script writes them."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}, not {variant!r}")
    if isinstance(contracts, bool) or not isinstance(contracts, int) \
            or not 1 <= contracts <= MAX_CONTRACTS:
        raise ValueError(f"contracts must be a whole number from 1 to {MAX_CONTRACTS}")
    if exit_tif not in ("GTC", "DAY"):
        raise ValueError(f"exit_tif must be GTC or DAY, not {exit_tif!r}")
    legs = check_legs(legs)
    ctype = complex_type(legs)
    side = is_debit(legs)
    if side is None:
        if debit is None:
            raise ValueError("these legs both buy and sell: say whether it opens for a debit")
        side = bool(debit)
    elif debit is not None and bool(debit) != side:
        raise ValueError("debit= contradicts what the legs say")
    if ticks is None:
        raise ValueError("ticks are required to put the limit on its price step")
    limit = round_to_tick(limit, root, ticks)

    opening = _with_complex({
        "orderStrategyType": "SINGLE",
        "orderType": _priced(ctype, side),
        "price": _price_text(limit),
        "duration": "DAY",
        "session": "NORMAL",
        "orderLegCollection": _leg_collection(legs, contracts, opening=True),
    }, ctype)
    if variant == "open":
        return opening

    if pcts is None:
        raise ValueError("pcts are required for the exits")
    target, stop = exit_prices(limit, side, pcts, root, ticks)
    duration = "GOOD_TILL_CANCEL" if exit_tif == "GTC" else "DAY"
    closing_legs = _leg_collection(legs, contracts, opening=False)
    # Closing a debit position is paid a credit, and the other way round.
    target_order = _with_complex({
        "orderStrategyType": "SINGLE",
        "orderType": _priced(ctype, not side),
        "price": _price_text(target),
        "duration": duration,
        "session": "NORMAL",
        "orderLegCollection": closing_legs,
    }, ctype)
    if variant == "target":
        children = [target_order]
    else:
        stop_order = _with_complex({
            "orderStrategyType": "SINGLE",
            "orderType": "STOP",
            "stopPrice": _price_text(stop),
            "duration": duration,
            "session": "NORMAL",
            "orderLegCollection": [dict(l) for l in closing_legs],
        }, ctype)
        children = [{"orderStrategyType": "OCO",
                     "childOrderStrategies": [target_order, stop_order]}]
    return {**opening, "orderStrategyType": "TRIGGER",
            "childOrderStrategies": children}
