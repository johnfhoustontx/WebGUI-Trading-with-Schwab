"""The factors of the Opening Scalp's weighted chance, assembled and scaled
(design 2026-10-22-scalp-weighted-probability, sections 3 and 5).

One function builds a call's factors whether the call is history (the fit) or live
(the chance): ``factors`` takes the call's raw inputs, its trend readings and the
``scalp_confirm.snapshot`` of its sources, and returns every factor SIGNED by the
call's direction ``d`` (+1 up, -1 down): positive means the factor agrees with the
call. ``scaled`` then divides each continuous factor by the history's scale.

Rules:

* **A gap is ``None`` until scaling, then 0.** A factor that cannot be read (no
  wall because Schwab sent no Greeks, no flow list, a missing bar) is ``None`` here
  and 0.0 in ``scaled``: it neither helps nor hurts the chance. An UNTRUSTED wall
  (net GEX of 0 or missing) is a gap, never "no room".
* **Scaling never centres.** A continuous factor is divided by its interquartile
  range and capped; 0 stays 0, so a gap and "no agreement either way" are the same
  number. The discrete factors (flow, sentiment, flip side and the block
  indicators) are never rescaled.
* **The dealer read reuses ``scalp_confirm``'s own helpers** (``_wall_view``,
  ``_room``, ``_STATE_SENSE``): the chance reads a wall exactly as the confirmation
  does. The snapshot's symbols are matched with ``$`` and case ignored.
* Pure functions of what they are given: no bus, no store, no import from
  ``compute``.
"""
import math

from shared import scalp_config
from shared import symbols as _symbols
from shared.numeric import finite as _finite

from services.options_svc import scalp_confirm as _confirm

#: A trade's sign against the call's own direction: the trades against the move
#: negate the priors of the trades with it (design section 5).
ROLE_SIGN = {"long_with": 1, "credit_with": 1, "long_against": -1, "credit_against": -1}

#: Factor-name prefixes whose prior is WIDE (they have ~180 days of history);
#: everything else has the tight prior.
WIDE = frozenset({"price:", "slope_", "rsi_", "block:"})

#: Never rescaled: already -1 / 0 / +1 (or the flow count over its cap), or an
#: indicator.
DISCRETE = frozenset({"flip_side", "sentiment", "flow", "block:midday", "block:afternoon"})

#: Inputs measured as a position in a range, 0..1: centred to -1..+1 and signed.
_POSITIONS = ("fut_range_pos", "open_close_loc")
#: Inputs that carry no direction of their own: read as they are.
_UNSIGNED = ("open_range_ratio",)

_SOURCE_NAMES = ("room", "breadth_room", "flip_side", "netprem", "flow", "sentiment")


def is_wide(name) -> bool:
    """True when the factor's prior is the wide one (``WIDE``'s prefixes)."""
    return isinstance(name, str) and name.startswith(tuple(WIDE))


# --- the price inputs -----------------------------------------------------------

def price_factors(inputs, d) -> dict:
    """Each input as ``price:<key>``, signed by ``d``. A percent move is times
    ``d``; the volatility input is times ``-d`` (rising volatility is against
    an up call); a 0..1 share or position is centred and scaled to -1..+1 first;
    the open-range ratio has no direction and stays as it is. ``None`` (and a
    value that is not a finite number) stays ``None``."""
    out = {}
    for key, raw in (inputs if isinstance(inputs, dict) else {}).items():
        value = _finite(raw)
        name = f"price:{key}"
        if value is None:
            out[name] = None
        elif key in _UNSIGNED:
            out[name] = value
        elif key == "vol_change":
            out[name] = value * -d
        elif key == "breadth_up_share" or key in _POSITIONS:
            out[name] = (value - 0.5) * 2.0 * d
        else:
            out[name] = value * d
    return out


def trend_signed(trend, d) -> dict:
    """The trend readings signed by ``d``: slope (percent) times ``d``; RSI
    (0..100) as ``(rsi - 50) / 50`` times ``d``. A reading that is not there stays
    ``None``."""
    out = {}
    for key, raw in (trend if isinstance(trend, dict) else {}).items():
        value = _finite(raw)
        if key.startswith("slope_"):
            out[key] = None if value is None else value * d
        elif key.startswith("rsi_"):
            out[key] = None if value is None else (value - 50.0) / 50.0 * d
    return out


# --- the sources ------------------------------------------------------------------

def _by_symbol(mapping) -> dict:
    """A snapshot mapping keyed by the names it was asked for, by normalised name."""
    out = {}
    for key, value in (mapping if isinstance(mapping, dict) else {}).items():
        out.setdefault(_confirm._norm(key), value)
    return out


def _dealer(rows, symbol, d, win_points, entry_spot) -> dict:
    """``room``, ``breadth_room`` and ``flip_side`` from the snapshot's matrix."""
    out = {"room": None, "breadth_room": None, "flip_side": None}
    own = rows.get(_confirm._norm(symbol))
    win = _finite(win_points)
    win = abs(win) if win else None                     # 0 and None are no target
    spot = _confirm._positive(entry_spot)
    if own is not None:
        view = _confirm._wall_view(own)
        view = dict(view, spot=spot) if spot is not None else view
        wall, room = _confirm._room(view, d)
        if room is not None and win is not None:
            cap = scalp_config.prob_room_cap()
            out["room"] = max(-cap, min(cap, room / win))
        if not view["walls_untrusted"] and view["flip"] is not None \
                and view["spot"] is not None and view["spot"] != view["flip"]:
            out["flip_side"] = 1 if (view["spot"] > view["flip"]) == (d > 0) else -1
    if win is not None and spot is not None and rows:
        win_pct = win / spot * 100.0
        read = with_room = 0
        for member in _symbols.big10():
            view = _confirm._wall_view(rows.get(_confirm._norm(member)))
            wall, room = _confirm._room(view, d)
            if wall is None:
                continue
            read += 1
            if room / view["spot"] * 100.0 >= win_pct:
                with_room += 1
        if read and read >= scalp_config.breadth_min_members():
            out["breadth_room"] = with_room / read - 0.5
    return out


def _netprem(levels, symbol, d):
    level = levels.get(_confirm._norm(symbol))
    if not isinstance(level, dict):
        return None
    call, put = _finite(level.get("call")), _finite(level.get("put"))
    if call is None or put is None or call < 0 or put < 0 or call + put <= 0:
        return None
    return (call - put) / (call + put) * d


def _flow(flow, d):
    window = flow.get("window") if isinstance(flow, dict) else None
    if not isinstance(window, dict):
        return None
    bull, bear = _finite(window.get("bull")), _finite(window.get("bear"))
    if bull is None or bear is None:
        return None
    cap = scalp_config.prob_flow_cap()
    return max(-cap, min(cap, (bull - bear) * d)) / cap


def _sentiment(sentiment, d):
    state = sentiment.get("state") if isinstance(sentiment, dict) else None
    sense = _confirm._STATE_SENSE.get(state) if isinstance(state, str) else None
    return None if sense is None else sense * d


def source_factors(snapshot, symbol, d, win_points, entry_spot) -> dict:
    """The dealer read, net premium, flow and sentiment factors from a
    ``scalp_confirm.snapshot``: ``room`` (the room to the wall in the call's
    direction over the target's points, signed, capped at ``prob_room_cap``),
    ``breadth_room`` (the big ten's share with that room, minus a half),
    ``flip_side`` (+1 when the entry spot is on the call's side of the gamma flip),
    ``netprem`` (call minus put over their sum, times ``d``), ``flow`` (the
    window's bull minus bear times ``d``, capped, over the cap) and ``sentiment``.
    A source that is absent, stale-recorded as another day's, or untrusted is
    ``None``."""
    snap = snapshot if isinstance(snapshot, dict) else {}
    out = _dealer(_by_symbol(snap.get("matrix")), symbol, d, win_points, entry_spot)
    out["netprem"] = _netprem(_by_symbol(snap.get("netprem")), symbol, d)
    out["flow"] = _flow(snap.get("flow"), d)
    out["sentiment"] = _sentiment(snap.get("sentiment"), d)
    return {name: out[name] for name in _SOURCE_NAMES}


# --- the union --------------------------------------------------------------------

def factors(kind, symbol, slot, d, inputs, trend, snapshot, win_points, entry_spot) -> dict:
    """Every factor of one call, signed by ``d``. ``kind`` is ``"morning"`` (the
    08:36 call, which has no time-of-day block) or ``"intraday"`` (the
    15-minute calls, which add the ``block:midday`` and ``block:afternoon``
    indicators of ``slot``'s block; morning is the baseline, both 0)."""
    out = price_factors(inputs, d)
    out.update(trend_signed(trend, d))
    out.update(source_factors(snapshot, symbol, d, win_points, entry_spot))
    if kind != "morning":
        block = scalp_config.intraday_block(slot)
        out["block:midday"] = 1.0 if block == "midday" else 0.0
        out["block:afternoon"] = 1.0 if block == "afternoon" else 0.0
    return out


# --- scaling ----------------------------------------------------------------------

def _quantile(sorted_values, q) -> float:
    """The ``q`` quantile by linear interpolation between order statistics."""
    pos = (len(sorted_values) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def fit_scaling(rows) -> dict:
    """Per continuous factor, the interquartile range of its non-None values over
    the history's ``rows``: 1.0 when the range is 0 or there are fewer than four
    values. The discrete factors are not scaled and are left out."""
    values = {}
    for row in rows or ():
        for name, raw in (row or {}).items():
            if name in DISCRETE:
                continue
            bucket = values.setdefault(name, [])
            value = _finite(raw)
            if value is not None:
                bucket.append(value)
    out = {}
    for name, bucket in values.items():
        iqr = 0.0
        if len(bucket) >= 4:
            bucket.sort()
            iqr = _quantile(bucket, 0.75) - _quantile(bucket, 0.25)
        out[name] = iqr if iqr > 0 else 1.0
    return out


def scaled(f, scaling) -> dict:
    """A call's factors ready for the weights: a gap (``None``, or anything not a
    finite number) is 0.0; a discrete factor is as it is; any other is divided by
    its scale and clipped to ``+-prob_cap()`` and is NEVER centred; a factor with no
    scale is 0.0."""
    cap = scalp_config.prob_cap()
    out = {}
    for name, raw in (f if isinstance(f, dict) else {}).items():
        value = _finite(raw)
        if value is None:
            out[name] = 0.0
        elif name in DISCRETE:
            out[name] = float(value)
        elif name in scaling and scaling[name]:
            out[name] = max(-cap, min(cap, value / scaling[name]))
        else:
            out[name] = 0.0
    return out
