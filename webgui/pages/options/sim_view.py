"""Pure readouts for the Simulator page (Tier-1, no ``nicegui``).

The Simulator used to hide every number a trader wants behind a chart hover —
the entry, the worst case, where it breaks even, what a slider position is
worth. This module states them. ``simulator.py`` holds the widgets and wiring;
everything here is arithmetic over the payloads the options service already
caches, so it is unit-tested without a browser (the ``rotation_view`` pattern).

**Units.** ``sim_run``'s What-if rows have always been in POSITION dollars (the
service applies the x100 contract multiplier). Its IV-shock rows and the Replay
Greeks were per share until 2026-09-11, when the service started scaling them too
and marking the payload ``units: "position"``. ``position_units`` is the one
place a payload WITHOUT that marker — a cache written before the upgrade — is
brought into line, so a stale cache cannot print per-share numbers under
position labels.

**Absence is never a zero.** Every figure here is either a real reading or
``None``, and every display string for ``None`` is the shared em-dash.
"""
import datetime as _dt
import math
from zoneinfo import ZoneInfo

from pages.fmt import NO_READING, num

SHARES_PER_CONTRACT = 100
POSITION_UNITS = "position"
_GREEK_COLS = ("theo_price", "delta", "gamma", "theta", "vega", "rho")

# The one settlement instant the whole project prices to (CLAUDE.md, "A TZ-NAIVE
# datetime in this project means CENTRAL time"): 16:00 America/New_York.
_SETTLE_TZ = ZoneInfo("America/New_York")
_SETTLE_HOUR = 16


def _money(v, signed=False):
    """``$1,234`` (whole dollars — a position figure, not a quote)."""
    v = num(v)
    if v is None:
        return NO_READING
    txt = f"${abs(v):,.0f}"
    if signed:
        return f"{'+' if v >= 0 else '-'}{txt}"
    return f"-{txt}" if v < 0 else txt


# -- units ---------------------------------------------------------------------

def position_units(row, units):
    """``row`` (an IV-shock / Greek dict) in POSITION units.

    ``units == "position"`` means the service already applied the contract
    multiplier; anything else is a pre-upgrade per-share payload and is scaled
    here. Non-numeric cells pass through untouched (``num`` decides what counts)."""
    row = dict(row or {})
    if units == POSITION_UNITS:
        return row
    for col in _GREEK_COLS:
        v = num(row.get(col))
        if v is not None:
            row[col] = v * SHARES_PER_CONTRACT
    return row


def position_greeks(result):
    """The position's Greeks at today's volatility — the IV-shock BASE row, which
    ``sim_run`` already computes — in position units, or ``None``."""
    shock = (result or {}).get("ivshock") or {}
    base = shock.get("base")
    if not base:
        return None
    return position_units(base, shock.get("units"))


# -- Task 1: the expiration payoff ---------------------------------------------

def _leg_rows(legs):
    """``[(kind, sign, qty, strike, expiry)]`` or ``None`` when any leg is
    unusable (no strike, a junk qty) — the caller then claims nothing."""
    rows = []
    for leg in legs or []:
        strike = num(leg.get("strike"))
        qty = num(leg.get("qty", 1))
        if strike is None or qty is None or qty <= 0:
            return None
        kind = "call" if leg.get("option_type") == "call" else "put"
        sign = -1 if leg.get("side") == "short" else 1
        rows.append((kind, sign, qty, strike, str(leg.get("expiry") or "")))
    return rows or None


def payoff_facts(legs, baseline):
    """Entry, max profit, max loss and breakevens of the EXPIRATION payoff.

    ``baseline`` is the service's ``whatif_baseline`` — the position's model value
    at today's spot and time — so the entry cash is ``-baseline`` (a credit
    spread's value is negative, and ``-value`` is the credit received).

    The expiration payoff is piecewise linear with corners only at the strikes,
    so evaluating it at ``{0} ∪ strikes`` and reading the slope beyond the last
    strike (the net call quantity) decides every figure EXACTLY — the method the
    Calculator's ``max_loss_estimate`` uses. Net long calls make profit
    ``"unlimited"``; net short calls make loss ``"unlimited"``.

    ``reason`` names why the expiry figures are absent:

    * ``"incomplete"`` — no legs, or a leg without a usable strike / qty;
    * ``"unpriced"`` — no usable baseline, so there is no entry to measure from;
    * ``"mixed_expiry"`` — legs settle on different dates, and a single-date
      payoff would settle a live back leg at intrinsic value.
    """
    entry = num(baseline)
    entry = -entry if entry is not None else None
    out = {"entry": entry, "max_profit": None, "max_loss": None,
           "breakevens": None, "reason": None}
    rows = _leg_rows(legs)
    if rows is None:
        out["reason"] = "incomplete"
        return out
    if entry is None:
        out["reason"] = "unpriced"
        return out
    if len({r[4] for r in rows}) > 1:
        out["reason"] = "mixed_expiry"
        return out

    def payoff(s):
        v = entry
        for kind, sign, qty, strike, _ in rows:
            intrinsic = max(s - strike, 0.0) if kind == "call" else max(strike - s, 0.0)
            v += sign * qty * SHARES_PER_CONTRACT * intrinsic
        return v

    corners = sorted({0.0} | {r[3] for r in rows})
    values = [payoff(s) for s in corners]
    slope_above = sum(sign * qty * SHARES_PER_CONTRACT
                      for kind, sign, qty, _, _ in rows if kind == "call")

    out["max_profit"] = "unlimited" if slope_above > 0 else round(max(values), 2)
    if slope_above < 0:
        out["max_loss"] = "unlimited"
    else:
        worst = min(values)
        out["max_loss"] = round(-worst, 2) if worst < 0 else 0.0

    roots = []
    for (a, va), (b, vb) in zip(zip(corners, values), zip(corners[1:], values[1:])):
        if va == 0 and a > 0:
            roots.append(a)
        elif va * vb < 0:
            roots.append(a + (0 - va) * (b - a) / (vb - va))
    last, v_last = corners[-1], values[-1]
    if v_last == 0 and last > 0:
        roots.append(last)
    elif slope_above and v_last * slope_above < 0:
        roots.append(last - v_last / slope_above)
    out["breakevens"] = sorted({round(r, 2) for r in roots})
    return out


# -- Task 2: the six tiles -------------------------------------------------------

_REASON_TEXT = {
    "incomplete": "pick a strike for every leg",
    "unpriced": "waiting for a price",
    "mixed_expiry": "legs expire on different dates",
}


def _tile(key, label, value=NO_READING, sub="", tone="neutral"):
    return {"key": key, "label": label, "value": value, "sub": sub, "tone": tone}


def _cap_text(v):
    if v == "unlimited":
        return "Unlimited"
    return _money(v)


def position_tiles(legs, result):
    """The six position tiles, ALWAYS six and always in this order, so the grid
    never reflows between renders: Entry · Max profit · Max loss · Breakeven(s) ·
    Delta · Theta per day. Every absence is an em-dash with a reason line."""
    result = result or {}
    facts = payoff_facts(legs, result.get("whatif_baseline")) if result else \
        {"entry": None, "max_profit": None, "max_loss": None,
         "breakevens": None, "reason": "unpriced"}
    why = _REASON_TEXT.get(facts["reason"], "")

    entry = facts["entry"]
    if entry is None:
        t_entry = _tile("entry", "Entry", sub=why)
    else:
        t_entry = _tile("entry", "Entry credit" if entry >= 0 else "Entry debit",
                        _money(abs(entry)), "model price at today's spot")

    if facts["max_profit"] is None:
        t_profit = _tile("max_profit", "Max profit", sub=why)
    else:
        t_profit = _tile("max_profit", "Max profit", _cap_text(facts["max_profit"]),
                         "at expiration", "pos")
    if facts["max_loss"] is None:
        t_loss = _tile("max_loss", "Max loss", sub=why)
    else:
        t_loss = _tile("max_loss", "Max loss", _cap_text(facts["max_loss"]),
                       "at expiration", "neg")

    bes = facts["breakevens"]
    if bes is None:
        t_be = _tile("breakeven", "Breakeven", sub=why)
    elif not bes:
        t_be = _tile("breakeven", "Breakeven", "None", "never crosses zero at expiration")
    else:
        label = "Breakeven" if len(bes) == 1 else "Breakevens"
        if len(bes) == 2:
            value = f"{bes[0]:,.2f} and {bes[1]:,.2f}"
        else:
            value = ", ".join(f"{b:,.2f}" for b in bes)
        spot = num(result.get("spot"))
        sub = ""
        if spot:
            pct = (bes[0] - spot) / spot * 100.0
            sub = f"{abs(pct):.1f}% {'above' if pct >= 0 else 'below'} spot"
        t_be = _tile("breakeven", label, value, sub)

    greeks = position_greeks(result) or {}
    delta = num(greeks.get("delta"))
    if delta is None:
        t_delta = _tile("delta", "Delta")
    else:
        shares = abs(round(delta))
        side = "long" if delta >= 0 else "short"
        t_delta = _tile("delta", "Delta", f"{delta:+,.0f}",
                        f"moves like {shares:,} shares {side}")
    theta = num(greeks.get("theta"))
    if theta is None:
        t_theta = _tile("theta", "Theta per day")
    else:
        t_theta = _tile("theta", "Theta per day", _money(theta, signed=True),
                        "earned from time passing" if theta >= 0 else "lost to time passing",
                        "pos" if theta > 0 else ("neg" if theta < 0 else "neutral"))
    return [t_entry, t_profit, t_loss, t_be, t_delta, t_theta]
