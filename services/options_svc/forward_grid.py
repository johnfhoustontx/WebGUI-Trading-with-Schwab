"""The Dealer Positioning heatmaps' projected afternoon.

From the open, every Greek heatmap runs to the close: right of the "now" line are
cells projected by pricing the nearest expiration's standing open interest at
later times, with spot, open interest and implied volatility held where they are
now. Only the time left to expiry moves. Design:
docs/plans/2026-10-23-heatmap-full-session-forward-design.md.

Every formula, unit and sign is ``GammaEngine.calc_all_from_chain``'s, so the
value at the first mark (``now``) IS the collected cell and the picture has no
seam at "now". Imports nothing from ``compute`` (CLAUDE.md: new service code goes
in a sibling module).
"""
import datetime as _dt

from services import _degrade
from shared import market_calendar as _cal

# The four views, in the order calc_all_from_chain returns them.
VIEWS = ("GEX", "Charm", "DEX", "Vanna")
_YEAR_HOURS = 365 * 24
# Below this a value cannot be scaled to six significant figures without
# overflowing a float (10 ** 308 is the ceiling).
_FLUSH = 1e-290


def forward_marks(now, step_min):
    """The instants the projection is priced at: ``now``, each clock multiple of
    ``step_min`` minutes after it, and one minute before the day's close.

    ``[]`` unless today is a trading day, the regular session has opened, and
    there is at least a minute to go: no band before the open, on a weekend or a
    holiday, or after the close. The close itself is left out because at no time
    left the at-the-money gamma, charm and vanna are effectively infinite."""
    ct = now.astimezone(_cal.CT)
    if not _cal.regular_session_has_opened(ct):
        return []
    last = _cal.regular_close_on(ct.date()) - _dt.timedelta(minutes=1)
    if ct >= last:
        return []
    step = _dt.timedelta(minutes=step_min)
    on_minute = ct.replace(second=0, microsecond=0)
    mark = on_minute - _dt.timedelta(minutes=on_minute.minute % step_min) + step
    marks = [ct]
    while mark < last:
        marks.append(mark)
        mark += step
    marks.append(last)
    return marks


def _years_left(dte, mark):
    """``GammaEngine``'s time to expiry at ``mark``: the expiry's whole days plus
    the hours left to its fixed close, in years, floored at 1e-6. Seconds are
    ignored, as the engine ignores them."""
    from gamma_tool import CLOSE_HOUR_CT, CLOSE_MIN_CT
    hours = max(0.0, (CLOSE_HOUR_CT - mark.hour) + (CLOSE_MIN_CT - mark.minute) / 60.0)
    return max((dte * 24 + hours) / _YEAR_HOURS, 1e-6)


def _contracts(exp_map, key, wanted):
    """``(strike, open interest, volatility, chain gamma, chain delta)`` for each
    contract of expiration ``key`` at a wanted strike. A missing delta is NaN."""
    from gamma_tool import _delta, _gamma
    out = []
    for strike_str, contracts in ((exp_map or {}).get(key) or {}).items():
        strike = float(strike_str)
        if strike not in wanted:
            continue
        for c in contracts or ():
            delta = _delta(c)
            out.append((strike, c.get("openInterest", 0) or 0,
                        (c.get("volatility", 0) or 0) / 100.0, _gamma(c),
                        float("nan") if delta is None else delta))
    return out


def _side_values(rows, side, spot, years, r):
    """``{view: values}``, each a (contracts × marks) array for one side."""
    import numpy as np
    from options_calculator import bs_greek_arrays
    strike, oi, iv, gamma, delta = (np.array(col, dtype=float)[:, None]
                                    for col in zip(*rows))
    t = np.array(years, dtype=float)[None, :]
    g = bs_greek_arrays(spot, strike, t, r, iv)
    now = bs_greek_arrays(spot, strike, t[:, :1], r, iv)
    live = iv > 0
    # GEX: the chain's own gamma, scaled by how the model's gamma moves from now.
    denom = now["gamma"]
    ratio = np.where(live & (denom > 0), g["gamma"] / np.where(denom > 0, denom, 1.0), 1.0)
    gex = (-1.0 if side == "put" else 1.0) * gamma * oi * 100 * spot * spot * 0.01 * ratio
    # DEX: the chain's delta moved by the model's change since now; the model's
    # own delta where the chain has none (the engine's fallback).
    shift = -1.0 if side == "put" else 0.0
    model = g["delta_call"] + shift
    usable = ~np.isnan(delta) & (delta != 0)
    chain_d = np.where(usable, delta, 0.0)
    moved = np.where(live, chain_d + model - (now["delta_call"] + shift), chain_d)
    d = np.where(usable, moved, np.where(live, model, 0.0))
    return {"GEX": gex,
            "Charm": np.where(live, g["charm"], 0.0) * oi * 100 * spot,
            "DEX": oi * d * 100 * spot,
            "Vanna": np.where(live, g["vanna"], 0.0) * oi * 100 * spot}, strike[:, 0]


def _significant(arr):
    """``arr`` rounded to the history's significant figures, all at once.

    ``gex_history_db._round_sig`` does it one float at a time and was 90% of a
    build (measured: 38 ms of a 60 ms build at 08:31 on 61 strikes). This is the
    same rounding in numpy; about 3% of values land one unit in the last place
    from the decimal, which costs a few characters of JSON and nothing else."""
    import numpy as np
    from gex_history_db import _GRID_SIG_FIGS
    out = np.array(arr, dtype=float)
    # A far-out strike prices to a denormal, whose six-figure scale overflows to
    # inf and comes back NaN. It is nothing on any colour scale: zero, as the
    # history's own float32 packing makes it.
    out[np.abs(out) < _FLUSH] = 0.0
    live = np.isfinite(out) & (out != 0)
    scale = 10.0 ** (_GRID_SIG_FIGS - 1 - np.floor(np.log10(np.abs(out[live]))))
    out[live] = np.round(out[live] * scale) / scale
    return out


def _build(chain, spot, marks, strikes):
    import numpy as np
    from gamma_tool import GammaEngine
    from options_calculator import RISK_FREE_RATE
    if (not marks or not isinstance(chain, dict) or not chain or isinstance(spot, bool)
            or not isinstance(spot, (int, float)) or not spot > 0 or not strikes):
        return {}
    today = marks[0].astimezone(_cal.CT).strftime("%Y-%m-%d")
    call_map, put_map = chain.get("callExpDateMap") or {}, chain.get("putExpDateMap") or {}
    ck, cdte = GammaEngine._find_nearest_exp_key(call_map, today)
    pk, pdte = GammaEngine._find_nearest_exp_key(put_map, today)
    dtes = [d for d in (cdte, pdte) if d is not None]
    if not dtes:
        return {}
    years = [_years_left(min(dtes), m) for m in marks]
    wanted = set().union(*strikes.values())
    sums = {}                                   # (view, side) -> {strike: row}
    for side, key, exp_map in (("call", ck, call_map), ("put", pk, put_map)):
        rows = _contracts(exp_map, key, wanted) if key else []
        if not rows:
            continue
        values, by = _side_values(rows, side, float(spot), years, RISK_FREE_RATE)
        uniq, idx = np.unique(by, return_inverse=True)
        for view, arr in values.items():
            total = np.zeros((len(uniq), len(marks)))
            np.add.at(total, idx, arr)
            sums[(view, side)] = dict(zip(uniq.tolist(), _significant(total).tolist()))
    head = {"ts": [int(m.timestamp()) for m in marks],
            "times": [m.astimezone(_cal.CT).strftime("%H:%M") for m in marks],
            "spot": spot}
    zero = [0.0] * len(marks)
    out = {}
    for view in VIEWS:
        calls, puts = sums.get((view, "call"), {}), sums.get((view, "put"), {})
        keep = sorted((set(calls) | set(puts)) & set(strikes.get(view) or ()))
        if not keep:
            continue
        out[view] = {**head,
                     "call": {str(k): calls.get(k, zero) for k in keep},
                     "put": {str(k): puts.get(k, zero) for k in keep}}
    return out


def kept_strikes(entry):
    """The float strikes a cropped view entry holds: its by-strike grid and every
    history row's. The band is priced on exactly these, so it covers what the
    heatmap can draw and nothing it cannot."""
    keys = set(((entry or {}).get("data") or {}).get("gex") or {})
    for row in (entry or {}).get("history") or ():
        if len(row) > 6 and isinstance(row[6], dict):
            keys.update(row[6])
    out = set()
    for k in keys:
        try:
            out.add(float(k))
        except (TypeError, ValueError):
            continue
    return out


def attach(views, chain, spot, now, cone):
    """Put each view's band on its entry as ``entry["forward"]``, with the
    expected-move ``cone(marks)`` (``{"mid", "up", "down"}``) on every one.

    Called on the CROPPED views, so ``kept_strikes`` is the display window.
    Nothing is attached when ``[forward] enabled`` is off or ``forward_marks``
    has no marks. Never raises: a failure leaves the views without a band."""
    from shared import gamma_heat_config as _cfg
    try:
        settings = _cfg.forward()
        marks = forward_marks(now, settings["step_min"]) if settings["enabled"] else []
        if not marks:
            return views
        strikes = {v: kept_strikes(e) for v, e in (views or {}).items() if v in VIEWS}
        bands = build(chain, spot, marks, strikes)
        if bands:
            fan = cone(marks)
            for view, band in bands.items():
                views[view]["forward"] = {**band, "cone": fan}
    except Exception:  # noqa: BLE001 - a band is optional; the heatmap draws without it
        _degrade.degraded("options.forward_attach")
    return views


def build(chain, spot, marks, strikes):
    """Each view's projection over ``marks`` at the strikes in ``strikes``
    (``{view: {float strike}}``, the strikes the published history keeps).

    ``{view: {"ts", "times", "spot", "call": {strike: [...]}, "put": {...}}}``,
    one value per mark, strike keys as ``str(float)`` like the history grids,
    rounded as the history is. Only the views named in ``strikes`` that price at
    least one contract appear. Empty on no marks, no spot or no expiration.
    Never raises."""
    try:
        return _build(chain, spot, marks, strikes)
    except Exception:  # noqa: BLE001 - a band is optional; the heatmap draws without it
        _degrade.degraded("options.forward_grid")
        return {}
