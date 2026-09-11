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


# Below this, a dollar figure keeps its cents: a one-lot's theta of $0.38 printed
# as "+$0" reads as a measured zero (found in the browser, 2026-09-11).
_CENTS_BELOW = 10.0


def _money(v, signed=False):
    """``$1,234`` — whole dollars for a position figure, cents under $10."""
    v = num(v)
    if v is None:
        return NO_READING
    txt = f"${abs(v):,.2f}" if 0 < abs(v) < _CENTS_BELOW else f"${abs(v):,.0f}"
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
    # No result is simply no baseline: payoff_facts then says whether the legs are
    # incomplete or merely unpriced, which are different things to tell a reader.
    facts = payoff_facts(legs, result.get("whatif_baseline"))
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


# -- Task 3: slider readouts and the Days range ----------------------------------

# A tz-naive "now" in this project is CENTRAL wall-clock time (CLAUDE.md, the
# 2026-08-20 time-basis fix). Read it as such, or every 0-DTE figure is an hour off.
_NAIVE_TZ = ZoneInfo("America/Chicago")
_DEFAULT_DAYS_MAX = 30
_FINE_STEP_BELOW_DAYS = 3        # a position this close to expiry steps in quarter days


def _aware(now):
    now = now or _dt.datetime.now(_NAIVE_TZ)
    return now if now.tzinfo else now.replace(tzinfo=_NAIVE_TZ)


def fractional_dte(expiry, now=None):
    """Days from ``now`` to 16:00 America/New_York on ``expiry``, floored at 0.

    The service's own settlement convention (``_leg_days_to_expiry``), so the
    slider's Expiry snap lands where the service prices the legs to intrinsic.
    ``None`` for an unparseable expiry."""
    if not expiry:
        return None
    try:
        d = _dt.date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return None
    settle = _dt.datetime(d.year, d.month, d.day, _SETTLE_HOUR, tzinfo=_SETTLE_TZ)
    days = (settle - _aware(now)).total_seconds() / 86400.0
    return max(days, 0.0)


def _ceil_to(x, step):
    return round(math.ceil(x / step - 1e-9) * step, 2)


def days_range(legs, now=None):
    """The Days-passed slider fitted to the position: ``{max, step, snaps}``.

    * ``max`` — the LONGEST leg's time to its close, rounded up to the step, so
      the slider can reach the last expiry and never runs far past it.
    * ``step`` — one day, or a quarter day when the longest leg is within three
      days (a 0-DTE position has hours, not days, to play with).
    * ``snaps`` — Now · Halfway · Expiry, the last named **First expiry** when
      the legs settle on different dates; a snap that would duplicate another
      (or land at zero) is dropped.

    No leg with a usable expiry falls back to the old fixed month."""
    dtes = [d for d in (fractional_dte(l.get("expiry"), now) for l in legs or [])
            if d is not None]
    if not dtes:
        return {"max": _DEFAULT_DAYS_MAX, "step": 1, "snaps": [("Now", 0.0)]}
    longest, first = max(dtes), min(dtes)
    step = 0.25 if longest <= _FINE_STEP_BELOW_DAYS else 1
    top = max(_ceil_to(longest, step), step)
    expiries = {str(l.get("expiry")) for l in legs or [] if l.get("expiry")}
    snaps = [("Now", 0.0)]
    half = _ceil_to(first / 2, step)
    end = min(_ceil_to(first, step), top)
    if 0 < half < end:
        snaps.append(("Halfway", half))
    if end > 0:
        snaps.append(("Expiry" if len(expiries) <= 1 else "First expiry", end))
    return {"max": top, "step": step, "snaps": snaps}


def days_text(days):
    """``"5 days"`` / ``"1 day"`` / ``"6 hours"`` — hours under one day, because a
    0-DTE slider in fractions of a day reads as noise."""
    d = num(days) or 0.0
    if 0 < d < 1:
        hours = round(d * 24)
        return f"{hours} hour{'' if hours == 1 else 's'}"
    if d == 1:
        return "1 day"
    return f"{d:g} days"


def curve_pnl_at(pairs, x):
    """The What-if curve's P/L at price ``x`` by linear interpolation between the
    sweep points the chart draws — so the readout and the chart cannot disagree.
    ``None`` outside the sweep (it spans spot ±20%) or with no curve."""
    x = num(x)
    pts = sorted((p for p in pairs or [] if num(p[0]) is not None and num(p[1]) is not None),
                 key=lambda p: p[0])
    if x is None or not pts or x < pts[0][0] or x > pts[-1][0]:
        return None
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _when_text(days, now, legs=None):
    """When the Days slider lands. Past the LAST leg's close it names expiration
    rather than a date: the slider steps in whole days, so its Expiry snap rounds
    7.2 days up to 8, and "on Sep 19" for a spread that expires Sep 18 would be a
    date that never happens for the position (the service prices it at intrinsic
    there, so the figure beside it is still right)."""
    d = num(days) or 0.0
    if d == 0:
        return "today"
    dtes = [x for x in (fractional_dte(l.get("expiry"), now) for l in legs or [])
            if x is not None]
    if dtes and d >= max(dtes):
        mixed = len({str(l.get("expiry")) for l in legs or [] if l.get("expiry")}) > 1
        return "after the last expiration" if mixed else "at expiration"
    if d < 1:
        return f"in {days_text(d)}"
    when = _aware(now) + _dt.timedelta(days=d)
    return f"on {when:%b} {when.day}"


def whatif_readout(pairs, target_s, days, now=None, legs=None):
    """``("At 386.00 on Sep 28: profit $8,240", "pos")`` — the price the Price
    slider lands on, the date the Days slider lands on, and what the position is
    worth there. With ``legs``, time at or past the last close reads as
    expiration (see ``_when_text``). ``("", "neutral")`` when there is no curve."""
    target_s = num(target_s)
    if not pairs or target_s is None:
        return "", "neutral"
    head = f"At {target_s:,.2f} {_when_text(days, now, legs)}"
    pnl = curve_pnl_at(pairs, target_s)
    if pnl is None:
        return f"{head}: {NO_READING}", "neutral"
    if abs(pnl) < 0.5:
        return f"{head}: break-even", "neutral"
    word, tone = ("profit", "pos") if pnl > 0 else ("loss", "neg")
    return f"{head}: {word} {_money(abs(pnl))}", tone


# -- Task 4: structure checks and copy -------------------------------------------

def _date_text(expiry):
    """``"2026-09-09"`` -> ``"Sep 9"``; the raw string when it will not parse."""
    try:
        d = _dt.date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return str(expiry)
    return f"{d:%b} {d.day}"


def structure_warnings(legs):
    """Plain-sentence warnings for a position whose risk is not what its shape
    suggests. Two conditions, each silent on an ordinary structure:

    * a SHORT leg expiring after a LONG leg — once the long leg settles the short
      one is uncovered (the Calculator's ``_short_outlives_long`` condition);
    * net SHORT calls — loss grows without bound above the last strike.
    """
    legs = list(legs or [])
    out = []
    shorts = [(i, l) for i, l in enumerate(legs) if l.get("side") == "short" and l.get("expiry")]
    longs = [(i, l) for i, l in enumerate(legs) if l.get("side") != "short" and l.get("expiry")]
    if shorts and longs:
        si, s = max(shorts, key=lambda p: str(p[1]["expiry"]))
        li, lg = min(longs, key=lambda p: str(p[1]["expiry"]))
        if str(s["expiry"]) > str(lg["expiry"]):
            out.append(
                f"Leg {si + 1:02d} is short and expires {_date_text(s['expiry'])}, "
                f"after leg {li + 1:02d} (long, {_date_text(lg['expiry'])}). "
                f"From {_date_text(lg['expiry'])} it is no longer covered.")
    net_calls = 0.0
    for leg in legs:
        if leg.get("option_type") == "call":
            qty = num(leg.get("qty", 1)) or 0.0
            net_calls += -qty if leg.get("side") == "short" else qty
    if net_calls < 0:
        out.append("More calls sold than bought, so losses are unlimited if the price rises.")
    return out


def matches_template(code, legs):
    """Whether ``legs`` still have the SHAPE of strategy ``code``.

    Shape is the (type, side) multiset with quantities in the template's RATIO —
    ten contracts of a credit spread is still a credit spread — plus the
    template's expiry pattern: every "near" leg on one date, every "far" leg on
    one date no earlier than that. Strikes never count (the
    ``strategies.summary_code`` rule). An unknown code claims nothing: ``True``."""
    from .strategies import STRATEGY_TEMPLATES
    specs = STRATEGY_TEMPLATES.get(code) if code else None
    if not specs:
        return True
    legs = list(legs or [])
    if len(legs) != len(specs):
        return False
    qtys = [num(l.get("qty", 1)) for l in legs]
    if any(q is None or q <= 0 for q in qtys):
        return False
    # the scale: one leg's qty over its spec's; every leg must share it
    unmatched = list(range(len(specs)))
    scale = None
    near, far = set(), set()
    for leg, q in zip(legs, qtys):
        hit = None
        for j in unmatched:
            spec = specs[j]
            if spec["option_type"] != leg.get("option_type") or spec["side"] != leg.get("side"):
                continue
            ratio = q / spec["qty"]
            if scale is None or math.isclose(ratio, scale):
                hit = j
                scale = ratio if scale is None else scale
                break
        if hit is None:
            return False
        unmatched.remove(hit)
        (far if specs[hit]["expiry_role"] == "far" else near).add(str(leg.get("expiry")))
    if len(near) > 1 or len(far) > 1:
        return False
    if near and far and next(iter(far)) < next(iter(near)):
        return False
    return True


def empty_state_text(meta, legs):
    """The ONE sentence the What-if and Replay panels show when they have nothing
    to draw — what is true, not which service is cold (the 2026-09-04 copy rule):

    no chain loaded · a leg without a strike · a strike the loaded chain does not
    list for that leg's expiry · otherwise the price is on its way."""
    if not meta:
        return "Load a symbol to see this chart."
    strikes = meta.get("strikes") or {}
    for i, leg in enumerate(legs or []):
        strike = num(leg.get("strike"))
        if strike is None:
            return f"Pick a strike for leg {i + 1:02d}."
        listed = (strikes.get(str(leg.get("expiry"))) or {}).get(leg.get("option_type")) or []
        if not any(num(k) is not None and math.isclose(num(k), strike) for k in listed):
            return (f"Leg {i + 1:02d}: the {strike:g} {leg.get('option_type')} is not listed "
                    f"for {_date_text(leg.get('expiry'))}. "
                    f"Pick another strike or reload the chain.")
    return "Pricing this position…"


# -- Task 5: the IV-shock table ---------------------------------------------------

# (label, column, formatter kind, does a RISE help the position?) — only the value
# and theta rows carry a verdict; a change in delta, gamma or vega is exposure,
# not profit, so tinting it green or red would state an opinion the page lacks.
_SHOCK_ROWS = (
    ("Position value", "theo_price", "money", True),
    ("Delta", "delta", "int", None),
    ("Gamma", "gamma", "dec", None),
    ("Theta per day", "theta", "money", True),
    ("Vega per volatility point", "vega", "money", None),
)
SHOCK_ROW_LABELS = tuple(r[0] for r in _SHOCK_ROWS)


def _fmt(kind, v):
    if v is None:
        return NO_READING
    if kind == "money":
        return _money(v, signed=True) if v else "$0"
    if kind == "int":
        return f"{v:+,.0f}"
    return f"{v:+,.2f}"


def _vol_move_text(mult):
    if math.isclose(mult, 2.0):
        return "doubles"
    if math.isclose(mult, 0.5):
        return "halves"
    pct = abs(mult - 1.0) * 100.0
    return f"{'rises' if mult > 1 else 'falls'} {pct:.0f}%"


def ivshock_table(ivshock, mult):
    """The IV-shock view as a table plus one headline sentence.

    The old view was a column chart with dollar values and Greeks on one axis,
    so four of its five categories drew as flat lines. A table states every row
    at its own scale. ``rows`` are ``{label, base, shock, change, tone}`` strings;
    ``headline`` says what the volatility move does to the position's value."""
    if not ivshock or not ivshock.get("base") or not ivshock.get("shock"):
        return {"headline": "", "tone": "neutral", "rows": []}
    units = ivshock.get("units")
    base = position_units(ivshock["base"], units)
    shock = position_units(ivshock["shock"], units)
    rows = []
    for label, col, kind, rise_helps in _SHOCK_ROWS:
        b, s = num(base.get(col)), num(shock.get(col))
        change = s - b if (b is not None and s is not None) else None
        tone = "neutral"
        if rise_helps and change is not None and abs(change) >= 0.5:
            tone = "pos" if change > 0 else "neg"
        rows.append({"label": label, "base": _fmt(kind, b), "shock": _fmt(kind, s),
                     "change": _fmt(kind, change), "tone": tone})

    m = num(mult) or 1.0
    b, s = num(base.get("theo_price")), num(shock.get("theo_price"))
    if math.isclose(m, 1.0):
        return {"headline": "Move the slider to see what a change in volatility does.",
                "tone": "neutral", "rows": rows}
    if b is None or s is None:
        return {"headline": "", "tone": "neutral", "rows": rows}
    delta = s - b
    head = f"If volatility {_vol_move_text(m)}, this position"
    if abs(delta) < 0.5:
        return {"headline": f"{head} barely changes.", "tone": "neutral", "rows": rows}
    word, tone = ("gains", "pos") if delta > 0 else ("loses", "neg")
    return {"headline": f"{head} {word} {_money(abs(delta))}.", "tone": tone, "rows": rows}


# -- Task 7: replay axis + cursor ------------------------------------------------

_MAX_REPLAY_TICKS = 10
_DAILY_GAP_HOURS = 20


def replay_in_position_units(trace):
    """``trace`` with its Greek series in position units (see ``position_units``).
    A trace already marked ``units: "position"`` is returned unchanged."""
    trace = dict(trace or {})
    if trace.get("units") == POSITION_UNITS:
        return trace
    greeks = {}
    for g, series in (trace.get("greeks") or {}).items():
        greeks[g] = [(v * SHARES_PER_CONTRACT if num(v) is not None else v)
                     for v in series or []]
    trace["greeks"] = greeks
    return trace


def _parse_ts(ts):
    try:
        return _dt.datetime.fromisoformat(str(ts))
    except ValueError:
        return None


def replay_categories(timestamps):
    """One label per bar, used as the x-axis categories so the tooltip header and
    the tick labels show real time, not a bar number. Dates alone for daily bars
    (a time of day is noise there), date and time for intraday bars. An
    unparseable stamp is passed through as-is rather than dropped, which would
    shift every later label onto the wrong bar."""
    stamps = list(timestamps or [])
    parsed = [_parse_ts(t) for t in stamps]
    good = [p for p in parsed if p is not None]
    gaps = sorted((b - a).total_seconds() for a, b in zip(good, good[1:]))
    daily = bool(gaps) and gaps[len(gaps) // 2] >= _DAILY_GAP_HOURS * 3600
    out = []
    for raw, p in zip(stamps, parsed):
        if p is None:
            out.append(str(raw))
        elif daily:
            out.append(f"{p:%b} {p.day}")
        else:
            out.append(f"{p:%b} {p.day} {p:%H:%M}")
    return out


def replay_tick_positions(trace):
    """Where the x-axis labels sit: each session's first bar when the window spans
    several sessions (thinned to at most ten), else the service's own evenly
    spaced ``ticks``, else eight evenly spaced bars."""
    trace = trace or {}
    sessions = trace.get("sessions") or []
    n = len(trace.get("x") or [])
    if len(sessions) > 1:
        starts = [int(s.get("start", 0)) for s in sessions]
        stride = max(1, math.ceil(len(starts) / _MAX_REPLAY_TICKS))
        return starts[::stride]
    pos = (trace.get("ticks") or {}).get("pos")
    if pos:
        return [int(p) for p in pos]
    if n == 0:
        return []
    k = min(8, n)
    return sorted({round(i * (n - 1) / max(k - 1, 1)) for i in range(k)})


def replay_cursor_text(trace, i):
    """What the scrubbed bar says: ``"Aug 24 13:45 — price 356.20, profit $1,230,
    delta +145"``. Parts with no reading are left out rather than zeroed."""
    trace = replay_in_position_units(trace)
    stamps = trace.get("timestamps") or []
    if not isinstance(i, int) or not 0 <= i < len(stamps):
        return ""
    parts = []
    prices = trace.get("prices") or []
    price = num(prices[i]) if i < len(prices) else None
    if price is not None:
        parts.append(f"price {price:,.2f}")
    pnl_series = trace.get("pnl") or []
    pnl = num(pnl_series[i]) if i < len(pnl_series) else None
    if pnl is not None:
        if abs(pnl) < 0.5:
            parts.append("break-even")
        else:
            parts.append(f"{'profit' if pnl > 0 else 'loss'} {_money(abs(pnl))}")
    deltas = (trace.get("greeks") or {}).get("delta") or []
    delta = num(deltas[i]) if i < len(deltas) else None
    if delta is not None:
        parts.append(f"delta {delta:+,.0f}")
    head = replay_categories([stamps[i]])[0]
    return f"{head} — {', '.join(parts)}" if parts else head


# -- Task 8: does a result belong to the legs on screen? ---------------------------

def _leg_key(leg):
    return (str(leg.get("kind") or leg.get("option_type")), num(leg.get("strike")),
            str(leg.get("expiry")), str(leg.get("side")), num(leg.get("qty", 1)))


def result_matches(result, symbol, legs_payload):
    """Whether ``result`` was priced for ``symbol`` and exactly these legs.

    ``sim_run`` echoes both since 2026-09-11. A result WITHOUT the echo (a cache
    from before the upgrade) is trusted, as the page always did — refusing it
    would blank every tile on the first visit after a deploy."""
    if not result:
        return False
    if "legs" not in result:
        return True
    if str(result.get("symbol") or "").upper() != str(symbol or "").upper():
        return False
    return sorted(map(_leg_key, result.get("legs") or []), key=repr) == \
        sorted(map(_leg_key, legs_payload or []), key=repr)
