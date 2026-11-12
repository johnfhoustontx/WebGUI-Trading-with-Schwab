"""A ThinkorSwim bracket order for the Trade detail panel's copy button.

The button puts a "1st triggers OCO" bracket on the clipboard, in the text
ThinkorSwim reads with Trade > Order Entry Tools > "Paste order from clipboard":

    BUY +1 VERTICAL SPX 100 (Weeklys) 30 OCT 26 5800/5810 CALL @1.50 LMT [TO OPEN/TO OPEN]
    SELL -1 VERTICAL SPX 100 (Weeklys) 30 OCT 26 5800/5810 CALL @TRG+100.00% LMT GTC TRG BY #1 OCO [TO CLOSE/TO CLOSE]
    SELL -1 VERTICAL SPX 100 (Weeklys) 30 OCT 26 5800/5810 CALL TRSTP MARK-50.00% MARK GTC TRG BY #1 OCO [TO CLOSE/TO CLOSE]

The opening limit triggers the other two, which cancel each other. The profit
target is a PERCENTAGE of the fill, worked out by ThinkorSwim when the opening
order fills, so it follows the price actually paid rather than the limit. The
stop is a TRAILING stop (``stop_type = "trailing"``, the operator's choice of
2026-11-12): it sits ``trail_pct`` from the trade's MARK and follows the mark
as it moves in the trade's favour, never back. ``stop_type = "fixed"`` writes
the stop pasted on 2026-10-28 instead, ``STP TRG-50.00% MARK``: ``stop_pct``
from the fill. The closing lines repeat the opening one with the side
reversed; the position effect names one ``TO OPEN`` / ``TO CLOSE`` per leg.

The trailing line (``_TRAIL_STOP``) was pasted by the operator on 2026-11-12,
on a single call (``TRSTP MARK-50.00% MARK``) and a put credit vertical
(``TRSTP MARK+100.00% MARK``): each loaded as order type TRAILSTOP, link MARK,
a ``%`` offset, trigger MARK, GTC. ThinkorSwim's manual says trailing stops
are for listed stocks only; its order entry takes them on options and spreads
all the same. Should a structure ever load otherwise, ``stop_type = "fixed"``
brings the 2026-10-28 stop back with no code change.

The opening order is a DAY order, written with no time-in-force word (DAY is
what ThinkorSwim reads when there is none, and the lines pasted on 2026-10-28
had none). The two exits carry ``exit_tif`` (shipped ``GTC``) after the order
type, and after a stop's ``MARK`` trigger: they should outlive the session the
trade opened in.

**One rule orders every named structure's strikes**: ThinkorSwim reads ``+1`` of
a spread as LONG its first-listed leg of each pair. So a debit vertical is
``BUY +1 ... long/short`` and a credit vertical ``SELL -1 ... short/long``; a
sold iron condor is ``SELL -1 IRON CONDOR ... 4730/4740/4730/4720 CALL/PUT``
(short call / long call / short put / long put - the order text of a real
ThinkorSwim fill, which also shows an iron butterfly travels as an IRON CONDOR
with equal short strikes); a calendar or diagonal lists the far expiry first.
Which side opens is read off the LEGS, never off the sign of the price: the
adapters store a debit both as a negative ``credit`` and as a negative
``net_cost``. Only a CUSTOM order that both buys and sells, which has no
geometry of its own, takes its side from the price's sign.

The row's price is the WHOLE position's (the legs' marks times their
quantities), so a position several lots deep is ordered as its smallest whole
ratio at the price divided by the lots.

The structures: a single option, VERTICAL, STRADDLE, STRANGLE, CALENDAR,
DIAGONAL, BUTTERFLY (equal wings), CONDOR, IRON CONDOR (and iron butterfly),
COVERED (100 shares a contract, opening order only: a percentage of a price
that is mostly shares is no exit), and CUSTOM for every other all-option
position (ratios, backspreads, broken wings). A protective put or a collar
holds shares beside a long put, which no ThinkorSwim order text carries, so
they get none. A buy is a debit and takes ``[debit]``'s exits; a sell takes
``[credit]``'s (``config/tos_order.toml``). A debit's trailing stop sits below
the highest mark since the fill, a credit's above the lowest.

Everything here is PURE except :func:`settings`, which reads the config file.
"""
import datetime as _dt
import math

from repo_paths import TOS_ORDER_TOML
from shared import market_calendar, tos_order_config
from shared.config_toml import toml_loader
# The validation is shared with the live-order service (Tier 2), which must price
# the same exits; this page keeps its own loader so its tests can patch ``load``.
from shared.tos_order_config import DEFAULTS, EXIT_TIFS  # noqa: F401  (tests read them)

from ..fmt import num

load, reset_cache = toml_loader(TOS_ORDER_TOML, DEFAULTS, label="tos_order.toml")

# ThinkorSwim's multiplier field, and its tag for a non-monthly expiry.
_MULTIPLIER = "100"
_WEEKLYS = "(Weeklys)"
_LINK = "TRG BY #1 OCO"
# The stop's order type and price. A fixed stop is a percentage of the fill
# (``TRG``); a trailing one a percentage of the moving mark (``MARK``). Both
# trigger on the mark. Both are paste-verified (module doc).
_FIXED_STOP = "STP TRG{sign}{pct:.2f}% MARK"
_TRAIL_STOP = "TRSTP MARK{sign}{pct:.2f}% MARK"
_SHARES_PER_CONTRACT = 100

# A stored row with no ``legs`` keeps its strikes in named keys
# (``signal_repricer._LEG_LAYOUT``, Tier 2): (right, key, +1 long / -1 short).
_KEY_LAYOUT = {
    "PCS": (("PUT", "short_strike", -1), ("PUT", "long_strike", +1)),
    "CCS": (("CALL", "short_strike", -1), ("CALL", "long_strike", +1)),
    "IC": (("PUT", "short_strike", -1), ("PUT", "long_strike", +1),
           ("CALL", "call_short", -1), ("CALL", "call_long", +1)),
    "SHORT_PUT": (("PUT", "short_strike", -1),),
    "SHORT_CALL": (("CALL", "short_strike", -1),),
    "COVERED_CALL": (("CALL", "short_strike", -1),),
}
_KEY_ALIASES = {"IRON_CONDOR": "IC", "NAKED_PUT": "SHORT_PUT",
                "NAKED_CALL": "SHORT_CALL"}


def settings():
    """The validated settings; never raises (``shared.tos_order_config``)."""
    return tos_order_config.settings_from(load())


# ── reading the position ────────────────────────────────────────────────────

def _date(raw):
    try:
        return _dt.date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _leg(right, signed_qty, strike, expiry):
    return {"right": right, "qty": signed_qty, "strike": strike, "expiry": expiry}


def _from_legs(signal, legs):
    """Canonical legs -> ``[{right, qty (signed), strike, expiry}]``, or None
    when any leg is unreadable. A share leg's qty counts 100-share lots."""
    out = []
    for l in legs:
        if not isinstance(l, dict):
            return None
        side = str(l.get("side", "")).lower()
        if side not in ("long", "short"):
            return None
        sign = 1 if side == "long" else -1
        q = num(l.get("qty", 1))
        if q is None or q <= 0 or q != int(q):
            return None
        kind = str(l.get("kind", "")).lower()
        if kind == "stock":
            out.append(_leg("STOCK", sign * int(q), None, None))
            continue
        if kind not in ("call", "put"):
            return None
        strike = num(l.get("strike"))
        expiry = _date(l.get("expiration") or signal.get("expiration"))
        if strike is None or strike <= 0 or expiry is None:
            return None
        out.append(_leg(kind.upper(), sign * int(q), strike, expiry))
    return out


def _from_keys(signal):
    """A stored row's named strike keys -> legs, or None for a type whose
    keys are not known here (an unknown type is never guessed at)."""
    t = str(signal.get("type", "")).upper()
    t = _KEY_ALIASES.get(t, t)
    layout = _KEY_LAYOUT.get(t)
    expiry = _date(signal.get("expiration"))
    if layout is None or expiry is None:
        return None
    out = []
    for right, key, sign in layout:
        strike = num(signal.get(key))
        if strike is None or strike <= 0:
            return None
        out.append(_leg(right, sign, strike, expiry))
    if t == "COVERED_CALL":
        out.insert(0, _leg("STOCK", 1, None, None))
    return out


def position_legs(signal):
    """The position as signed legs, or None when it cannot be read."""
    s = signal if isinstance(signal, dict) else {}
    legs = s.get("legs")
    return _from_legs(s, legs) if legs else _from_keys(s)


# ── text pieces ─────────────────────────────────────────────────────────────

def is_monthly(d):
    """The standard monthly expiry: the third Friday, or the session before it
    when that Friday is a closure (Good Friday, Juneteenth)."""
    tf = market_calendar.third_friday(d.year, d.month)
    if market_calendar.is_trading_day(tf):
        return d == tf
    return d == market_calendar.prev_trading_day(tf)


def expiry_text(d):
    """``30 OCT 26`` / ``6 NOV 26``: ThinkorSwim pads no day."""
    return f"{d.day} {d.strftime('%b %y').upper()}"


def strike_text(k):
    """``5800`` / ``452.5``: ThinkorSwim writes a strike with no padding and no
    thousands separator, so this is not ``fmt.strike``."""
    return f"{k:.3f}".rstrip("0").rstrip(".")


def _root(signal):
    return str((signal or {}).get("symbol") or "").strip().lstrip("$").upper()


def _series(sym, expiries):
    """``SPX 100 (Weeklys)``: tagged when ANY expiry is not the monthly one."""
    tag = "" if all(is_monthly(d) for d in expiries) else f" {_WEEKLYS}"
    return f"{sym} {_MULTIPLIER}{tag}"


def _join(items, fn=str):
    return "/".join(fn(x) for x in items)


# ── naming the structure ────────────────────────────────────────────────────

def _named(sym, opts):
    """``(body, side, name)`` for a named ThinkorSwim spread, or None.

    ``opts`` are option legs whose quantities are already reduced to the
    smallest whole ratio. ``side`` is +1 to BUY the listed spread, -1 to SELL."""
    n = len(opts)
    exps = {l["expiry"] for l in opts}
    rights = {l["right"] for l in opts}
    unit = all(abs(l["qty"]) == 1 for l in opts)
    if n == 1:
        l = opts[0]
        return (f"{_series(sym, [l['expiry']])} {expiry_text(l['expiry'])} "
                f"{strike_text(l['strike'])} {l['right']}",
                1 if l["qty"] > 0 else -1, "single option")
    if n == 2 and unit:
        a, b = opts
        if len(rights) == 1 and a["qty"] == -b["qty"]:
            if len(exps) == 1 and a["strike"] != b["strike"]:
                long_l, short_l = (a, b) if a["qty"] > 0 else (b, a)
                lower_is_long = long_l["strike"] < short_l["strike"]
                debit = lower_is_long if a["right"] == "CALL" else not lower_is_long
                first, second = (long_l, short_l) if debit else (short_l, long_l)
                return (f"VERTICAL {_series(sym, exps)} {expiry_text(a['expiry'])} "
                        f"{strike_text(first['strike'])}/{strike_text(second['strike'])} "
                        f"{a['right']}", 1 if debit else -1, "vertical")
            if len(exps) == 2:
                far, near = sorted(opts, key=lambda l: l["expiry"], reverse=True)
                same = far["strike"] == near["strike"]
                strikes = (strike_text(far["strike"]) if same else
                           f"{strike_text(far['strike'])}/{strike_text(near['strike'])}")
                name = "CALENDAR" if same else "DIAGONAL"
                return (f"{name} {_series(sym, [far['expiry'], near['expiry']])} "
                        f"{expiry_text(far['expiry'])}/{expiry_text(near['expiry'])} "
                        f"{strikes} {far['right']}",
                        1 if far["qty"] > 0 else -1, name.lower())
        if rights == {"CALL", "PUT"} and len(exps) == 1 and a["qty"] == b["qty"]:
            call, put = (a, b) if a["right"] == "CALL" else (b, a)
            exp = expiry_text(a["expiry"])
            side = 1 if a["qty"] > 0 else -1
            if call["strike"] == put["strike"]:
                return (f"STRADDLE {_series(sym, exps)} {exp} "
                        f"{strike_text(call['strike'])}", side, "straddle")
            if call["strike"] < put["strike"]:
                # A "guts" strangle (both legs in the money) has never been
                # pasted as a STRANGLE; CUSTOM is a text that has.
                return None
            return (f"STRANGLE {_series(sym, exps)} {exp} "
                    f"{strike_text(call['strike'])}/{strike_text(put['strike'])} "
                    "CALL/PUT", side, "strangle")
        return None
    if len(exps) != 1:
        return None
    exp = expiry_text(next(iter(exps)))
    legs = sorted(opts, key=lambda l: l["strike"])
    ks = [l["strike"] for l in legs]
    qs = [l["qty"] for l in legs]
    if n == 3 and len(rights) == 1 and len(set(ks)) == 3:
        s = qs[0]
        if qs == [s, -2 * s, s] and abs(s) == 1 and ks[1] - ks[0] == ks[2] - ks[1]:
            return (f"BUTTERFLY {_series(sym, exps)} {exp} {_join(ks, strike_text)} "
                    f"{legs[0]['right']}", s, "butterfly")
        return None
    if n == 4 and unit and len(rights) == 1 and len(set(ks)) == 4:
        s = qs[0]
        if qs == [s, -s, -s, s]:
            return (f"CONDOR {_series(sym, exps)} {exp} {_join(ks, strike_text)} "
                    f"{legs[0]['right']}", s, "condor")
        return None
    if n == 4 and unit and rights == {"CALL", "PUT"}:
        calls = [l for l in legs if l["right"] == "CALL"]
        puts = [l for l in legs if l["right"] == "PUT"]
        if len(calls) != 2 or len(puts) != 2:
            return None
        c_in, c_out = calls                      # inner call = the lower strike
        p_out, p_in = puts                       # inner put = the higher strike
        if (c_in["strike"] == c_out["strike"] or p_in["strike"] == p_out["strike"]
                or c_in["qty"] != -c_out["qty"] or p_in["qty"] != -p_out["qty"]
                or c_in["qty"] != p_in["qty"] or c_in["strike"] < p_in["strike"]):
            return None
        return (f"IRON CONDOR {_series(sym, exps)} {exp} "
                f"{_join([c_in['strike'], c_out['strike'], p_in['strike'], p_out['strike']], strike_text)} "
                "CALL/PUT", c_in["qty"], "iron condor")
    return None


def _custom(sym, opts, debit):
    """ThinkorSwim's CUSTOM order for any other all-option position: the
    ratios, then each leg's expiry, strike and right, legs in expiry, right
    and strike order. Listed as held for a buy, negated for a sell."""
    legs = sorted(opts, key=lambda l: (l["expiry"], l["right"], l["strike"]))
    side = 1 if debit else -1
    ratios = _join([side * l["qty"] for l in legs])
    exps = [l["expiry"] for l in legs]
    return (f"{ratios} CUSTOM {_series(sym, exps)} {_join(exps, expiry_text)} "
            f"{_join([l['strike'] for l in legs], strike_text)} "
            f"{_join([l['right'] for l in legs])}", side, "custom")


def _price_is_debit(signal):
    """True / False from the price's sign, or None when it has none."""
    s = signal if isinstance(signal, dict) else {}
    for key in ("net_cost", "credit"):
        v = num(s.get(key))
        if v is not None and v != 0:
            return v < 0
    return None


def _custom_is_debit(signal, opts):
    """A CUSTOM order's side: every leg bought is a debit and every leg sold a
    credit, whatever sign the price carries; only a mixed position reads the
    price's sign (a debit when it has none)."""
    if all(l["qty"] > 0 for l in opts):
        return True
    if all(l["qty"] < 0 for l in opts):
        return False
    debit = _price_is_debit(signal)
    return True if debit is None else debit


def entry_price(signal):
    """The per-share price to open at, always positive, or None. ``net_cost`` is
    signed (negative = debit) and so is a debit row's ``credit``; only the size
    is used here."""
    s = signal if isinstance(signal, dict) else {}
    v = num(s.get("net_cost"))
    if v is None:
        v = num(s.get("credit"))
    if v is None or v == 0:
        return None
    return abs(v)


def structure(signal):
    """``{body, side, legs, name, bracket, shares, lots}`` for the selected
    trade, or None.

    ``body`` follows ``BUY +q`` / ``SELL -q``; ``legs`` counts the position
    effects; ``bracket`` is False for a position holding shares. ``lots`` is
    how many of the ordered unit the position holds (3 for a vertical three
    deep): the row's price is the whole position's, so the order divides it."""
    legs = position_legs(signal)
    sym = _root(signal)
    if not legs or not sym:
        return None
    stock = [l for l in legs if l["right"] == "STOCK"]
    opts = [l for l in legs if l["right"] != "STOCK"]
    if not opts:
        return None
    if stock:
        # Covered: long shares, one short call a 100-share lot. Anything else
        # with shares (a protective put, a collar) has no order text.
        if (len(stock) != 1 or len(opts) != 1 or opts[0]["right"] != "CALL"
                or stock[0]["qty"] <= 0 or opts[0]["qty"] != -stock[0]["qty"]):
            return None
        c = opts[0]
        return {"body": (f"COVERED {_series(sym, [c['expiry']])} "
                         f"{expiry_text(c['expiry'])} {strike_text(c['strike'])} CALL"),
                "side": 1, "legs": 2, "name": "covered call", "bracket": False,
                "shares": True, "lots": stock[0]["qty"]}
    g = math.gcd(*(abs(l["qty"]) for l in opts))
    unit = [{**l, "qty": l["qty"] // g} for l in opts]
    named = _named(sym, unit)
    if named is None:
        named = _custom(sym, unit, _custom_is_debit(signal, unit))
    body, side, name = named
    return {"body": body, "side": side, "legs": len(opts), "name": name,
            "bracket": True, "shares": False, "lots": g}


def _side_text(side, q):
    return f"BUY +{q}" if side > 0 else f"SELL -{q}"


def order(signal, cfg=None):
    """``{"text", "summary"}`` for the selected trade, or None when it cannot be
    written as a ThinkorSwim order or has no usable price. ``text`` is the
    lines to paste; ``summary`` is one sentence for the button's tooltip."""
    st = structure(signal)
    price = entry_price(signal)
    if st is None or price is None:
        return None
    price /= st["lots"]
    cfg = cfg or settings()
    q = cfg["quantity"]
    if st["shares"]:
        q *= _SHARES_PER_CONTRACT
    opening = "/".join(["TO OPEN"] * st["legs"])
    closing = "/".join(["TO CLOSE"] * st["legs"])
    first = f"{_side_text(st['side'], q)} {st['body']} @{price:.2f} LMT [{opening}]"
    paste = "Paste it with Order Entry Tools > Paste order from clipboard."
    if not st["bracket"]:
        return {"text": first,
                "summary": f"Copies the ThinkorSwim opening order for this "
                           f"{st['name']} at {price:.2f}, with no exits: a "
                           f"percentage of a price that is mostly shares is no "
                           f"target or stop. {paste}"}
    debit = st["side"] > 0
    pcts = cfg["debit"] if debit else cfg["credit"]
    tgt_sign, stp_sign = ("+", "-") if debit else ("-", "+")
    close = _side_text(-st["side"], q)
    tif = "" if cfg["exit_tif"] == "DAY" else f" {cfg['exit_tif']}"
    if cfg.get("stop_type", "trailing") == "trailing":
        stop = _TRAIL_STOP.format(sign=stp_sign, pct=pcts["trail_pct"])
        stop_words = (f"a trailing stop {pcts['trail_pct']:.2f}% "
                      f"{'below the highest' if debit else 'above the lowest'} mark")
    else:
        stop = _FIXED_STOP.format(sign=stp_sign, pct=pcts["stop_pct"])
        stop_words = f"stop at {stp_sign}{pcts['stop_pct']:.2f}%"
    text = "\n".join([
        first,
        f"{close} {st['body']} @TRG{tgt_sign}{pcts['target_pct']:.2f}% LMT{tif} {_LINK} [{closing}]",
        f"{close} {st['body']} {stop}{tif} {_LINK} [{closing}]",
    ])
    word = "debit" if debit else "credit"
    summary = (f"Copies a ThinkorSwim bracket for this {st['name']}: open for a "
               f"{price:.2f} {word}, take profit at {tgt_sign}{pcts['target_pct']:.2f}%, "
               f"{stop_words}, both "
               f"{'good till cancelled' if tif else 'for the day'}. {paste}")
    return {"text": text, "summary": summary}
