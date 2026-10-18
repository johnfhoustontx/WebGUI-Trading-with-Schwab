"""The Opening Scalp's trade model: one 0DTE trade priced along the real bars.

Past option prices do not exist, so a trade entered at 08:35 CT is MODELLED:
Black-Scholes at one flat volatility, repriced at each following bar of the
underlying. Pure: no I/O, no proxy, no store write. The study calls it for every
stored morning and the live call for today's.

The four roles (``ROLES``) map to four structures through the morning's
direction (``structure_for``). Prices come from ``options_calculator.bs_price``
and time to expiry ONLY from ``options_calculator.expiry_time_to_years`` (it
settles 16:00 New York and never goes negative; a naive datetime means Central).

How a trade ends (``simulate``), with ``t`` / ``s`` the configured target and stop
percentages (``shared.scalp_config``, read per call):

* a LONG option wins when its value reaches ``entry * (1 + t/100)`` and loses when
  it falls to ``entry * (1 - s/100)``;
* a CREDIT spread is valued as its positive cost to close (short leg minus long
  leg); it wins when that cost falls to ``credit * (1 - t/100)`` (half the credit
  kept at the defaults) and loses when it rises to ``credit * (1 + s/100)``;
* each bar is priced at its END time, at both its high and its low; the extreme
  that favours the trade tests the win level and the other tests the loss level.
  A bar that reaches both is a LOSS (``both_in_bar``): within one bar the order is
  unknown, and the study must not flatter the trade;
* the path is the bars that START in ``[entry, entry + time_stop_min)``; if
  neither level is reached the trade times out and is marked at the last bar's
  close.

A date whose session ended early (the day after Thanksgiving, Christmas Eve)
settles at 12:00 CT, which this model does not know; ``short_session`` flags it
so the study skips the date. Imports nothing from ``compute``.
"""
import datetime as _dt
import math
from typing import NamedTuple
from zoneinfo import ZoneInfo

import repo_paths
from shared import scalp_config
from services.options_svc.scalp_fetch import ct
from services.options_svc.scalp_store import Bar

ROLES = ("long_with", "long_against", "credit_with", "credit_against")

#: ``direction`` +1 (up morning): role -> structure. -1 mirrors every one.
_UP = {"long_with": "long_call", "long_against": "long_put",
       "credit_with": "pcs", "credit_against": "ccs"}
_MIRROR = {"long_call": "long_put", "long_put": "long_call",
           "pcs": "ccs", "ccs": "pcs"}
_LONG = ("long_call", "long_put")

#: The band ``level_points`` searches, as a fraction of the entry-time spot.
_BAND = 0.10
_BISECT_ROUNDS = 80

_CT = ZoneInfo("America/Chicago")
#: Regular session in CT (the study's short-session test reads it).
_OPEN_MIN = 8 * 60 + 30
_CLOSE_MIN = 15 * 60
_FULL_DAY_LAST_START_MIN = 14 * 60 + 55


class Outcome(NamedTuple):
    result: str                     # "win" | "loss" | "timeout" | "skip"
    minutes: "int | None"           # entry -> END of the exit bar (the last bar on a timeout)
    entry_value: float              # the option's price, or the credit
    end_pnl_pct: "float | None"     # see simulate()
    both_in_bar: bool


def _calc():
    """options-scanner's pricing module - the ONE home of the rate, the pricer
    and time-to-expiry (CLAUDE.md: never re-declare them)."""
    import sys
    if str(repo_paths.OPTIONS_SCANNER) not in sys.path:
        sys.path.insert(0, str(repo_paths.OPTIONS_SCANNER))
    import options_calculator
    return options_calculator


def structure_for(role: str, direction: int) -> str:
    """The structure a role trades on a morning whose direction is ``direction``
    (+1 up, -1 down)."""
    if role not in _UP:
        raise ValueError(f"unknown role {role!r}")
    if direction not in (1, -1):
        raise ValueError(f"direction must be +1 or -1, got {direction!r}")
    base = _UP[role]
    return base if direction == 1 else _MIRROR[base]


def strikes_for(structure, spot, step, width, otm_steps):
    """``(primary, second)``: ``(strike, None)`` for a long option, ``(short, long)``
    for a spread. Every contract is at or out of the money, so a spot exactly on a
    strike uses that strike."""
    if structure not in _MIRROR:
        raise ValueError(f"unknown structure {structure!r}")
    up = math.ceil(spot / step) * step
    down = math.floor(spot / step) * step
    if structure == "long_call":
        return (_tidy(up + otm_steps * step), None)
    if structure == "long_put":
        return (_tidy(down - otm_steps * step), None)
    if structure == "pcs":
        short = _tidy(down - otm_steps * step)
        return (short, _tidy(short - width))
    short = _tidy(up + otm_steps * step)
    return (short, _tidy(short + width))


def _tidy(x):
    """Kill float noise from a fractional step (``0.5 * 3``)."""
    return round(float(x), 6)


def value(structure, spot, strikes, when, day, sigma):
    """What the structure is worth: the option's price for a long, the positive
    cost to close (short minus long) for a credit spread. ``when`` is a datetime
    (naive means Central); ``day`` the expiry date."""
    oc = _calc()
    t = oc.expiry_time_to_years(when, day)
    r = oc.RISK_FREE_RATE
    first, second = strikes
    if structure == "long_call":
        return oc.bs_price(spot, first, t, r, sigma, "call")
    if structure == "long_put":
        return oc.bs_price(spot, first, t, r, sigma, "put")
    if structure == "pcs":
        return (oc.bs_price(spot, first, t, r, sigma, "put")
                - oc.bs_price(spot, second, t, r, sigma, "put"))
    if structure == "ccs":
        return (oc.bs_price(spot, first, t, r, sigma, "call")
                - oc.bs_price(spot, second, t, r, sigma, "call"))
    raise ValueError(f"unknown structure {structure!r}")


def _levels(structure, entry_value):
    """``(win_value, loss_value)``: the structure's value at which the trade wins
    and at which it loses."""
    t = scalp_config.target_pct() / 100.0
    s = scalp_config.stop_pct() / 100.0
    if structure in _LONG:
        return entry_value * (1 + t), entry_value * (1 - s)
    return entry_value * (1 - t), entry_value * (1 + s)


def _rises_with_spot(structure):
    """True when the structure's value climbs as the underlying climbs."""
    return structure in ("long_call", "ccs")


def _aware(moment):
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=_CT)


def simulate(structure, spot0, strikes, entry, day, sigma, path: list[Bar],
             interval: int) -> Outcome:
    """Run one trade along ``path`` (bars, ``interval`` minutes each) from ``entry``.

    Returns an ``Outcome``. ``"skip"`` (no minutes, no P&L) when the entry value is
    below ``min_premium`` - the trade was never worth taking - or when there are no
    bars to walk. Otherwise ``entry_value`` is the price paid (long) or the credit.

    ``minutes`` runs from ``entry`` to the END of the exit bar (of the last bar on
    a timeout). ``end_pnl_pct`` is a percentage of ``entry_value``: for a win or a
    loss it is the LEVEL's P&L (``+target_pct`` / ``-stop_pct``: the exit is assumed
    to fill at the level, not at a bar's overshoot); for a timeout it is the
    structure marked at the last bar's close and END time.
    """
    entry = _aware(entry)
    entry_value = value(structure, spot0, strikes, entry, day, sigma)
    if not math.isfinite(entry_value) or entry_value < scalp_config.min_premium():
        return Outcome("skip", None, entry_value, None, False)

    t0 = int(entry.timestamp())
    t1 = t0 + scalp_config.time_stop_min() * 60
    bars = sorted((b for b in path if t0 <= b.ts < t1), key=lambda b: b.ts)
    if not bars:
        return Outcome("skip", None, entry_value, None, False)

    win_v, loss_v = _levels(structure, entry_value)
    long_side = structure in _LONG
    rises = _rises_with_spot(structure)
    tgt = scalp_config.target_pct()
    stp = scalp_config.stop_pct()
    step = _dt.timedelta(minutes=interval)

    for bar in bars:
        end = ct(bar.ts) + step
        v_high = value(structure, bar.high, strikes, end, day, sigma)
        v_low = value(structure, bar.low, strikes, end, day, sigma)
        # The extreme at which the structure is worth the MOST, and the least.
        v_hi, v_lo = (v_high, v_low) if rises else (v_low, v_high)
        if long_side:
            # A long wants the most value: the high-value extreme tests the win.
            won, lost = v_hi >= win_v, v_lo <= loss_v
        else:
            # A credit wants the least cost.
            won, lost = v_lo <= win_v, v_hi >= loss_v
        minutes = int(round((end.timestamp() - t0) / 60.0))
        if lost:
            return Outcome("loss", minutes, entry_value, -stp, won)
        if won:
            return Outcome("win", minutes, entry_value, tgt, False)

    last = bars[-1]
    end = ct(last.ts) + step
    v_end = value(structure, last.close, strikes, end, day, sigma)
    if long_side:
        pnl = (v_end - entry_value) / entry_value * 100.0
    else:
        pnl = (entry_value - v_end) / entry_value * 100.0
    minutes = int(round((end.timestamp() - t0) / 60.0))
    return Outcome("timeout", minutes, entry_value, pnl, False)


def _root(f, lo, hi):
    """Bisection for ``f(x) = 0`` on ``[lo, hi]``; ``None`` when the ends do not
    straddle zero. ``f`` is monotonic in every use here."""
    f_lo, f_hi = f(lo), f(hi)
    if not (math.isfinite(f_lo) and math.isfinite(f_hi)):
        return None
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if (f_lo > 0) == (f_hi > 0):
        return None
    for _ in range(_BISECT_ROUNDS):
        mid = (lo + hi) / 2.0
        f_mid = f(mid)
        if f_mid == 0:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def level_points(structure, spot0, strikes, entry, day, sigma):
    """``(win_points, loss_points)``: how far the underlying must move from
    ``spot0`` (signed, + up) for the structure to reach its win level and its loss
    level at the ENTRY time. Found by bisection within +-10% of ``spot0``; ``None``
    for a level the band cannot reach (a spread's loss level above its width, say)
    or when the entry value is not a positive number."""
    entry = _aware(entry)
    entry_value = value(structure, spot0, strikes, entry, day, sigma)
    if not math.isfinite(entry_value) or entry_value <= 0:
        return None, None
    win_v, loss_v = _levels(structure, entry_value)
    lo, hi = spot0 * (1 - _BAND), spot0 * (1 + _BAND)

    def point(level):
        s = _root(lambda x: value(structure, x, strikes, entry, day, sigma) - level,
                  lo, hi)
        return None if s is None else s - spot0

    return point(win_v), point(loss_v)


def short_session(day_bars_spx: list[Bar]) -> bool:
    """True when the day's regular session ended early: the last $SPX bar that
    STARTS in 08:30-15:00 CT starts before 14:55 CT. No regular-session bar at all
    also reads True (nothing usable to model)."""
    starts = []
    for bar in day_bars_spx:
        local = ct(bar.ts)
        minute = local.hour * 60 + local.minute
        if _OPEN_MIN <= minute < _CLOSE_MIN:
            starts.append(minute)
    if not starts:
        return True
    return max(starts) < _FULL_DAY_LAST_START_MIN


__all__ = ["ROLES", "Outcome", "structure_for", "strikes_for", "value",
           "simulate", "level_points", "short_session"]
