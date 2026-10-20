"""The intraday scalp call's confirmation reading: four live sources, shown and
recorded, NOT part of the probability.

Beside a call's tested probability the page shows what the market is saying
right now, from four views the service already publishes:

* **flow** - ``cache:options:flow_alerts``: the directional alerts in the last
  ``[confirm] flow_window_min`` minutes on the call's symbol and the big ten;
* **net premium** - ``cache:options:net_premium``: the symbol's latest call and
  put premium LEVEL (never a difference of two points: the stored premium is
  volume at the current mark and falls when marks fall);
* **the dealer read** (the operator's "Option C") - ``cache:options:matrix``: room
  from spot to the call wall (up) or the put wall (down) against the points the
  trade needs, and how many of the big ten have the same room;
* **sentiment** - ``cache:sentiment:composite`` ``derived.trend.state``, at half
  weight.

Each gives agree (+1), disagree (-1), neutral (0) or no reading (``None``)
RELATIVE to the call's direction; ``combine`` turns them into a weighted score
in [-1, 1] and one word. Nothing here feeds the probability: ``snapshot`` records
the raw values so the reading can be tested later.

Rules that keep it honest:

* **Absent is not neutral.** A source that is missing, older than
  ``[confirm] max_age_min``, or has no usable time is NO READING, and a wall
  that is ``None`` (Schwab sent no Greeks) is no reading for that symbol, never
  "no room". A source that IS there and says nothing is a real zero: no flow
  alerts in the window, a premium inside the margin, a neutral lean.
* **The alert's own estimate, never a guess.** The view's alerts carry no
  bought / sold figure; ``cache:options:flow_sides`` holds it per alert id.
  An ``uoa`` / ``big_delta`` alert is directional only when one side holds at
  least ``[confirm] sides_majority`` of its labelled volume; with no estimate
  (the view absent, from another day, or no entry) it is not directional, and
  the reading records which.
* **Pure functions take the payloads.** ``sources`` is the only bus reader;
  everything else reads what it returned, so a test needs no bus.
* **Never raises.** A malformed source degrades to absent for that component
  and is counted (``options.scalp_confirm``).

Design: docs/plans/2026-10-20-scalp-intraday-calls-design.md section 5.
"""
import datetime as _dt
from zoneinfo import ZoneInfo

from services import _degrade
from shared import scalp_config
from shared import symbols as _symbols
from shared.numeric import finite as _finite

_CT = ZoneInfo("America/Chicago")

MATRIX = "cache:options:matrix"
NETPREM = "cache:options:net_premium"
FLOW = "cache:options:flow_alerts"
FLOW_SIDES = "cache:options:flow_sides"
SENTIMENT = "cache:sentiment:composite"

#: How many alerts the flow view keeps (``handlers._FLOW_ALERTS_MAX``; a test
#: pins the two together, so this module need not import the handlers).
FLOW_CAP = 300

#: The view name each source's time is read from; flow and its estimate carry
#: none of their own (a quiet list is a reading, see ``sources``).
_TS_FIELD = {"matrix": "ts", "netprem": "ts", "sentiment": "composite_at"}
_KEYS = {"matrix": MATRIX, "netprem": NETPREM, "flow": FLOW,
         "sentiment": SENTIMENT, "flow_sides": FLOW_SIDES}
COMPONENTS = ("flow", "netprem", "dealer", "sentiment")

# The `side` each detector really writes (flow_alerts.py, hiro.py), and which
# way it leans. gamma_flip (to_positive / to_negative) is never directional.
_SIDE_SENSE = {
    ("crossover", "calls_over"): 1, ("crossover", "puts_over"): -1,
    ("hiro_surge", "dealers_buying"): 1, ("hiro_surge", "dealers_selling"): -1,
    ("hiro_flip", "to_buying"): 1, ("hiro_flip", "to_selling"): -1,
}
# uoa and big_delta write side call | put; the lean comes from what was bought.
_CONTRACT_TYPES = ("uoa", "big_delta")
_CONTRACT_SIDE = {"call": 1, "put": -1}

# The five regime keys are a contract (sentiment-dashboard market_state):
# bullish, lack_of_bullishness, neutral, lack_of_bearishness, bearish. Only the
# two named directions lean; the "lack of" states and neutral read as no reading.
_STATE_SENSE = {"bullish": 1, "bearish": -1, "neutral": None,
                "lack_of_bullishness": None, "lack_of_bearishness": None}

_EPS = 1e-9


# --- small readers ---------------------------------------------------------

def _norm(symbol) -> str:
    """``$SPX`` and ``SPX`` are one symbol; anything that is not text is none."""
    return symbol.strip().upper().lstrip("$") if isinstance(symbol, str) else ""


def _dir(direction):
    """+1, -1, or None for anything else (a bool is not a direction)."""
    if isinstance(direction, bool) or not isinstance(direction, (int, float)):
        return None
    return 1 if direction > 0 else -1 if direction < 0 else None


def _positive(value):
    v = _finite(value)
    return v if v is not None and v > 0 else None


def _now(now):
    if not isinstance(now, _dt.datetime):
        return _dt.datetime.now(_CT)
    return now if now.tzinfo else now.replace(tzinfo=_CT)


def _age_min(raw, now):
    """Minutes since an ISO timestamp, or None when it is not one. A naive time
    is Central (the repo's rule); a time a little ahead of the clock is zero."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        t = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=_CT)
    return max((now - t).total_seconds() / 60.0, 0.0)


def _source(src, name) -> dict:
    got = src.get(name) if isinstance(src, dict) else None
    if isinstance(got, dict) and got.get("state") in ("ok", "stale", "absent"):
        return got
    return {"payload": None, "age_min": None, "state": "absent"}


def _payload(src, name):
    """The source's payload when it is fresh enough to read, else None."""
    got = _source(src, name)
    return got["payload"] if got["state"] == "ok" and isinstance(got["payload"], dict) \
        else None


def _stale(src, name) -> bool:
    return _source(src, name)["state"] == "stale"


# --- the one bus reader ----------------------------------------------------

def sources(bus, now) -> dict:
    """Read the views once. Each entry is ``{"payload", "age_min", "state"}``:
    ``absent`` (no view), ``stale`` (older than ``confirm_max_age_min``, or with
    no usable time) or ``ok``. The flow view and its estimate carry no time of
    their own and are never stale: an empty or quiet alert list is a real
    reading of zero, and only a missing view is absent. ``now`` anchors the
    ages (naive is Central)."""
    now = _now(now)
    limit = scalp_config.confirm_max_age_min()
    out = {}
    for name, key in _KEYS.items():
        entry = {"payload": None, "age_min": None, "state": "absent"}
        try:
            env = bus.cache_get(key)
            payload = getattr(env, "payload", None) if env is not None else None
            if isinstance(payload, dict):
                entry["payload"] = payload
                if name in _TS_FIELD:
                    age = _age_min(payload.get(_TS_FIELD[name]), now)
                    entry["age_min"] = age
                    entry["state"] = "ok" if age is not None and age <= limit else "stale"
                else:
                    entry["state"] = "ok"
                    if name == "flow":
                        entry["age_min"] = _newest_age(payload, now)
        except Exception:
            _degrade.degraded("options.scalp_confirm", detail=f"read {key}")
            entry = {"payload": None, "age_min": None, "state": "absent"}
        out[name] = entry
    return out


def _newest_age(flow_payload, now):
    """Minutes since the newest alert (informational; None for an empty list)."""
    alerts = flow_payload.get("alerts")
    stamps = [_finite(a.get("ts")) for a in alerts if isinstance(a, dict)] \
        if isinstance(alerts, list) else []
    stamps = [t for t in stamps if t is not None]
    return max((now.timestamp() - max(stamps)) / 60.0, 0.0) if stamps else None


# --- flow -------------------------------------------------------------------

def _lean(estimate):
    """+1 when bought is at least ``sides_majority`` of the labelled (bought +
    sold) volume, -1 when sold is, None for anything closer, all unlabelled, or
    no usable tally. ``estimate`` is one entry of the
    flow_sides view's ``contracts`` map (``{"poll", "stream"}``) or a bare
    ``{"bought", "sold"}`` tally. The session's poll figure only: the poll and
    the stream are different samples and are never blended."""
    if not isinstance(estimate, dict):
        return None
    tally = estimate["poll"] if "poll" in estimate else estimate
    if not isinstance(tally, dict):
        return None
    bought, sold = _finite(tally.get("bought")), _finite(tally.get("sold"))
    if bought is None or sold is None or bought < 0 or sold < 0 or bought + sold <= 0:
        return None
    majority = scalp_config.sides_majority() - _EPS
    share = bought / (bought + sold)
    return 1 if share >= majority else -1 if 1 - share >= majority else None


def flow_sense(alert, estimate=None):
    """+1 bullish, -1 bearish, None not directional.

    ``uoa`` / ``big_delta``: a call bought or a put sold is bullish, a call sold
    or a put bought is bearish; with no bought / sold estimate the alert is not
    directional (its ``side`` is only call or put). ``crossover``: calls over
    puts bullish. ``hiro_surge`` / ``hiro_flip``: dealers buying (or turning to
    buying) bullish. ``gamma_flip`` is recorded, never directional."""
    if not isinstance(alert, dict):
        return None
    typ, side = alert.get("type"), alert.get("side")
    if typ in _CONTRACT_TYPES:
        cp, lean = _CONTRACT_SIDE.get(side), _lean(estimate)
        return None if cp is None or lean is None else cp * lean
    return _SIDE_SENSE.get((typ, side))


def _estimates(src, flow_payload):
    """``(contracts, state)``: the flow_sides view's ``{alert id: entry}`` map
    and why it is or is not usable (``ok``, ``absent``, ``other_day``, ``empty``).
    The service keeps yesterday's view on a quiet morning, so its date must be
    the flow view's. Without a map no uoa / big_delta alert is directional."""
    got = _source(src, "flow_sides")
    sides = got["payload"]
    if got["state"] == "absent" or not isinstance(sides, dict):
        return {}, "absent"
    if sides.get("date") != flow_payload.get("date"):
        return {}, "other_day"
    contracts = sides.get("contracts")
    if not isinstance(contracts, dict) or not contracts:
        return {}, "empty"
    return contracts, "ok"


def _flow(src, symbol, d, entry_ts):
    payload = _payload(src, "flow")
    alerts = payload.get("alerts") if payload else None
    if not isinstance(alerts, list):
        return {"verdict": None, "raw": {"state": "absent"}, "stale": False}
    if entry_ts is None:
        return {"verdict": None, "raw": {"state": "no_entry_ts"}, "stale": False}
    raw = _flow_scope(src, payload, symbol, entry_ts)
    verdict = None
    if d is not None:
        lean = raw["net"] * d
        verdict = (1 if lean > 0 and lean >= raw["margin"]
                   else -1 if lean < 0 and -lean >= raw["margin"] else 0)
    return {"verdict": verdict, "stale": False, "raw": raw}


def _flow_scope(src, payload, symbol, entry_ts):
    """The flow reading's raw counts: the directional alerts in
    ``[entry_ts - flow_window_min, entry_ts]`` on ``symbol`` and the big ten.
    ``components`` scores it and ``snapshot`` records it, so what is recorded is
    what the call saw."""
    alerts = payload["alerts"]
    contracts, sides_state = _estimates(src, payload)
    window = scalp_config.flow_window_min()
    start = entry_ts - window * 60
    me = _norm(symbol)
    members = {_norm(m) for m in _symbols.big10()}
    stamps = [t for t in (_finite(a.get("ts")) for a in alerts if isinstance(a, dict))
              if t is not None]
    by_type, bull, bear, n = {}, 0, 0, 0
    for a in alerts:
        ts = _finite(a.get("ts")) if isinstance(a, dict) else None
        sym = _norm(a.get("symbol")) if isinstance(a, dict) else ""
        if ts is None or not (start <= ts <= entry_ts) or not (sym == me or sym in members):
            continue
        sense = flow_sense(a, contracts.get(a.get("id")))
        row = by_type.setdefault(str(a.get("type")), {"n": 0, "bull": 0, "bear": 0})
        row["n"] += 1
        n += 1
        if sense == 1:
            row["bull"] += 1
            bull += 1
        elif sense == -1:
            row["bear"] += 1
            bear += 1
    # The view keeps only its newest FLOW_CAP alerts: a full list whose oldest
    # alert is inside the window may have lost older ones. A short list that
    # starts late is just a quiet morning.
    truncated = len(alerts) >= FLOW_CAP and bool(stamps) and min(stamps) > start
    return {"state": "ok", "window_min": window, "n_alerts": n, "bull": bull,
            "bear": bear, "net": bull - bear, "margin": scalp_config.flow_margin(),
            "by_type": by_type, "truncated": truncated, "sides_view": sides_state}


# --- net premium -------------------------------------------------------------

def _latest_level(series, symbol, at=None):
    """``(ts, call, put)`` of the symbol's newest usable point, or None. With
    ``at`` (epoch seconds) only a point at or before it counts: a call is read
    as of its own entry, never from a later point."""
    if not isinstance(series, dict):
        return None
    me = _norm(symbol)
    best = None
    for key, points in series.items():
        if _norm(key) != me or not isinstance(points, list):
            continue
        for p in points:
            if not isinstance(p, (list, tuple)) or len(p) < 3:
                continue
            ts, call, put = _finite(p[0]), _finite(p[1]), _finite(p[2])
            if ts is None or call is None or put is None or call < 0 or put < 0:
                continue
            if at is not None and ts > at:
                continue
            if best is None or ts > best[0]:
                best = (ts, call, put)
        break
    return best


def _netprem(src, symbol, d, entry_ts=None):
    if _stale(src, "netprem"):
        return {"verdict": None, "raw": {"state": "stale"}, "stale": True}
    payload = _payload(src, "netprem")
    if payload is None:
        return {"verdict": None, "raw": {"state": "absent"}, "stale": False}
    level = _latest_level(payload.get("series"), symbol, entry_ts)
    if level is None or level[1] + level[2] <= 0:
        return {"verdict": None, "raw": {"state": "no_reading"}, "stale": False}
    ts, call, put = level
    if entry_ts is not None and entry_ts - ts > scalp_config.confirm_max_age_min() * 60:
        # The view is fresh, but this symbol's newest point at entry is not.
        return {"verdict": None, "stale": True,
                "raw": {"state": "stale_point", "ts": int(ts), "call": call, "put": put}}
    frac = (call - put) / (call + put)
    margin = scalp_config.netprem_margin()
    verdict = None
    if d is not None:
        lean = frac * d
        verdict = 1 if lean > margin else -1 if lean < -margin else 0
    return {"verdict": verdict, "stale": False,
            "raw": {"state": "ok", "ts": int(ts), "call": call, "put": put,
                    "frac": frac, "margin": margin}}


# --- the dealer read (Option C) ----------------------------------------------

def _rows(src):
    """The matrix rows by normalised symbol (the first of a symbol wins)."""
    payload = _payload(src, "matrix")
    rows = payload.get("rows") if payload else None
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict):
            out.setdefault(_norm(r.get("symbol")), r)
    return out


def _wall_view(row) -> dict:
    """One row's spot, walls, flip and net GEX, as recorded; a figure that is not
    a usable number is None (a zero wall is a missing wall, never a wall at
    zero).

    ``walls_untrusted`` is True when ``net_gex`` is None, zero or not a number.
    That is what a chain with no Greeks does: Schwab's -999 gamma is refused,
    every GEX cell is 0.0, and the wall picker still returns the first strikes it
    finds, so the row carries walls that mean nothing. They stay in the view (the
    record keeps what happened) but ``_room`` and ``wall_context`` never use
    them."""
    row = row if isinstance(row, dict) else {}
    net_gex = _finite(row.get("net_gex"))
    return {"spot": _positive(row.get("spot")),
            "call_wall": _positive(row.get("call_wall")),
            "put_wall": _positive(row.get("put_wall")),
            "flip": _positive(row.get("flip")), "net_gex": net_gex,
            "walls_untrusted": net_gex is None or net_gex == 0}


def _trusted_wall(view, kind):
    """The ``call`` or ``put`` wall, or None when it is missing or untrusted."""
    return None if view["walls_untrusted"] else view[f"{kind}_wall"]


def _room(view, d):
    """``(wall, room)``: the wall in the trade's direction and the distance from
    spot to it (negative when spot is already beyond it); ``(None, None)`` for
    a missing or untrusted wall or a missing spot."""
    wall = _trusted_wall(view, "call" if d > 0 else "put")
    spot = view["spot"]
    if wall is None or spot is None:
        return None, None
    return wall, (wall - spot) * d


def _breadth(rows, d, win_pct):
    """How many of the big ten have the move's room, in percent of each name's own
    spot. A name with a missing or untrusted wall is not counted, and the share
    is read only when ``[confirm] breadth_min_members`` names are: a share of two
    names is not a breadth."""
    members = _symbols.big10()
    read = with_room = 0
    for m in members:
        view = _wall_view(rows.get(_norm(m)))
        wall, room = _room(view, d)
        if wall is None or win_pct is None:
            continue
        read += 1
        if room / view["spot"] * 100.0 >= win_pct:
            with_room += 1
    out = {"with_room": with_room, "of": read,
           "share": with_room / read if read else None, "verdict": None}
    if read and read >= scalp_config.breadth_min_members():
        agree = scalp_config.breadth_agree()
        share = out["share"]
        out["verdict"] = (1 if share >= agree - _EPS
                          else -1 if share <= 1 - agree + _EPS else 0)
    return out


def _dealer(src, symbol, d, win_points):
    """``(component, line)``. The verdict is the symbol's own room when the
    breadth of room over the big ten agrees with it or has no reading; 0
    (present, neutral) when room and breadth disagree; and the breadth's own
    verdict when the symbol has no wall. The gamma flip is recorded and never
    scored."""
    if _stale(src, "matrix"):
        return ({"verdict": None, "raw": {"state": "stale"}, "stale": True},
                "No reading: the dealer data is stale")
    rows = _rows(src)
    if _payload(src, "matrix") is None:
        return ({"verdict": None, "raw": {"state": "absent"}, "stale": False},
                "No reading: no dealer data")
    view = _wall_view(rows.get(_norm(symbol)))
    spot, flip = view["spot"], view["flip"]
    raw = {"state": "ok", **view,
           "above_flip": None if spot is None or flip is None else spot > flip}
    if d is None:
        return {"verdict": None, "raw": raw, "stale": False}, None
    kind = "call" if d > 0 else "put"
    win = _positive(win_points)
    win_pct = win / spot * 100.0 if win is not None and spot is not None else None
    breadth = _breadth(rows, d, win_pct)
    wall, room = _room(view, d)
    raw.update({"wall_kind": kind, "wall": wall, "room": room, "win_points": win,
                "room_pct": None if room is None else room / spot * 100.0,
                "breadth": breadth})
    own = None if wall is None or win is None else (1 if room >= win else -1)
    verdict = _dealer_verdict(own, breadth["verdict"])
    raw["own_verdict"] = own
    return {"verdict": verdict, "raw": raw, "stale": False}, _dealer_line(
        symbol, d, kind, wall, room, spot, win, breadth, own, verdict,
        view["walls_untrusted"])


def _dealer_verdict(own, breadth):
    """The symbol's room (``own``) and the breadth, each +1 / -1 / 0 / None.
    Breadth decides alone when the symbol has no reading; opposite signs cancel
    to 0; a neutral or missing breadth leaves the symbol's room standing."""
    if own is None:
        return breadth
    return 0 if breadth is not None and breadth == -own else own


def _dealer_line(symbol, d, kind, wall, room, spot, win, breadth, own, verdict,
                 untrusted=False):
    if win is None:
        head = "No reading: no target to measure room against"
    elif wall is None and untrusted:
        head = f"No usable {kind} wall for {symbol} (no gamma reading)"
    elif wall is None:
        head = f"No {kind} wall for {symbol}"
    elif room < 0:
        head = (f"Spot {spot:,.2f} is {'above' if d > 0 else 'below'} the {kind} "
                f"wall at {wall:,.2f}")
    elif room >= win:
        head = (f"Room {'up' if d > 0 else 'down'} to the {kind} wall at "
                f"{wall:,.2f} ({room / spot * 100:.2f}%)")
    else:
        head = (f"The {kind} wall at {wall:,.2f} ({room / spot * 100:.2f}%) "
                "is inside the target")
    if breadth["verdict"] is None:
        return f"{head}; breadth not read"
    line = f"{head}; {breadth['with_room']} of {breadth['of']} big names have room"
    if own is None and verdict is not None:
        return f"{line} (the big names alone count)"
    if own is not None and verdict == 0:
        return f"{line} (room and breadth disagree)"
    return line


# --- sentiment ---------------------------------------------------------------

def _sentiment(src, d):
    if _stale(src, "sentiment"):
        return {"verdict": None, "raw": {"state": None, "source": "stale"}, "stale": True}
    payload = _payload(src, "sentiment")
    if payload is None:
        return {"verdict": None, "raw": {"state": None, "source": "absent"}, "stale": False}
    derived = payload.get("derived")
    trend = derived.get("trend") if isinstance(derived, dict) else None
    trend = trend if isinstance(trend, dict) else {}
    state = trend.get("state") if isinstance(trend.get("state"), str) else None
    sense = _STATE_SENSE.get(state)
    verdict = None if sense is None or d is None else sense * d
    return {"verdict": verdict, "stale": False,
            "raw": {"state": state, "score": _finite(trend.get("score")), "source": "ok"}}


# --- the reading ------------------------------------------------------------

def combine(verdicts):
    """``(score, word)`` from ``{component: +1 | -1 | 0 | None}``: the weighted
    mean over the components that gave a reading (a 0 counts, as present and
    neutral; ``None`` does not), and ``Confirms`` / ``Mixed`` / ``Contradicts``
    from the configured cuts. ``(None, None)`` when nothing gave a reading."""
    weights = scalp_config.confirm_weights()
    total = weight = 0.0
    for name, v in (verdicts or {}).items():
        if v is None or name not in weights:
            continue
        total += weights[name] * v
        weight += weights[name]
    if weight <= 0:
        return None, None
    score = total / weight
    lo, hi = scalp_config.confirm_cuts()
    word = "Confirms" if score >= hi else "Contradicts" if score <= lo else "Mixed"
    return score, word


def _absent(name):
    return {"verdict": None, "raw": {"state": "error", "component": name}, "stale": False}


def _guarded(name, fn, *args):
    try:
        return fn(*args)
    except Exception:
        _degrade.degraded("options.scalp_confirm", detail=name)
        return None


def components(src, symbol, direction, win_points, entry_ts):
    """The four components for one call, the combined score and word, the dealer
    line and whether the flow window was cut short.

    ``direction`` is +1 / -1 (None: every verdict and the score are None, but the
    raw values are still filled, so a call with no direction is still recorded).
    ``win_points`` is the long-with-the-move role's points to target, a positive
    magnitude in the underlying's points. ``entry_ts`` is epoch seconds."""
    d = _dir(direction)
    ts = _finite(entry_ts)
    flow = _guarded("flow", _flow, src, symbol, d, ts)
    netprem = _guarded("netprem", _netprem, src, symbol, d, ts)
    dealer = _guarded("dealer", _dealer, src, symbol, d, win_points)
    sentiment = _guarded("sentiment", _sentiment, src, d)
    out = {"flow": flow or _absent("flow"), "netprem": netprem or _absent("netprem"),
           "sentiment": sentiment or _absent("sentiment")}
    out["dealer"], line = dealer or (_absent("dealer"), None)
    score, word = combine({k: out[k]["verdict"] for k in COMPONENTS})
    out.update({"score": score, "word": word, "dealer_line": line,
                "truncated": bool(out["flow"]["raw"].get("truncated"))})
    return out


# --- walls in the path -------------------------------------------------------

def wall_context(src, symbol, spot, target_spot, stop_spot) -> list:
    """Words for any wall between entry and the target or the stop, target
    first: ``"Target is beyond the call wall at 771.00"``. A wall exactly at the
    target is not beyond it. Empty for a missing or untrusted wall (no gamma
    reading), a stale matrix or a price that is not usable."""
    try:
        view = _wall_view(_rows(src).get(_norm(symbol)))
        spot = _positive(spot)
        if spot is None:
            return []
        out = []
        for label, end in (("Target", _positive(target_spot)), ("Stop", _positive(stop_spot))):
            if end is None:
                continue
            lo, hi = min(spot, end), max(spot, end)
            for kind in ("call", "put"):
                wall = _trusted_wall(view, kind)
                if wall is not None and lo < wall < hi:
                    out.append(f"{label} is beyond the {kind} wall at {wall:,.2f}")
        return out
    except Exception:
        _degrade.degraded("options.scalp_confirm", detail="wall_context")
        return []


# --- what to record ----------------------------------------------------------

def _tally(alerts, contracts):
    by_type = {}
    for a in alerts:
        if not isinstance(a, dict):
            continue
        sense = flow_sense(a, contracts.get(a.get("id")))
        row = by_type.setdefault(str(a.get("type")), {"n": 0, "bull": 0, "bear": 0})
        row["n"] += 1
        row["bull"] += sense == 1
        row["bear"] += sense == -1
    return by_type


def snapshot(src, symbols, entry_ts=None, symbol=None) -> dict:
    """The raw values to record beside a call: each requested symbol's and each
    big-ten member's spot, walls, flip, net GEX and ``walls_untrusted``; the
    symbols' net premium levels as of ``entry_ts`` and the big-ten basket's;
    the sentiment state and score; and the flow counts twice: the whole view
    (``flow.by_type``) and, when ``symbol`` and ``entry_ts`` are given, the
    windowed, scoped reading the call itself saw (``flow.window``, the same
    scope ``components`` uses). A source that is stale or absent contributes no
    values (its state is recorded), so a stale figure is never kept as a fact.
    ``None`` marks a symbol asked for that had no row."""
    out = {"sources": {name: {"state": _source(src, name)["state"],
                              "age_min": _source(src, name)["age_min"]}
                       for name in _KEYS},
           "matrix": {}, "netprem": {}, "sentiment": None, "flow": None}
    try:
        names = list(dict.fromkeys(list(symbols or ()) + list(_symbols.big10())))
        rows = _rows(src)
        if _payload(src, "matrix") is not None:
            for name in names:
                row = rows.get(_norm(name))
                view = _wall_view(row) if row is not None else None
                out["matrix"][name] = None if view is None else {
                    k: view[k] for k in ("spot", "call_wall", "put_wall", "flip", "net_gex",
                                         "walls_untrusted")}
        np = _payload(src, "netprem")
        if np is not None:
            for name in list(dict.fromkeys(list(symbols or ()) + ["BIG10"])):
                level = _latest_level(np.get("series"), name, _finite(entry_ts))
                out["netprem"][name] = None if level is None else {
                    "call": level[1], "put": level[2], "ts": int(level[0])}
        sent = _sentiment(src, 1)
        if sent["raw"].get("source") == "ok":
            out["sentiment"] = {"state": sent["raw"]["state"], "score": sent["raw"]["score"]}
        flow = _payload(src, "flow")
        if flow is not None and isinstance(flow.get("alerts"), list):
            contracts, sides_state = _estimates(src, flow)
            out["flow"] = {"date": flow.get("date"), "n": len(flow["alerts"]),
                           "sides_view": sides_state,
                           "by_type": _tally(flow["alerts"], contracts),
                           "window": None}
            at = _finite(entry_ts)
            if symbol is not None and at is not None:
                out["flow"]["window"] = {"symbol": symbol, "entry_ts": int(at),
                                         **_flow_scope(src, flow, symbol, at)}
    except Exception:
        _degrade.degraded("options.scalp_confirm", detail="snapshot")
    return out
