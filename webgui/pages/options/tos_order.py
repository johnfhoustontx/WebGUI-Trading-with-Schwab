"""A ThinkorSwim bracket order for the Trade detail panel's copy button.

The button puts a "1st triggers OCO" bracket on the clipboard, in the text
ThinkorSwim reads with Trade > Order Entry Tools > "Paste order from clipboard":

    BUY +1 VERTICAL SPX 100 (Weeklys) 30 OCT 26 5800/5810 CALL @1.50 LMT [TO OPEN/TO OPEN]
    SELL -1 VERTICAL SPX 100 (Weeklys) 30 OCT 26 5800/5810 CALL @TRG+100.00% LMT TRG BY #1 OCO [TO CLOSE/TO CLOSE]
    SELL -1 VERTICAL SPX 100 (Weeklys) 30 OCT 26 5800/5810 CALL STP TRG-50.00% MARK TRG BY #1 OCO [TO CLOSE/TO CLOSE]

The opening limit triggers the other two, which cancel each other. The two exit
prices are PERCENTAGES of the fill, worked out by ThinkorSwim when the opening
order fills, so they follow the price actually paid rather than the limit.

**Verticals only**: two option legs, one bought and one sold, same right, same
expiry, same quantity. Anything else (a condor, a butterfly, a calendar, a single
option, a position with shares) gets no order, and the panel draws no button.

**The strike order is ThinkorSwim's**: "A/B" is long A, short B for each +1, so
a debit vertical is ``BUY +1 ... long/short`` and a credit vertical is
``SELL -1 ... short/long``. A credit's exits are the mirror of a debit's: the
target is a FALL in the spread's price and the stop a RISE (``config/tos_order.toml``).

Everything here is PURE except :func:`settings`, which reads the config file.
The direction is read off the strikes, never off the sign of the price: the
adapters store a debit both as a negative ``credit`` and as a negative
``net_cost``, and a legacy row may carry neither sign.
"""
import datetime as _dt
import math

from repo_paths import TOS_ORDER_TOML
from shared import market_calendar
from shared.config_toml import toml_loader

from ..fmt import num

DEFAULTS = {
    "quantity": 1,
    "debit": {"target_pct": 100.0, "stop_pct": 50.0},
    "credit": {"target_pct": 50.0, "stop_pct": 100.0},
}

load, reset_cache = toml_loader(TOS_ORDER_TOML, DEFAULTS, label="tos_order.toml")

# ThinkorSwim's multiplier field, and its tag for a non-monthly expiry.
_MULTIPLIER = "100"
_WEEKLYS = "(Weeklys)"
_LINK = "TRG BY #1 OCO"


def _pct_setting(table, key, *, below_100):
    """A percentage from ``[table]``, or the default for a missing or bad one.
    A percentage BELOW the entry must stay under 100: at 100 the price is zero."""
    raw = (load().get(table) or {}).get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) \
            or not math.isfinite(raw) or raw <= 0:
        return DEFAULTS[table][key]
    too_big = (raw >= 100) if below_100 else (raw > 1000)
    if too_big:
        return DEFAULTS[table][key]
    return float(raw)


def settings():
    """The validated settings; never raises."""
    q = load().get("quantity")
    if isinstance(q, bool) or not isinstance(q, (int, float)) or q != int(q) \
            or not 1 <= q <= 1000:
        q = DEFAULTS["quantity"]
    return {
        "quantity": int(q),
        "debit": {"target_pct": _pct_setting("debit", "target_pct", below_100=False),
                  "stop_pct": _pct_setting("debit", "stop_pct", below_100=True)},
        "credit": {"target_pct": _pct_setting("credit", "target_pct", below_100=True),
                   "stop_pct": _pct_setting("credit", "stop_pct", below_100=False)},
    }


def _date(raw):
    try:
        return _dt.date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _is_stock(leg):
    return str(leg.get("kind", "")).lower() == "stock"


def _from_legs(signal, legs):
    """``(long, short, right, expiry)`` from the canonical legs, or None."""
    if any(not isinstance(l, dict) or _is_stock(l) for l in legs) or len(legs) != 2:
        return None
    kinds = {str(l.get("kind", "")).lower() for l in legs}
    if len(kinds) != 1 or not kinds <= {"call", "put"}:
        return None
    by_side = {str(l.get("side", "")).lower(): l for l in legs}
    if set(by_side) != {"long", "short"}:
        return None
    qtys = {num(l.get("qty", 1)) for l in legs}
    if len(qtys) != 1:
        return None
    expiries = {_date(l.get("expiration") or signal.get("expiration")) for l in legs}
    if len(expiries) != 1:
        return None
    return (num(by_side["long"].get("strike")), num(by_side["short"].get("strike")),
            kinds.pop().upper(), expiries.pop())


def _from_keys(signal):
    """The same from a scanner row's strike keys, or None for an iron condor
    and for a type that does not say calls or puts."""
    if signal.get("call_short") is not None or signal.get("call_long") is not None:
        return None
    t = str(signal.get("type", "")).upper()
    if "IC" in t or "CONDOR" in t:
        return None
    if t.startswith("CC") or "CALL" in t:
        right = "CALL"
    elif t.startswith("PC") or "PUT" in t:
        right = "PUT"
    else:
        return None
    return (num(signal.get("long_strike")), num(signal.get("short_strike")),
            right, _date(signal.get("expiration")))


def vertical(signal):
    """``{symbol, long, short, right, expiry, debit}`` for a vertical, else None.

    ``debit`` is read off the strikes: a call vertical is bought when its long
    strike is the lower, a put vertical when it is the higher."""
    s = signal if isinstance(signal, dict) else {}
    legs = s.get("legs")
    parts = _from_legs(s, legs) if legs else _from_keys(s)
    if parts is None:
        return None
    long_k, short_k, right, expiry = parts
    symbol = str(s.get("symbol") or "").strip().lstrip("$").upper()
    if (not symbol or expiry is None or long_k is None or short_k is None
            or long_k <= 0 or short_k <= 0 or long_k == short_k):
        return None
    debit = long_k < short_k if right == "CALL" else long_k > short_k
    return {"symbol": symbol, "long": long_k, "short": short_k, "right": right,
            "expiry": expiry, "debit": debit}


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


def order(signal, cfg=None):
    """``{"text", "summary"}`` for the selected trade, or None when it is not a
    vertical with a usable price. ``text`` is the three lines to paste;
    ``summary`` is one sentence for the button's tooltip."""
    v = vertical(signal)
    price = entry_price(signal)
    if v is None or price is None:
        return None
    cfg = cfg or settings()
    q = cfg["quantity"]
    if v["debit"]:
        open_side, close_side = f"BUY +{q}", f"SELL -{q}"
        first, second = v["long"], v["short"]
        target, stop = cfg["debit"]["target_pct"], cfg["debit"]["stop_pct"]
        tgt_sign, stp_sign, word = "+", "-", "debit"
    else:
        open_side, close_side = f"SELL -{q}", f"BUY +{q}"
        first, second = v["short"], v["long"]
        target, stop = cfg["credit"]["target_pct"], cfg["credit"]["stop_pct"]
        tgt_sign, stp_sign, word = "-", "+", "credit"
    tag = "" if is_monthly(v["expiry"]) else f" {_WEEKLYS}"
    leg = (f"VERTICAL {v['symbol']} {_MULTIPLIER}{tag} {expiry_text(v['expiry'])} "
           f"{strike_text(first)}/{strike_text(second)} {v['right']}")
    text = "\n".join([
        f"{open_side} {leg} @{price:.2f} LMT [TO OPEN/TO OPEN]",
        f"{close_side} {leg} @TRG{tgt_sign}{target:.2f}% LMT {_LINK} [TO CLOSE/TO CLOSE]",
        f"{close_side} {leg} STP TRG{stp_sign}{stop:.2f}% MARK {_LINK} [TO CLOSE/TO CLOSE]",
    ])
    summary = (f"Copies a ThinkorSwim bracket: open for a {price:.2f} {word}, "
               f"take profit at {tgt_sign}{target:.2f}%, stop at {stp_sign}{stop:.2f}%. "
               "Paste it with Order Entry Tools > Paste order from clipboard.")
    return {"text": text, "summary": summary}
