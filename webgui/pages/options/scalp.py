"""Opening Scalp (``/options/scalp``): the morning's measured call, read calmly.

Every trading morning at 08:36 CT the options service publishes
``cache:options:scalp`` - for $SPX, SPY and QQQ, the premarket inputs and, for
four same-day (0DTE) scalps entered after the first five minutes, the rate at
which each reached +50% before -50% inside 60 minutes over every stored morning
(whether a bucket beats its usual rate is the walk-forward-tested part). The
operator reads it and decides; the page has no trade control.

The study almost never finds a measured edge, so the page's COMMON message is
"No measured edge", and it is drawn as a plain, ordinary answer - not a warning
and not an empty state. The rules that keep it truthful:

* The headline number on every row is the trade's USUAL win rate (its base rate
  over all mornings). The bucket's own rate and interval sit beside it, emphasised
  only on the single row the service marked ``highlight``. A non-highlighted row
  is never styled as a recommendation.
* ``avoid`` names a role measured to LOSE more often than usual. It gets its own
  caution marker, which is neither the edge colour nor a recommendation.
* The four trades are named by that morning's contract: an up morning is
  Long call / Long put / Put credit spread / Call credit spread, a down morning
  the mirror, and ``with`` / ``against`` says which side of the opening move each
  one is on.
* ``waiting`` / ``closed`` / ``error`` keep the last call on screen, so every card
  says WHICH day's call it is; yesterday's is never read as today's.
* An absent input is "Absent", never a disagreement. The headlines and flow alerts
  are third-party text shown beside the call as NOT in the probability; they reach
  the page only through ``ui.label`` / ``ui.link`` (which escape), a link is
  followed only when it is a plain http(s) address, and the page draws no raw
  HTML at all.

The page leads with a glance: one table row per symbol (the latest call's
direction, the trade with the call, its fitted chance of target beside its usual
rate, the room to the wall and the 08:36 direction). Everything else sits below it
in closed sections: one per symbol (its 15-minute call, what moved its chance and
its 08:36 call), then the day's calls, the context and the scorecard.

The chance of target is a fitted figure for the trade a row names
(``entry["chance"]``, ``role["chance"]``): it sits BESIDE the usual win rate, never
in place of it, with when its weights were fitted and whether they beat the usual
rate on days they were not fitted on. When the service has the feature switched
off no entry carries a ``chance`` key and none of it is drawn
(:func:`chance_enabled`). The confirmation WORD (Confirms, Mixed, Contradicts) and
its count of sources are not on the page: they are still recorded, and the
scorecard still splits by them.

The 15-minute calls (``view["intraday"]``, 08:51 to 13:51 CT) keep the same
truthfulness and add two rules of their own: the rates are over EVERY stored day
and the counts beside them are DAYS (not rows). A section of another day is
labelled as that day's, and a latest call whose next slot has come reads as past.

Pure helpers (``role_rows``, ``headline``, ``input_rows``, ``state_line``,
``intraday_role_rows``, ``intraday_status`` ...) carry every fact; ``render`` holds widgets and wiring. Tier 1: this reads ONE
view and imports no engine and no config module of the service.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

from shared import market_calendar as _mc

from pages import fmt as _fmt
from pages import news_view as _nv   # safe_href: the one http(s)-only link rule
from pages.options import theme as _t

# bus_client reads and watches a view by its name without the ``cache:`` prefix:
# this is the key ``cache:options:scalp``.
VIEW = "options:scalp"

#: How often the page asks whether the clock changed what it shows (seconds).
CLOCK_TICK_SEC = 30

ROLE_ORDER = ("long_with", "long_against", "credit_with", "credit_against")

_CT = ZoneInfo("America/Chicago")

NO_EDGE = "No measured edge"
CAUTION = "Caution: measured to lose more often than usual"
EDGE = "Measured edge"
SCORECARD_EMPTY = "No graded days yet."
#: The fitted chance of target: the column's label and the block's title.
CHANCE_LABEL = "Chance of target"
CHANCE_TITLE = "What moved the chance"
CHANCE_NOTE = ("How far each factor moved the chance for the trade with the call, in "
               "percentage points, and how many days of history its weight rests on. "
               "A factor marked assumed still has its starting weight: too little history "
               "yet to have learned its own.")
NOT_FITTED = "not fitted yet"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
           "Nov", "Dec")

#: The contract each role is on a morning that opened up; a down morning swaps
#: calls for puts. ``with`` / ``against`` is the side of the opening move.
_UP_CONTRACT = {"long_with": "Long call", "long_against": "Long put",
                "credit_with": "Put credit spread", "credit_against": "Call credit spread"}
_DOWN_CONTRACT = {"long_with": "Long put", "long_against": "Long call",
                  "credit_with": "Call credit spread", "credit_against": "Put credit spread"}
_SIDE = {"long_with": "with the opening move", "long_against": "against the opening move",
         "credit_with": "with the opening move", "credit_against": "against the opening move"}


# ── small helpers ────────────────────────────────────────────────────────────
def _rate(v):
    """A probability (0..1) as a percentage, two decimals; the dash if absent."""
    f = _fmt.num(v)
    return _fmt.NO_READING if f is None else _fmt.pct(f * 100.0)


def _interval(lo, hi):
    if _fmt.num(lo) is None or _fmt.num(hi) is None:
        return _fmt.NO_READING
    return f"{_rate(lo)} – {_rate(hi)}"


def _count(v):
    f = _fmt.num(v)
    return _fmt.NO_READING if f is None else _fmt.plain(int(f))


def _points(v):
    """Underlying points to the target / the stop, signed (a put's target is a
    fall): ``+1.27`` / ``-0.99``."""
    f = _fmt.num(v)
    if f is None:
        return _fmt.NO_READING
    return ("+" if f > 0 else "") + _fmt.price(f)


def _strikes(raw):
    if not isinstance(raw, (list, tuple)) or not raw:
        return _fmt.NO_READING
    return " / ".join(_fmt.strike_text(s) for s in raw)


def _as_dict(v):
    return v if isinstance(v, dict) else {}


def _ct(iso):
    """An ISO timestamp as an aware Central datetime, or None."""
    if not isinstance(iso, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        return when.replace(tzinfo=_CT)       # a naive time means Central
    return when.astimezone(_CT)


# ── the four trades ──────────────────────────────────────────────────────────
def direction_words(direction):
    return {1: "Opened up", -1: "Opened down"}.get(direction, "No direction")


def contract_name(role, direction):
    """The role's contract for that morning, e.g. ``Long put``."""
    table = _DOWN_CONTRACT if direction == -1 else _UP_CONTRACT
    return table.get(role, role)


def _tone(sym, role):
    if sym.get("highlight") == role:
        return "edge"
    if role in (sym.get("avoid") or []):
        return "caution"
    return "plain"


_MARK = {"edge": EDGE, "caution": CAUTION, "plain": ""}
_ROW_CLASS = {"edge": f"bg-[{_t.THEME['semantic']['positive']}]/10",
              "caution": f"bg-[{_t.THEME['semantic']['warning']}]/10", "plain": ""}
_MARK_CLASS = {"edge": f"{_t.BADGE_POS} px-2 py-0.5 font-semibold",
               "caution": f"{_t.BADGE_WARN} px-2 py-0.5", "plain": ""}
# The bucket's own rate is emphasised only where the service found an edge.
_RATE_CLASS = {"edge": f"{_t.TXT_POS} font-semibold", "caution": _t.TXT_WARN,
               "plain": _t.MUTED}


def _entry_text(role):
    value = _fmt.num(role.get("entry_value"))
    if value is None:
        return _fmt.NO_READING
    kind = "debit" if str(role.get("structure") or "").startswith("long") else "credit"
    source = {"chain": "live quote", "model": "modelled"}.get(role.get("entry_source"))
    return f"{_fmt.price(value)} {kind}" + (f" · {source}" if source else "")


# ── the chance of target ─────────────────────────────────────────────────────
def chance_text(value):
    """A fitted chance (0..1) as a percentage, two decimals; the dash if absent."""
    return _rate(value)


def _chance_of(entry):
    """The entry's ``chance`` block, or None (no roles, a bad payload, switched off)."""
    got = _as_dict(entry).get("chance")
    return got if isinstance(got, dict) else None


def _role_chance(entry, name, role):
    """The named trade's chance: its own ``role["chance"]``, else the entry's
    ``by_role``. None when neither is a number."""
    own = _as_dict(role).get("chance")
    if _fmt.num(own) is not None:
        return own
    return _as_dict(_as_dict(_chance_of(entry)).get("by_role")).get(name)


def chance_enabled(view):
    """Whether the service has the chance switched on: some call in the view
    carries a ``chance`` KEY (its value may be None: no roles, or not fitted yet).
    When no entry does, no chance column or block is drawn. PURE."""
    view = _as_dict(view)
    entries = [e for e in _as_dict(view.get("symbols")).values() if isinstance(e, dict)]
    entries += [e for _name, e in latest_entries(intraday_section(view))]
    return any("chance" in e for e in entries)


def chance_note(chance, role):
    """The small line under a chance: ``fitted Oct 25 · beats the usual rate on
    unseen days: no``; ``not fitted yet`` with no model; '' with no chance block.
    ``role`` picks which trade's walk-forward verdict is quoted. PURE."""
    if not isinstance(chance, dict):
        return ""
    if chance.get("version") is None:
        return NOT_FITTED
    when = _ct(chance.get("fitted_at"))
    parts = ["fitted" + (f" {_MONTHS[when.month - 1]} {when.day}" if when else "")]
    beats = _as_dict(chance.get("beats_usual")).get(role)
    if isinstance(beats, bool):
        parts.append("beats the usual rate on unseen days: " + ("yes" if beats else "no"))
    return " · ".join(parts)


def chance_lead(section, now=None):
    """Which day the chance is from, when that is not today; else ''."""
    status = intraday_status(section, now)
    if status["today"]:
        return ""
    return f"From the {status['title'][0].lower()}{status['title'][1:]}"


_PUSH_LABEL = {"room": "Room to the wall", "breadth_room": "Room to the wall, big names",
               "flip_side": "Side of the gamma flip", "netprem": "Net premium",
               "flow": "Options flow", "sentiment": "Market sentiment"}
#: The 15-minute calls' price inputs (``scalp_intraday_inputs``); the 08:36 call's
#: are ``_INPUT_LABEL``'s. ``vol_change`` and ``etf:<X>`` exist in both and mean
#: different moves, so the call's kind picks the words.
_PUSH_INTRADAY_LABEL = {
    "since_open": "Move since the open", "vwap_side": "Side of VWAP",
    "fut_15m": "Futures, last 15 minutes",
    "vol_change": "Volatility index, last 15 minutes",
    "breadth_up_share": "Big-ten breadth"}
_PUSH_PRICE_LABEL = {"breadth_up_share": "Big-ten breadth"}
_INTRADAY_ONLY = ("since_open", "vwap_side", "fut_15m", "breadth_up_share")


def _timeframe(size):
    """A bar size in minutes in words: ``5-minute``, ``hourly``."""
    return "hourly" if int(size) == 60 else f"{int(size)}-minute"


def _is_intraday(names):
    """Whether these factor names are a 15-minute call's: it carries the time-of-day
    indicators and its own price inputs."""
    return any(n.startswith("block:") or n[len("price:"):] in _INTRADAY_ONLY
               for n in names if n.startswith(("block:", "price:")))


def _push_label(name, intraday=False):
    """A factor's name in words, never a raw key."""
    if name.startswith("price:"):
        key = name[len("price:"):]
        if intraday:
            if key in _PUSH_INTRADAY_LABEL:
                return _PUSH_INTRADAY_LABEL[key]
            if key.startswith("etf:"):
                return f"{key[4:]}, last 15 minutes"
        label = _PUSH_PRICE_LABEL.get(key) or _input_label(key, {})
        if label == key:                      # nobody has labelled it: words, not the key
            label = key.replace("_", " ").replace(":", " ")
            label = label[:1].upper() + label[1:]
        return label
    if name.startswith("block:"):
        return "Time of day"
    kind, _sep, size = name.partition("_")
    if size.isdigit() and kind in ("slope", "rsi"):
        head = "Moving-average slope" if kind == "slope" else "Momentum (RSI)"
        return f"{head}, {_timeframe(size)}"
    return _PUSH_LABEL.get(name) or name.replace("_", " ").capitalize()


def push_rows(chance, kind=None):
    """One row per factor of ``chance["pushes"]``, largest push first: its name in
    words, its reading signed by the call (how much it agrees with it), its push in
    percentage points, the days its weight rests on, and ``assumed`` where the
    weight is still the starting one. PURE. ``[]`` with none."""
    pushes = _as_dict(chance).get("pushes")
    if kind is None:
        kind = "intraday" if _is_intraday(
            [p["factor"] for p in pushes if isinstance(p, dict)
             and isinstance(p.get("factor"), str)] if isinstance(pushes, list) else []
        ) else "morning"
    pairs = []
    for push in pushes if isinstance(pushes, list) else []:
        if not isinstance(push, dict) or not isinstance(push.get("factor"), str) \
                or not push["factor"]:
            continue
        points = _fmt.num(push.get("points"))
        pairs.append((abs(points or 0.0), {
            "id": push["factor"], "factor": _push_label(push["factor"], kind == "intraday"),
            "reading": _fmt.ratio(push.get("reading")),
            "points": _fmt.NO_READING if points is None else _points(points) + " pts",
            "days": _days_word(push.get("days")),
            "assumed": "assumed" if push.get("assumed") is True else ""}))
    pairs.sort(key=lambda p: p[0], reverse=True)
    return [row for _size, row in pairs]


def push_columns():
    return [
        {"name": "factor", "label": "Factor", "field": "factor", "align": "left"},
        {"name": "reading", "label": "Reading (for the call)", "field": "reading"},
        {"name": "points", "label": "Moved the chance", "field": "points"},
        {"name": "days", "label": "Days of history", "field": "days"},
        {"name": "assumed", "label": "Starting weight", "field": "assumed"},
    ]


def _chance_column():
    return {"name": "chance", "label": CHANCE_LABEL, "field": "chance"}


def _with_chance_column(cols, chance):
    """``cols`` with the chance column after the usual win rate, or as they are."""
    if not chance:
        return cols
    at = next(i for i, c in enumerate(cols) if c["name"] == "usual") + 1
    return cols[:at] + [_chance_column()] + cols[at:]


def role_rows(symbol_payload):
    """One table row per role, in the fixed order, named by the morning's
    contract. PURE. An empty list when the symbol has no roles."""
    sym = _as_dict(symbol_payload)
    roles = _as_dict(sym.get("roles"))
    rows = []
    for name in ROLE_ORDER:
        role = roles.get(name)
        if not isinstance(role, dict):
            continue
        tone = _tone(sym, name)
        rows.append({
            "id": name, "trade": contract_name(name, sym.get("direction")),
            "side": _SIDE[name], "mark": _MARK[tone], "tone": tone,
            "usual": _rate(role.get("p_base")), "rate": _rate(role.get("p")),
            "chance": chance_text(_role_chance(sym, name, role)),
            "interval": _interval(role.get("lo"), role.get("hi")),
            "n": _count(role.get("n")), "n_base": _count(role.get("n_base")),
            "samples": f"{_count(role.get('n'))} / {_count(role.get('n_base'))}",
            "strikes": _strikes(role.get("strikes")), "entry": _entry_text(role),
            "target": _points(role.get("win_points")),
            "stop": _points(role.get("loss_points")),
            "_row_class": _ROW_CLASS[tone], "_mark_class": _MARK_CLASS[tone],
            "_rate_class": _RATE_CLASS[tone]})
    return rows


def role_columns(chance=True):
    """Seven columns (eight with the chance), so the table fits a phone with little
    sideways scroll: the mark sits under the trade's name and the interval under its
    rate. ``chance=False`` is the service with the chance switched off."""
    return _with_chance_column([
        {"name": "trade", "label": "Trade", "field": "trade", "align": "left"},
        {"name": "usual", "label": "Usual win rate", "field": "usual"},
        {"name": "rate", "label": "This morning's rate", "field": "rate"},
        {"name": "samples", "label": "Samples (bucket / all)", "field": "samples"},
        {"name": "strikes", "label": "Strikes", "field": "strikes"},
        {"name": "entry", "label": "Entry", "field": "entry"},
        {"name": "target", "label": "Underlying move", "field": "target"},
    ], chance)


def headline(symbol_payload):
    """The one sentence a card leads with. PURE.

    No roles: why there is no call (the service's own note). Roles with no
    highlight: ``No measured edge`` - and no role is named, so the line cannot be
    read as a recommendation. A highlight: that role, its rate, interval and n,
    against its usual rate."""
    sym = _as_dict(symbol_payload)
    roles = _as_dict(sym.get("roles"))
    if not roles:
        note = sym.get("note")
        if isinstance(note, str) and note.strip():
            note = note.strip()
            return note[0].upper() + note[1:]
        return "No call for this symbol"
    name = sym.get("highlight")
    role = roles.get(name) if isinstance(name, str) else None
    if not isinstance(role, dict):
        return f"{NO_EDGE} — these are the usual win rates"
    return (f"{EDGE}: {contract_name(name, sym.get('direction'))} won "
            f"{_rate(role.get('p'))} of the time ({_interval(role.get('lo'), role.get('hi'))}, "
            f"{_count(role.get('n'))} samples), against {_rate(role.get('p_base'))} usually")


def pricing_line(symbol_payload):
    """How the strikes and premiums were priced, or an empty string."""
    sym = _as_dict(symbol_payload)
    if not sym.get("roles"):
        return ""
    chain = sym.get("chain")
    if isinstance(chain, dict):
        iv = _fmt.num(chain.get("atm_iv"))
        vol = "" if iv is None else f", at-the-money volatility {_fmt.pct(iv * 100.0)}"
        return f"Priced from the live chain: underlying {_fmt.price(chain.get('spot'))}{vol}."
    return "No live chain: priced from the model, so the premiums are modelled."


def alignment_text(symbol_payload):
    sym = _as_dict(symbol_payload)
    bucket = sym.get("bucket")
    if not bucket:
        return ""
    share = _fmt.num(sym.get("alignment"))
    tail = "" if share is None else f" ({_fmt.pct(share * 100.0)} of the inputs that took a side)"
    return f"Agreement with the opening move: {bucket}{tail}"


# ── the inputs ───────────────────────────────────────────────────────────────
_INPUT_LABEL = {
    "fut_asia": "Overnight futures, Asia session",
    "fut_europe": "Overnight futures, Europe session",
    "fut_gap": "Futures gap from yesterday's close",
    "fut_range_pos": "Futures position in the overnight range",
    "vol_change": "Volatility index change before the open",
    "open_close_loc": "Where the opening bar closed in its own range",
    "open_broke_pm": "Opening bar against the premarket range",
    "open_range_ratio": "Opening range compared with its usual size",
}
_INPUT_ORDER = ("fut_asia", "fut_europe", "fut_gap", "fut_range_pos", "etf:", "vol_change",
                "open_close_loc", "open_broke_pm", "open_range_ratio")
_POSITION_INPUTS = ("fut_range_pos", "open_close_loc")
_VERDICT = {1: "Agrees", -1: "Disagrees", 0: "No side taken (dead zone)"}
ABSENT = "Absent"
INFO_ONLY = "Information only"
#: A reading is there but nothing to compare it with: a flat open (or no opening
#: bar) names no direction, so the service scores no input at all.
NO_DIRECTION = "No opening direction"
_VERDICT_CLASS = {"Agrees": _t.TXT_POS, "Disagrees": _t.TXT_NEG,
                  "No side taken (dead zone)": _t.MUTED, ABSENT: _t.MUTED,
                  INFO_ONLY: _t.MUTED, NO_DIRECTION: _t.MUTED}


def _input_label(key, sym):
    if key.startswith("etf:"):
        return f"{key[4:]} premarket change from yesterday's close"
    label = _INPUT_LABEL.get(key, key)
    if key == "vol_change" and sym.get("vol_source"):
        label += f" ({sym['vol_source']}; a fall agrees with up)"
    return label


def _input_reading(key, value):
    f = _fmt.num(value)
    if f is None:
        return _fmt.NO_READING
    if key in _POSITION_INPUTS:
        return _fmt.pct(f * 100.0)
    if key == "open_broke_pm":
        return ("Broke above the premarket high" if f > 0 else
                "Broke below the premarket low" if f < 0 else
                "Stayed inside the premarket range")
    if key == "open_range_ratio":
        return f"{_fmt.ratio(f)}×"
    return _fmt.pct(f, signed=True)


def _input_rank(key):
    for i, head in enumerate(_INPUT_ORDER):
        if key == head or (head.endswith(":") and key.startswith(head)):
            return (i, key)
    return (len(_INPUT_ORDER), key)


def input_rows(symbol_payload):
    """One row per morning input: its reading and whether it agreed with the
    opening move. PURE. ``Absent`` means the READING is missing - never a
    disagreement. A reading that is there but unscored because the morning has no
    direction says so (``No opening direction``), not ``Absent``."""
    sym = _as_dict(symbol_payload)
    inputs, agree = _as_dict(sym.get("inputs")), _as_dict(sym.get("agree"))
    rows = []
    for key in sorted(set(inputs) | set(agree), key=_input_rank):
        if _fmt.num(inputs.get(key)) is None:
            verdict = ABSENT
        elif key == "open_range_ratio" and key not in agree:
            verdict = INFO_ONLY
        elif agree.get(key) in _VERDICT:
            verdict = _VERDICT[agree[key]]
        elif sym.get("direction") not in (1, -1):
            verdict = NO_DIRECTION
        else:
            verdict = ABSENT
        rows.append({"id": key, "label": _input_label(key, sym),
                     "reading": _input_reading(key, inputs.get(key)),
                     "verdict": verdict, "_tone_class": _VERDICT_CLASS[verdict]})
    return rows


def input_columns():
    return [
        {"name": "label", "label": "Input", "field": "label", "align": "left"},
        {"name": "reading", "label": "Reading", "field": "reading"},
        {"name": "verdict", "label": "Against the opening move", "field": "verdict"},
    ]


def input_summary(rows):
    """``"5 agree · 1 disagree · 3 absent"`` over the rows."""
    count = {v: sum(1 for r in rows if r["verdict"] == v)
             for v in (*_VERDICT.values(), ABSENT, NO_DIRECTION)}
    if count[NO_DIRECTION]:
        n = count[NO_DIRECTION]
        parts = [f"{n} {'reading' if n == 1 else 'readings'}, "
                 "no opening direction to compare"]
        if count[ABSENT]:
            parts.append(f"{count[ABSENT]} absent")
        return " · ".join(parts)
    parts = [f"{count['Agrees']} agree", f"{count['Disagrees']} disagree"]
    if count["No side taken (dead zone)"]:
        parts.append(f"{count['No side taken (dead zone)']} in the dead zone")
    parts.append(f"{count[ABSENT]} absent")
    return " · ".join(parts)


# ── whose call is this, and the state line ───────────────────────────────────
def _clock(now=None) -> _dt.datetime:
    """The Central clock the page reads 'today' from. Injected so a test is
    deterministic; a naive datetime means Central."""
    if now is None:
        return _dt.datetime.now(_CT)
    return now.replace(tzinfo=_CT) if now.tzinfo is None else now.astimezone(_CT)


def _call_time() -> _dt.time:
    """The morning call's time (``[slots.scalp]`` in sessions.toml, 08:36)."""
    try:
        times = sorted(_mc.slot_times("scalp").values())
    except Exception:  # noqa: BLE001 - a bad slot table must not blank the page
        times = []
    return times[0] if times else _dt.time(8, 36)


def _clock_line(now: _dt.datetime):
    """What the CLOCK says about the next call, whatever the view says.

    The service publishes ``waiting`` / ``closed`` only when the 08:36 slot runs
    or Refresh is pressed, so overnight and over a weekend the view still holds
    the last trading day's ``call`` payload. The page decides for itself: not a
    trading day -> the next trading day's call; a trading day before the call
    time -> today's. Otherwise None (the call is, or is about to be, today's)."""
    today = now.date()
    at = _call_time()
    if not _mc.is_trading_day(today):
        nxt = _dt.datetime.combine(_mc.next_trading_day(today), at, tzinfo=_CT)
        return (f"Market closed — next call {nxt.strftime('%a %Y-%m-%d')} "
                f"at {nxt.strftime('%H:%M CT')}")
    if now.time() < at:
        return "Next call at " + at.strftime("%H:%M CT")
    return None


def state_line(view, now=None):
    """The line at the top of the page, or None for a live call. PURE.

    The clock decides first (``_clock_line``). The view's own ``error`` line is
    always kept; its ``waiting`` / ``closed`` line is used only when it was
    published today, after the call time (so it is not stale)."""
    view = _as_dict(view)
    now = _clock(now)
    state = view.get("state")
    if state == "error":
        return "The last refresh failed — showing the last call"
    line = _clock_line(now)
    if line:
        return line
    if state in ("waiting", "closed") and view.get("day") == now.date().isoformat():
        nxt = _ct(view.get("next_call"))
        if state == "waiting":
            return ("Next call at " + nxt.strftime("%H:%M CT") if nxt
                    else "Waiting for the morning call")
        if nxt is None:
            return "Market closed"
        return (f"Market closed — next call {nxt.strftime('%a %Y-%m-%d')} "
                f"at {nxt.strftime('%H:%M CT')}")
    return None


def call_label(view, symbol_payload, now=None):
    """Which call this card shows. PURE. It is 'Today's call' only when the call
    day is today's date on the Central clock; any other day's names its day and
    says it is not today's (the view's own ``day`` is the day it was PUBLISHED
    and goes stale overnight)."""
    view, sym = _as_dict(view), _as_dict(symbol_payload)
    made = _ct(sym.get("made_at"))
    stamp = f" · made {made.strftime('%H:%M CT')}" if made else ""
    call_day = view.get("call_day")
    if not call_day:
        return "Last call"
    if call_day == _clock(now).date().isoformat():
        return f"Today's call{stamp}"
    return f"Call of {call_day}{stamp} — not today's call"


# ── context: shown beside the call, not measured ──────────────────────────────────────────
def report_href(context):
    return _nv.safe_href(_as_dict(context).get("report_url"))


def news_rows(context):
    """The headlines: title, a followable address or None, source, and a time."""
    items = _as_dict(context).get("news")
    rows = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        when = _ct(item.get("published_at"))
        rows.append({"title": str(item.get("title") or "").strip() or "(untitled)",
                     "href": _nv.safe_href(item.get("url")),
                     "source": str(item.get("source") or ""),
                     "time": when.strftime("%H:%M CT") if when else ""})
    return rows


def flow_rows(context):
    """Today's flow alerts on the scalp symbols, oldest first."""
    items = _as_dict(context).get("flow")
    rows = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        ts = _fmt.num(item.get("ts"))
        when = None if ts is None else _dt.datetime.fromtimestamp(ts, _CT)
        rows.append({"time": when.strftime("%H:%M") if when else _fmt.NO_READING,
                     "symbol": str(item.get("symbol") or ""),
                     "kind": str(item.get("type") or "").replace("_", " "),
                     "side": str(item.get("side") or ""),
                     "text": str(item.get("text") or "")})
    return rows


def flow_columns():
    return [
        {"name": "time", "label": "Time (CT)", "field": "time", "align": "left"},
        {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left"},
        {"name": "kind", "label": "Alert", "field": "kind", "align": "left"},
        {"name": "text", "label": "What", "field": "text", "align": "left"},
    ]


# ── the scorecard ────────────────────────────────────────────────────────────
#: Role words for the scorecard: its rows span mornings of both directions, so a
#: role cannot be named by one morning's contract.
_ROLE_WORDS = {"long_with": "Long option, with the open",
               "long_against": "Long option, against the open",
               "credit_with": "Credit spread, with the open",
               "credit_against": "Credit spread, against the open"}


def _days_word(n):
    f = _fmt.num(n)
    return _fmt.NO_READING if f is None else f"{_fmt.plain(int(f))} {'day' if int(f) == 1 else 'days'}"


def scorecard_symbols(scorecard):
    """The scorecard's symbol cards, in the service's order, as ``[(symbol, card)]``;
    an empty list for an empty, absent or malformed scorecard. PURE."""
    cards = _as_dict(_as_dict(scorecard).get("symbols"))
    return [(name, card) for name, card in cards.items() if isinstance(card, dict)]


def scorecard_heading(scorecard):
    """The panel's lead line: how many graded days, over how long a look-back."""
    card = _as_dict(scorecard)
    return (f"{_days_word(card.get('days'))} graded, looking back at most "
            f"{_days_word(card.get('window'))} per symbol")


def edge_line(card):
    """The edge calls' record. PURE. With no highlighted day it says so rather than
    printing a rate: there was nothing to be right or wrong about."""
    edge = _as_dict(_as_dict(card).get("edge"))
    days = _fmt.num(edge.get("days"))
    if not days:
        return "Edge calls: none. No role was highlighted on a graded day."
    return (f"Edge calls: the highlighted role won {_count(edge.get('wins'))} of "
            f"{_count(days)} highlighted {'day' if days == 1 else 'days'} "
            f"({_rate(edge.get('rate'))}), against {_rate(edge.get('p_base'))} usually.")


def caution_line(card):
    """The cautions' record: how often a role we warned about still won, beside the
    rate it usually wins. A warning is working when the first is below the second."""
    caution = _as_dict(_as_dict(card).get("caution"))
    days = _fmt.num(caution.get("days"))
    if not days:
        return "Cautions: none. No role was marked to avoid on a graded day."
    return (f"Cautions: a role marked to avoid won {_count(caution.get('wins'))} of "
            f"{_count(days)} {'time' if days == 1 else 'times'} "
            f"({_rate(caution.get('rate'))}), against {_rate(caution.get('p_base'))} usually.")


def scorecard_rows(card):
    """One row per role, in the fixed order: the model's win / loss / timeout counts
    and win rate, and the chain-priced win rate where the chain gave one. PURE."""
    roles = _as_dict(_as_dict(card).get("roles"))
    rows = []
    for name in ROLE_ORDER:
        role = roles.get(name)
        if not isinstance(role, dict):
            continue
        chain_n = _fmt.num(role.get("chain_n"))
        rows.append({
            "id": name, "role": _ROLE_WORDS[name], "n": _count(role.get("n")),
            "win": _count(role.get("win")), "loss": _count(role.get("loss")),
            "timeout": _count(role.get("timeout")), "rate": _rate(role.get("win_rate")),
            "chain": (_fmt.NO_READING if not chain_n else
                      f"{_rate(role.get('chain_win_rate'))} of {_count(chain_n)}")})
    return rows


def scorecard_columns():
    return [
        {"name": "role", "label": "Trade", "field": "role", "align": "left"},
        {"name": "n", "label": "Days", "field": "n"},
        {"name": "win", "label": "Won", "field": "win"},
        {"name": "loss", "label": "Lost", "field": "loss"},
        {"name": "timeout", "label": "Timed out", "field": "timeout"},
        {"name": "rate", "label": "Win rate (model)", "field": "rate"},
        {"name": "chain", "label": "Win rate (live chain)", "field": "chain"},
    ]


# ── the intraday calls (every 15 minutes) ────────────────────────────────────
# ``view["intraday"]`` is ``{"day", "slot", "latest": {symbol: entry},
# "timeline": [{"slot", "symbols": {symbol: cell}}], "next_slot"}``. Three things
# differ from the 08:36 card and are said wherever they show: the rates are over
# EVERY stored day (the edge test is the walk-forward part), and the sample counts
# are DAYS.
RATES_BASIS_TEXT = "Rate over every stored day; the edge test is walk-forward."
INTRADAY_SCORECARD_NOTE = (
    "Days are distinct days; a win rate is over the calls made on those days (several "
    "a day), so two days can rest on more than two calls. The split by the recorded "
    "confirmation word is an informal first look: the word is not in the chance of "
    "target, which is fitted from the raw sources.")
TIMELINE_NOTE = RATES_BASIS_TEXT + " Days are distinct days."
TIMELINE_CHANCE_NOTE = (" Chance is the fitted chance of target for the trade the "
                        "call names.")
#: The scorecard splits its win rates by the recorded confirmation word.
CONFIRM_WORDS = ("Confirms", "Mixed", "Contradicts")
_MOVE_WORDS = {1: "moving up over the last 15 minutes",
               -1: "moving down over the last 15 minutes"}
_INTRADAY_SIDE = {"with": "with the last 15 minutes' move",
                  "against": "against the last 15 minutes' move"}
_DIRECTION_CELL = {1: "↑ Up", -1: "↓ Down"}
_NO_DIRECTION_CELL = f"{_fmt.NO_READING} No direction"


def intraday_section(view):
    """The view's ``intraday`` section, or ``{}``. PURE."""
    return _as_dict(_as_dict(view).get("intraday"))


def latest_entries(section):
    """The latest call per symbol as ``[(symbol, entry)]``, in the service's order."""
    latest = _as_dict(_as_dict(section).get("latest"))
    return [(name, entry) for name, entry in latest.items() if isinstance(entry, dict)]


def _when(when, day):
    """``HH:MM`` for a time on ``day``, else the weekday and date as well."""
    if when.date().isoformat() == day:
        return when.strftime("%H:%M")
    return when.strftime("%a %Y-%m-%d %H:%M")


#: The time stop a section that carries none is read with (``[trade]
#: time_stop_min``'s shipped value; Tier 1 does not read the service's config).
DEFAULT_TIME_STOP_MIN = 60


def _slot_trade_end(section):
    """The section slot's own trade end: the slot on its ``day`` floored to the
    5-minute bar it enters on, plus the section's ``time_stop_min`` (else
    ``DEFAULT_TIME_STOP_MIN``). ``None`` without a usable day and slot."""
    sec = _as_dict(section)
    day, slot = sec.get("day"), sec.get("slot")
    try:
        d = _dt.date.fromisoformat(day)
        hh, mm = (int(x) for x in slot.split(":"))
        entry = _dt.datetime(d.year, d.month, d.day, hh, mm - mm % 5, tzinfo=_CT)
    except (TypeError, ValueError, AttributeError):
        return None
    stop = _fmt.num(sec.get("time_stop_min"))
    stop = stop if stop is not None and stop > 0 else DEFAULT_TIME_STOP_MIN
    return entry + _dt.timedelta(minutes=stop)


def _trade_end(section):
    """When the latest call's trade is over: the latest ``entry_ts`` plus the
    ``rules.time_stop_min`` it was frozen with, as a Central datetime. When no
    entry carries both (every symbol flat, short of bars or missed: no trade),
    the slot's own entry plus the section's time stop, so a final slot of flat
    tapes still reads over when its hour is up; ``None`` without even that."""
    ends = []
    for _name, entry in latest_entries(section):
        ts = _fmt.num(entry.get("entry_ts"))
        stop = _fmt.num(_as_dict(entry.get("rules")).get("time_stop_min"))
        if ts is not None and stop is not None and stop > 0:
            ends.append(_dt.datetime.fromtimestamp(ts + stop * 60.0, _CT))
    return max(ends) if ends else _slot_trade_end(section)


def intraday_status(section, now=None):
    """What the CLOCK says about the section. PURE.

    ``today`` is the Central date equal to the section's own ``day``: a section of
    another day is labelled as that day's and is never read as current. Within
    today a latest call is CURRENT only until the earlier of its ``next_slot`` and
    the end of its own trade (``entry_ts`` plus its frozen time stop); after that it
    is ``past`` and reads 'Last call'. ``over`` is the trade being finished;
    ``late`` is the next slot having come on the same day without publishing;
    ``final`` is a ``next_slot`` on another day (the day's last call). ``line``
    names the slot and the next call; ``title`` heads the day's list of calls."""
    sec = _as_dict(section)
    now = _clock(now)
    day = sec.get("day") if isinstance(sec.get("day"), str) else ""
    slot = (sec["slot"] if isinstance(sec.get("slot"), str) and sec["slot"]
            else _fmt.NO_READING)
    nxt = _ct(sec.get("next_slot"))
    nxt_text = _when(nxt, day) if nxt else ""
    if not day or day != now.date().isoformat():
        title = f"Calls of {day or 'an earlier day'} — not today's"
        ahead = f" · next at {nxt_text}" if nxt and nxt > now else ""
        return {"today": False, "past": False, "over": False, "final": False,
                "late": False, "title": title, "latest_title": title,
                "line": title + ahead, "warning": ""}
    final = bool(nxt and nxt.date().isoformat() != day)
    end = _trade_end(sec)
    over = bool(end and now >= end)
    late = bool(nxt and not final and now >= nxt)
    past = over or late
    head = "Last" if past else "Final" if final else "Latest"
    line = f"{head} call{' of the day' if head == 'Final' else ''} at {slot}"
    if over:
        line += " · over"
    if final and head == "Last":
        line += " · final call of the day"
    if nxt_text:
        line += f" · next: {nxt_text}" if final else f" · next at {nxt_text}"
    if late:
        warning = ("A newer slot has passed and has not published yet; this is not "
                   "the current reading.")
    elif over:
        warning = ("This call's trade window is over; it is not a current reading "
                   "(the session may simply be finished).")
    else:
        warning = ""
    return {"today": True, "past": past, "over": over, "final": final, "late": late,
            "title": "Today's calls", "latest_title": "Latest call", "line": line,
            "warning": warning}


def clock_signature(view, now=None):
    """Everything on the page that the CLOCK (not a new view) can change, as one
    comparable value: the page repaints on the timer only when it moves. PURE."""
    view = _as_dict(view)
    now = _clock(now)
    status = intraday_status(intraday_section(view), now) if intraday_section(view) else None
    return (state_line(view, now),
            None if status is None else (status["line"], status["title"], status["past"],
                                         status["over"], status["warning"]),
            tuple(call_label(view, sym, now) for sym in _as_dict(view.get("symbols")).values()
                  if isinstance(sym, dict)))


def latest_label(section, now=None):
    """The small label on a latest-call card. PURE. Never 'Latest call' for another
    day's section; 'Last call ... past' once the next slot has come, and
    'Last call ... over' once its trade window has ended."""
    sec = _as_dict(section)
    status = intraday_status(sec, now)
    slot = (sec["slot"] if isinstance(sec.get("slot"), str) and sec["slot"]
            else _fmt.NO_READING)
    if not status["today"]:
        return f"Call of {sec.get('day') or 'an earlier day'} at {slot} — not today's call"
    if status["over"]:
        return f"Last call at {slot} · over"
    if status["past"]:
        return f"Last call at {slot} · past"
    return f"Latest call at {slot}"


def move_words(entry):
    """The last 15 minutes' direction in words. PURE."""
    sym = _as_dict(entry)
    said = _MOVE_WORDS.get(sym.get("direction")) if not isinstance(
        sym.get("direction"), bool) else None
    if said:
        return said
    note = sym.get("note")
    if isinstance(note, str) and note.strip().lower().startswith("no bars"):
        return "no bars for the last 15 minutes yet — no call"
    return "flat over the last 15 minutes — no call"


def intraday_headline(entry):
    """The one sentence a latest call leads with. PURE. As the 08:36 headline: no
    roles -> the service's own note; roles with no highlight -> no measured edge,
    naming no role; a highlight -> that role, its rate, interval and DAYS."""
    sym = _as_dict(entry)
    roles = _as_dict(sym.get("roles"))
    if not roles:
        return headline(sym)
    name = sym.get("highlight")
    role = roles.get(name) if isinstance(name, str) else None
    if not isinstance(role, dict):
        return f"{NO_EDGE} — these are the usual win rates"
    return (f"{EDGE}: {contract_name(name, sym.get('direction'))} won "
            f"{_rate(role.get('p'))} of the time ({_interval(role.get('lo'), role.get('hi'))}, "
            f"{_days_word(role.get('n_days'))}), against {_rate(role.get('p_base'))} usually")


def _walls(role):
    items = role.get("wall_context")
    if not isinstance(items, list):
        return ""
    return " · ".join(w.strip() for w in items if isinstance(w, str) and w.strip())


def intraday_role_rows(entry, muted=False):
    """One table row per role, in the fixed order, named by the call's contract.
    PURE. Counts are DAYS (``n_days`` / ``n_base_days``), unlike the 08:36 rows.
    ``muted`` (a call that is not current: another day's, or past) keeps every word
    and mark but draws no edge or caution colour."""
    sym = _as_dict(entry)
    roles = _as_dict(sym.get("roles"))
    rows = []
    for name in ROLE_ORDER:
        role = roles.get(name)
        if not isinstance(role, dict):
            continue
        tone = _tone(sym, name)
        side = _INTRADAY_SIDE["with" if name.endswith("_with") else "against"]
        rows.append({
            "id": name, "trade": contract_name(name, sym.get("direction")),
            "side": side, "mark": _MARK[tone], "tone": tone,
            "usual": _rate(role.get("p_base")), "rate": _rate(role.get("p")),
            "chance": chance_text(_role_chance(sym, name, role)),
            "interval": _interval(role.get("lo"), role.get("hi")),
            "days": _days_word(role.get("n_days")),
            "days_base": _days_word(role.get("n_base_days")),
            "samples": f"{_count(role.get('n_days'))} / "
                       f"{_days_word(role.get('n_base_days'))}",
            "strikes": _strikes(role.get("strikes")), "entry": _entry_text(role),
            "target": _points(role.get("win_points")),
            "stop": _points(role.get("loss_points")), "walls": _walls(role),
            "_row_class": "" if muted else _ROW_CLASS[tone],
            "_mark_class": (f"{_t.BADGE_MUTED} px-2 py-0.5" if muted and tone != "plain"
                            else _MARK_CLASS[tone]),
            "_rate_class": _t.MUTED if muted else _RATE_CLASS[tone]})
    return rows


def intraday_role_columns(chance=True):
    return _with_chance_column([
        {"name": "trade", "label": "Trade", "field": "trade", "align": "left"},
        {"name": "usual", "label": "Usual win rate", "field": "usual"},
        {"name": "rate", "label": "Rate in this bucket", "field": "rate"},
        {"name": "samples", "label": "Days (bucket / all)", "field": "samples"},
        {"name": "strikes", "label": "Strikes", "field": "strikes"},
        {"name": "entry", "label": "Entry", "field": "entry"},
        {"name": "target", "label": "Underlying move", "field": "target"},
    ], chance)


def rates_basis_line(entry):
    """What the shown rates are computed over, or '' when there are no rates."""
    return RATES_BASIS_TEXT if _as_dict(_as_dict(entry).get("roles")) else ""


def intraday_alignment_text(entry):
    sym = _as_dict(entry)
    bucket = sym.get("bucket")
    if not bucket:
        return ""
    share = _fmt.num(sym.get("alignment"))
    tail = "" if share is None else f" ({_fmt.pct(share * 100.0)} of the inputs that took a side)"
    return f"Agreement with the last 15 minutes' move: {bucket}{tail}"


def dealer_line(entry):
    """The dealer read as one line; the call's own, else the confirmation's. ''."""
    sym = _as_dict(entry)
    for line in (sym.get("dealer_line"), _as_dict(sym.get("confirmation")).get("dealer_line")):
        if isinstance(line, str) and line.strip():
            return line.strip()
    return ""


def inputs_line(entry):
    """Which inputs the call was built without, or ''. The service's own words
    when it gave them, else the failed list."""
    sym = _as_dict(entry)
    note = sym.get("inputs_note")
    if isinstance(note, str) and note.strip():
        return note.strip()[0].upper() + note.strip()[1:]
    failed = [f for f in (sym.get("inputs_failed") or []) if isinstance(f, str)] \
        if isinstance(sym.get("inputs_failed"), list) else []
    if failed:
        return "Built on fewer inputs: no fresh bars for " + ", ".join(failed)
    return ""


def intraday_notes(entry, lead):
    """The service's notes (missed, no volatility ...) not already in the headline."""
    sym = _as_dict(entry)
    out = []
    for extra in (sym.get("note"), sym.get("points_note")):
        if isinstance(extra, str) and extra.strip() and extra.strip().lower() not in lead.lower():
            out.append(extra.strip()[0].upper() + extra.strip()[1:])
    return out


# ── today's calls ────────────────────────────────────────────────────────────
def _timeline(section):
    items = _as_dict(section).get("timeline")
    return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []


def timeline_symbols(section):
    """Every symbol in the timeline, in the order first seen."""
    seen = []
    for row in _timeline(section):
        for name in _as_dict(row.get("symbols")):
            if name not in seen:
                seen.append(name)
    return seen


def timeline_columns(section):
    cols = [{"name": "time", "label": "Time (CT)", "field": "time", "align": "left"}]
    for i, name in enumerate(timeline_symbols(section)):
        cols.append({"name": f"s{i}", "label": str(name), "field": f"s{i}",
                     "align": "left", "sortable": False})
    return cols


def _pick_text(cell):
    hl = cell.get("highlight")
    if not isinstance(hl, str) or not hl:
        return _fmt.NO_READING
    name = contract_name(hl, cell.get("direction"))
    if _fmt.num(cell.get("edge_p")) is None:
        return name
    text = f"{name} · {_rate(cell.get('edge_p'))} vs {_rate(cell.get('edge_p_base'))} usually"
    if _fmt.num(cell.get("n_days")) is not None:
        text += f" · {_days_word(cell.get('n_days'))}"
    return text


def timeline_rows(section, muted=False, chance=True):
    """One row per published slot: the time and, per symbol (``s0``, ``s1`` ...), the
    direction word, the highlight or the dash, the named trade's chance and the
    note. PURE. The usual 'no measured edge' note is the dash, so it is not
    repeated. ``muted`` (another day's section) keeps every highlight's words but
    draws no edge colour: an old pick must not look actionable. The chance is
    ``Chance 47.12%`` for a cell with a direction (the dash when it has no
    reading) and '' for a flat one, or with ``chance=False`` (switched off)."""
    names = timeline_symbols(section)
    rows = []
    for item in _timeline(section):
        slot = item.get("slot") if isinstance(item.get("slot"), str) else _fmt.NO_READING
        row = {"id": slot, "time": slot}
        cells = _as_dict(item.get("symbols"))
        for i, name in enumerate(names):
            cell = cells.get(name)
            cell = cell if isinstance(cell, dict) else None
            picked = _pick_text(cell) if cell else _fmt.NO_READING
            note = cell.get("note") if cell else None
            note = note.strip() if isinstance(note, str) else ""
            row[f"s{i}"] = (_DIRECTION_CELL.get(cell.get("direction"), _NO_DIRECTION_CELL)
                            if cell else _fmt.NO_READING)
            row[f"s{i}_pick"] = picked
            row[f"s{i}_pick_class"] = (f"{_t.TXT_POS} font-semibold"
                                       if picked != _fmt.NO_READING and not muted
                                       else _t.MUTED)
            row[f"s{i}_chance"] = (
                f"Chance {chance_text(cell.get('chance'))}"
                if chance and cell and _direction(cell.get("direction")) is not None else "")
            row[f"s{i}_note"] = "" if note.lower() == NO_EDGE.lower() else note
        rows.append(row)
    return rows


# ── the intraday scorecard ───────────────────────────────────────────────────
def _calls_word(n):
    f = _fmt.num(n)
    return _fmt.NO_READING if f is None else f"{_fmt.plain(int(f))} {'call' if int(f) == 1 else 'calls'}"


def intraday_edge_line(block):
    """The highlighted calls' record in a block: wins of calls, with the DAYS they
    fell on. PURE. 'days' is distinct days, 'calls' is the rows."""
    edge = _as_dict(_as_dict(block).get("edge"))
    if not _fmt.num(edge.get("days")):
        return "Edge calls: none. No role was highlighted in this block on a graded day."
    return (f"Edge calls: the highlighted role won {_count(edge.get('wins'))} of "
            f"{_calls_word(edge.get('rows'))} ({_rate(edge.get('rate'))} · "
            f"{_days_word(edge.get('days'))}), against {_rate(edge.get('p_base'))} usually.")


def intraday_caution_line(block):
    caution = _as_dict(_as_dict(block).get("caution"))
    if not _fmt.num(caution.get("days")):
        return "Cautions: none. No role was marked to avoid in this block on a graded day."
    return (f"Cautions: a role marked to avoid won {_count(caution.get('wins'))} of "
            f"{_calls_word(caution.get('rows'))} ({_rate(caution.get('rate'))} · "
            f"{_days_word(caution.get('days'))}), against {_rate(caution.get('p_base'))} usually.")


def intraday_word_rows(block):
    """Win rates by the confirmation word, each beside the days it rests on."""
    by_word = _as_dict(_as_dict(block).get("by_word"))
    rows = []
    for word in CONFIRM_WORDS:
        stat = _as_dict(by_word.get(word))
        calls = _fmt.num(stat.get("rows"))
        rows.append({"id": word, "word": word, "rate": _rate(stat.get("win_rate")),
                     "calls": (f"{_count(stat.get('wins'))} of {_count(calls)}"
                               if calls else _fmt.NO_READING),
                     "days": _days_word(stat.get("days"))})
    return rows


def intraday_word_columns():
    return [
        {"name": "word", "label": "Confirmation", "field": "word", "align": "left"},
        {"name": "rate", "label": "Win rate", "field": "rate"},
        {"name": "calls", "label": "Won of calls", "field": "calls"},
        {"name": "days", "label": "Days", "field": "days"},
    ]


def intraday_scorecard_blocks(scorecard):
    """``[(symbol, days, [{"block", "label", "days", "data"}])]`` in the service's
    order; ``[]`` for an absent or malformed section. PURE."""
    sec = _as_dict(_as_dict(scorecard).get("intraday"))
    out = []
    for name, card in _as_dict(sec.get("symbols")).items():
        blocks = []
        for block, data in _as_dict(_as_dict(card).get("blocks")).items():
            if isinstance(data, dict):
                blocks.append({"block": block, "label": str(block).capitalize(),
                               "days": data.get("days"), "data": data})
        if blocks:
            out.append((name, _as_dict(card).get("days"), blocks))
    return out


def intraday_scorecard_heading(scorecard):
    sec = _as_dict(_as_dict(scorecard).get("intraday"))
    return (f"{_days_word(sec.get('days'))} graded, looking back at most "
            f"{_days_word(sec.get('window'))} per symbol and block")


def has_scorecard(scorecard):
    """Whether the scorecard has anything to show: 08:36 cards or intraday blocks."""
    return bool(scorecard_symbols(scorecard) or intraday_scorecard_blocks(scorecard))


# ── at a glance: one row per symbol ──────────────────────────────────────────
# The top of the page answers "what is each symbol doing now?" in one table; the
# cards that say why sit below it, closed. A row comes from the latest 15-minute
# call when the intraday section is at least as recent as the 08:36 call and holds
# that symbol, else from the 08:36 call. Its rate is the USUAL rate of the trade
# with the move, unless the service marked an edge: then the row names that trade
# and sets its rate against the usual one. A row that is not current (another
# day's, or a call whose time has passed) keeps its words and loses its colour.
GLANCE_EDGE_NONE = f"{NO_EDGE} — the rates are the usual win rates"
_GLANCE_DIRECTION = {1: "↑ Up", -1: "↓ Down"}
_GLANCE_DIRECTION_CLASS = {1: _t.TXT_POS, -1: _t.TXT_NEG}


def _direction(v):
    """1 / -1, else None (a bool is not a direction)."""
    return v if v in (1, -1) and not isinstance(v, bool) else None


def glance_symbols(view):
    """Every symbol on the page: the 08:36 call's order, then any symbol only the
    15-minute calls carry. PURE."""
    names = [n for n, s in _as_dict(_as_dict(view).get("symbols")).items()
             if isinstance(s, dict)]
    for name, _entry in latest_entries(intraday_section(view)):
        if name not in names:
            names.append(name)
    return names


def intraday_leads(view):
    """Whether the 15-minute calls are at least as recent as the 08:36 call, so
    they lead the glance and each symbol's details. PURE. Before the day's first
    15-minute call the section is the previous day's and the 08:36 call leads."""
    view = _as_dict(view)
    day = intraday_section(view).get("day")
    call_day = view.get("call_day")
    if not isinstance(day, str) or not day:
        return False
    return not isinstance(call_day, str) or day >= call_day


def _glance_role(entry):
    """``(role name, role)`` the glance names: the highlighted role when the
    service marked one, else the trade with the move; ``(None, None)`` with no
    roles."""
    roles = _as_dict(_as_dict(entry).get("roles"))
    hl = entry.get("highlight")
    name = hl if isinstance(hl, str) and isinstance(roles.get(hl), dict) else "long_with"
    role = roles.get(name)
    return (name, role) if isinstance(role, dict) else (None, None)


def _glance_price(entry):
    sym = _as_dict(entry)
    for v in (sym.get("entry_spot"), _as_dict(sym.get("chain")).get("spot")):
        if _fmt.num(v) is not None:
            return _fmt.price(v)
    return ""


def glance_row(name, entry, kind, *, current, when, morning=None):
    """One symbol's row. PURE. ``kind`` is ``"intraday"`` or ``"morning"``;
    ``morning`` is the 08:36 payload, for the last column."""
    sym = _as_dict(entry)
    d = _direction(sym.get("direction"))
    role_name, role = _glance_role(sym)
    tone = _tone(sym, role_name) if role_name else "plain"
    if role is None:
        lead = move_words(sym) if kind == "intraday" else headline(sym)
        trade, side = lead[0].upper() + lead[1:], ""
    else:
        trade = f"{contract_name(role_name, d)} {_strikes(role.get('strikes'))}"
        side = ("with the move" if role_name.endswith("_with")
                else "against the move")
    if role is None:
        rate, note = _fmt.NO_READING, ""
    elif tone == "edge":
        rate = f"{_rate(role.get('p'))} vs {_rate(role.get('p_base'))} usually"
        note = EDGE
    else:
        rate, note = _rate(role.get("p_base")), (CAUTION if tone == "caution" else "")
    # The chance is the NAMED trade's own, beside its usual rate; a row with no
    # trade (a flat tape, no roles) has none: the dash and no note.
    if role is None:
        chance, chance_line = _fmt.NO_READING, ""
    else:
        chance = chance_text(_role_chance(sym, role_name, role))
        chance_line = chance_note(_chance_of(sym), role_name)
    room = _fmt.NO_READING
    if kind == "intraday" and d is not None and dealer_line(sym):
        room = dealer_line(sym).split(";")[0].strip()
    m = _direction(_as_dict(morning).get("direction"))
    return {
        "id": name, "symbol": name, "price": _glance_price(sym),
        "now": _GLANCE_DIRECTION.get(d, "No direction"), "when": when,
        "_now_class": _GLANCE_DIRECTION_CLASS.get(d, _t.MUTED) if current else _t.MUTED,
        "chance": chance, "chance_note": chance_line,
        "_chance_class": "" if current else _t.MUTED,
        "trade": trade, "side": side, "rate": rate, "rate_note": note,
        "edge": tone == "edge" and current,
        "_rate_class": _RATE_CLASS[tone] if current and tone != "plain" else "",
        "room": room,
        "morning": (_GLANCE_DIRECTION.get(m, "No direction") if morning is not None
                    else _fmt.NO_READING),
        "_morning_class": _GLANCE_DIRECTION_CLASS.get(m, _t.MUTED) if current else _t.MUTED}


def glance_rows(view, now=None):
    """One row per symbol, in :func:`glance_symbols` order. PURE."""
    view = _as_dict(view)
    now = _clock(now)
    today = now.date().isoformat()
    section = intraday_section(view)
    latest = dict(latest_entries(section))
    lead = intraday_leads(view)
    status = intraday_status(section, now) if section else None
    morning = _as_dict(view.get("symbols"))
    call_day = view.get("call_day")
    rows = []
    for name in glance_symbols(view):
        sym = morning.get(name) if isinstance(morning.get(name), dict) else None
        entry = latest.get(name)
        if entry is not None and (lead or sym is None):
            slot = section.get("slot") if isinstance(section.get("slot"), str) else ""
            current = bool(status["today"] and not status["past"])
            when = (f"{slot} call" + ("" if current else " · past") if status["today"]
                    else f"{section.get('day') or 'an earlier day'} {slot} call · not today's")
            rows.append(glance_row(name, entry, "intraday", current=current,
                                   when=when, morning=sym))
        elif sym is not None:
            made = _ct(sym.get("made_at"))
            stamp = made.strftime("%H:%M") if made else "08:36"
            current = call_day == today
            when = (f"{stamp} call" if current
                    else f"{call_day or 'an earlier day'} {stamp} call · not today's")
            rows.append(glance_row(name, sym, "morning", current=current,
                                   when=when, morning=sym))
    return rows


def glance_line(view, now=None):
    """``(line, warning)`` over the glance table. PURE: the latest 15-minute
    call's own status line when those calls lead, else the 08:36 call's label."""
    view = _as_dict(view)
    section = intraday_section(view)
    if section and intraday_leads(view) and latest_entries(section):
        status = intraday_status(section, now)
        return status["line"], status["warning"]
    first = next((s for s in _as_dict(view.get("symbols")).values()
                  if isinstance(s, dict)), None)
    return (call_label(view, first, now) if first is not None else ""), ""


def edge_summary(rows):
    """The one line saying whether any symbol has a measured edge now. PURE."""
    names = [r["symbol"] for r in rows if r.get("edge")]
    if names:
        return f"{EDGE} now: {', '.join(names)}"
    return GLANCE_EDGE_NONE if any(r.get("side") for r in rows) else ""


def countdown_text(view, now=None):
    """``"next call in 9 min"`` while the section's next slot is later today,
    else ''. PURE; the page refreshes it on its clock tick without a repaint."""
    now = _clock(now)
    nxt = _ct(intraday_section(view).get("next_slot"))
    if nxt is None or nxt <= now or nxt.date() != now.date():
        return ""
    minutes = -(-int((nxt - now).total_seconds()) // 60)
    return f"next call in {minutes} min"


def glance_columns(chance=True):
    """The glance's columns; the chance of target sits right beside the usual win
    rate so the two read together. ``chance=False`` is the service with the chance
    switched off."""
    cols = [
        {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left",
         "sortable": False},
        {"name": "now", "label": "Now", "field": "now", "align": "left", "sortable": False},
        {"name": "trade", "label": "Trade with the call", "field": "trade",
         "align": "left", "sortable": False},
        {"name": "chance", "label": CHANCE_LABEL, "field": "chance", "align": "left",
         "sortable": False},
        {"name": "rate", "label": "Usually wins", "field": "rate", "align": "left",
         "sortable": False},
        {"name": "room", "label": "Room to the wall", "field": "room", "align": "left",
         "sortable": False},
        {"name": "morning", "label": "08:36 call", "field": "morning", "align": "left",
         "sortable": False},
    ]
    return cols if chance else [c for c in cols if c["name"] != "chance"]


# ── widgets ──────────────────────────────────────────────────────────────────
_TRADE_SLOT = r'''
  <q-td :props="props">
    <div class="font-semibold">{{ props.row.trade }}</div>
    <div class="text-[11px] opacity-70">{{ props.row.side }}</div>
    <div v-if="props.row.mark"
         class="mt-1 inline-block max-w-[150px] whitespace-normal leading-tight text-[11px]"
         :class="props.row._mark_class">{{ props.row.mark }}</div>
  </q-td>
'''
_USUAL_SLOT = r'''
  <q-td :props="props"><span class="font-semibold">{{ props.value }}</span></q-td>
'''
_RATE_SLOT = r'''
  <q-td :props="props">
    <div :class="props.row._rate_class">{{ props.value }}</div>
    <div class="text-[11px] opacity-70">{{ props.row.interval }}</div>
  </q-td>
'''
_MOVE_SLOT = r'''
  <q-td :props="props">
    <div>to target {{ props.row.target }}</div>
    <div class="text-[11px] opacity-70">to stop {{ props.row.stop }}</div>
  </q-td>
'''
_VERDICT_SLOT = r'''
  <q-td :props="props"><span :class="props.row._tone_class">{{ props.value }}</span></q-td>
'''

_INTRADAY_MOVE_SLOT = r'''
  <q-td :props="props">
    <div>to target {{ props.row.target }}</div>
    <div class="text-[11px] opacity-70">to stop {{ props.row.stop }}</div>
    <div v-if="props.row.walls"
         class="mt-1 max-w-[220px] whitespace-normal leading-tight text-[11px] opacity-80">
      {{ props.row.walls }}
    </div>
  </q-td>
'''
#: One timeline cell per symbol; ``@@`` is the symbol's column number.
_TIMELINE_CELL = r'''
  <q-td :props="props">
    <div class="font-semibold">{{ props.row.s@@ }}</div>
    <div class="text-[12px]" :class="props.row.s@@_pick_class">{{ props.row.s@@_pick }}</div>
    <div v-if="props.row.s@@_chance" class="text-[11px] opacity-70">{{ props.row.s@@_chance }}</div>
    <div v-if="props.row.s@@_note"
         class="max-w-[220px] whitespace-normal leading-tight text-[11px] opacity-70">
      {{ props.row.s@@_note }}
    </div>
  </q-td>
'''

_CARD = f"w-full {_t.CARD} gap-3"
_SMALL = f"text-[12px] {_t.MUTED}"


def _symbol_card(view, name, sym, now=None, title=None, chance=True):
    """One symbol's 08:36 call. ``title`` replaces the symbol as the heading when
    the card sits inside that symbol's own section; ``chance`` draws the chance of
    target column (off when the service has it switched off)."""
    from nicegui import ui

    from pages import ui_kit as kit

    rows = role_rows(sym)
    highlight = sym.get("highlight")
    # A non-string highlight (a list from a bad payload) is no highlight: an
    # unhashable value in a set test would raise and blank the card.
    has_edge = isinstance(highlight, str) and highlight in {r["id"] for r in rows}
    with ui.column().classes(_CARD):
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            ui.label(title or name).classes(f"text-lg font-semibold {_t.LABEL}")
            if rows:
                ui.label(direction_words(sym.get("direction"))) \
                    .classes(f"{_t.BADGE_ACCENT} px-2 py-0.5 text-[12px]")
            ui.space()
            ui.label(call_label(view, sym, now)).classes(_SMALL)
        lead = headline(sym)
        ui.label(lead).classes(
            f"text-xl font-semibold {_t.TXT_POS if has_edge else _t.LABEL}")
        for extra in (sym.get("note"), sym.get("points_note")):
            if isinstance(extra, str) and extra.strip() and extra.strip().lower() not in lead.lower():
                ui.label(extra.strip()[0].upper() + extra.strip()[1:]).classes(_SMALL)
        if rows:
            if alignment_text(sym):
                ui.label(alignment_text(sym)).classes(_SMALL)
            table = kit.table(role_columns(chance), rows,
                              numeric=("usual", "chance", "rate", "samples", "strikes",
                                       "entry", "target"))
            table.add_slot("body-cell-trade", _TRADE_SLOT)
            table.add_slot("body-cell-usual", _USUAL_SLOT)
            if chance:
                table.add_slot("body-cell-chance", _USUAL_SLOT)
            table.add_slot("body-cell-rate", _RATE_SLOT)
            table.add_slot("body-cell-target", _MOVE_SLOT)
            if pricing_line(sym):
                ui.label(pricing_line(sym)).classes(_SMALL)
        # A symbol with no call (a flat open, a missing bar) still shows the
        # inputs it has: they are how the operator sees WHY there is no call.
        inputs = input_rows(sym)
        if inputs:
            title = f"Morning inputs · {input_summary(inputs)}"
            with ui.expansion(title, value=True).classes(f"w-full {_t.LABEL}"):
                grid = kit.table(input_columns(), inputs, numeric=("reading",))
                grid.add_slot("body-cell-verdict", _VERDICT_SLOT)


def _latest_card(section, name, entry, now=None, title=None, chance=True):
    """One symbol's latest 15-minute call: the move in words, the four trades with
    their rates (and the days under them), the dealer read and the walls.
    ``title`` replaces the symbol as the heading inside the symbol's section;
    ``chance`` draws the chance of target column."""
    from nicegui import ui

    from pages import ui_kit as kit

    status = intraday_status(section, now)
    current = status["today"] and not status["past"]
    rows = intraday_role_rows(entry, muted=not current)
    highlight = entry.get("highlight")
    # No green emphasis on a call that is not current (another day's, or past).
    has_edge = (current and isinstance(highlight, str)
                and highlight in {r["id"] for r in rows})
    with ui.column().classes(_CARD):
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            ui.label(title or name).classes(f"text-lg font-semibold {_t.LABEL}")
            if rows or entry.get("direction") in (1, -1):
                # A flat tape's headline already says so; no second badge for it.
                ui.label(move_words(entry).capitalize()) \
                    .classes(f"{_t.BADGE_ACCENT} px-2 py-0.5 text-[12px]")
            ui.space()
            ui.label(latest_label(section, now)).classes(_SMALL)
        lead = intraday_headline(entry)
        ui.label(lead).classes(
            f"text-xl font-semibold {_t.TXT_POS if has_edge else _t.LABEL}")
        for extra in intraday_notes(entry, lead):
            ui.label(extra).classes(_SMALL)
        if inputs_line(entry):
            ui.label(inputs_line(entry)).classes(f"text-[12px] {_t.TXT_WARN}")
        if rows:
            if intraday_alignment_text(entry):
                ui.label(intraday_alignment_text(entry)).classes(_SMALL)
            table = kit.table(intraday_role_columns(chance), rows,
                              numeric=("usual", "chance", "rate", "samples", "strikes",
                                       "entry", "target"))
            table.add_slot("body-cell-trade", _TRADE_SLOT)
            table.add_slot("body-cell-usual", _USUAL_SLOT)
            if chance:
                table.add_slot("body-cell-chance", _USUAL_SLOT)
            table.add_slot("body-cell-rate", _RATE_SLOT)
            table.add_slot("body-cell-target", _INTRADAY_MOVE_SLOT)
            ui.label(rates_basis_line(entry)).classes(_SMALL)
            if pricing_line(entry):
                ui.label(pricing_line(entry)).classes(_SMALL)
        if dealer_line(entry):
            ui.label("Dealer read: " + dealer_line(entry)).classes("text-sm")


def _timeline_panel(section, status, chance=True):
    """The day's 15-minute calls, one row per slot. Its heading is the section it
    sits in (:func:`_details`)."""
    from nicegui import ui

    from pages import ui_kit as kit

    rows = timeline_rows(section, muted=not status["today"], chance=chance)
    with ui.column().classes(_CARD):
        if not rows:
            ui.label("No calls published yet.").classes(_SMALL)
            return
        ui.label(TIMELINE_NOTE + (TIMELINE_CHANCE_NOTE if chance else "")).classes(_SMALL)
        table = kit.table(timeline_columns(section), rows)
        for i in range(len(timeline_symbols(section))):
            table.add_slot(f"body-cell-s{i}", _TIMELINE_CELL.replace("@@", str(i)))


def _chance_block(entry, lead="", kind=None):
    """One symbol's chance of target and what moved it, for the trade with the
    call: a note line (when fitted, whether it beat the usual rate on unseen days)
    and one table of the factors, the largest push first. ``lead`` names the day
    when the section is not today's. Nothing for a call with no chance block (a
    flat tape, or the service with it switched off)."""
    from nicegui import ui

    from pages import ui_kit as kit

    chance = _chance_of(entry)
    if chance is None:
        return
    rows = push_rows(chance, kind)
    with ui.column().classes(_CARD):
        kit.section_title(CHANCE_TITLE)
        if lead:
            ui.label(lead).classes(f"text-sm {_t.TXT_WARN}")
        note = chance_note(chance, "long_with")
        if note:
            ui.label(note).classes(f"text-sm {_t.LABEL}")
        if not rows:
            ui.label("The weights have not been fitted yet, so there is no chance to "
                     "explain." if note == NOT_FITTED else
                     "No factors to show yet.").classes(_SMALL)
            return
        ui.label(CHANCE_NOTE).classes(_SMALL)
        kit.table(push_columns(), rows, numeric=("reading", "points", "days"))


_GLANCE_SLOTS = {
    "symbol": r'''
  <q-td :props="props">
    <div class="font-semibold">{{ props.row.symbol }}</div>
    <div class="text-[11px] opacity-70">{{ props.row.price }}</div>
  </q-td>
''',
    "now": r'''
  <q-td :props="props">
    <div class="font-semibold" :class="props.row._now_class">{{ props.row.now }}</div>
    <div class="text-[11px] opacity-70">{{ props.row.when }}</div>
  </q-td>
''',
    "trade": r'''
  <q-td :props="props">
    <div class="max-w-[220px] whitespace-normal leading-tight">{{ props.row.trade }}</div>
    <div v-if="props.row.side" class="text-[11px] opacity-70">{{ props.row.side }}</div>
  </q-td>
''',
    "chance": r'''
  <q-td :props="props">
    <div class="font-semibold" :class="props.row._chance_class">{{ props.row.chance }}</div>
    <div v-if="props.row.chance_note"
         class="max-w-[200px] whitespace-normal leading-tight text-[11px] opacity-70">{{ props.row.chance_note }}</div>
  </q-td>
''',
    "rate": r'''
  <q-td :props="props">
    <div class="font-semibold" :class="props.row._rate_class">{{ props.row.rate }}</div>
    <div v-if="props.row.rate_note"
         class="max-w-[180px] whitespace-normal leading-tight text-[11px]"
         :class="props.row._rate_class">{{ props.row.rate_note }}</div>
  </q-td>
''',
    "room": r'''
  <q-td :props="props">
    <div class="max-w-[240px] whitespace-normal leading-tight text-[12px]">{{ props.row.room }}</div>
  </q-td>
''',
    "morning": r'''
  <q-td :props="props"><span :class="props.row._morning_class">{{ props.row.morning }}</span></q-td>
''',
}


def _glance(view, now=None):
    """The top of the page: one line on which call is shown and when the next
    comes, then one table row per symbol. Returns the countdown label (the clock
    tick refreshes its text), or None when there is nothing to show. The chance
    column is drawn only when the service has the chance switched on."""
    from nicegui import ui

    from pages import ui_kit as kit

    rows = glance_rows(view, now)
    if not rows:
        return None
    line, warning = glance_line(view, now)
    summary = edge_summary(rows)
    has_edge = any(r["edge"] for r in rows)
    with ui.column().classes(_CARD):
        with ui.row().classes("w-full items-baseline gap-x-3 gap-y-1 flex-wrap"):
            ui.label(line).classes(f"text-base font-semibold {_t.LABEL}")
            countdown = ui.label(countdown_text(view, now)).classes(_SMALL)
            ui.space()
            if summary:
                ui.label(summary).classes(
                    f"text-sm font-semibold {_t.TXT_POS}" if has_edge else _SMALL)
        if warning:
            ui.label(warning).classes(f"text-sm {_t.TXT_WARN}")
        chance = chance_enabled(view)
        table = kit.table(glance_columns(chance), rows)
        for name, slot in _GLANCE_SLOTS.items():
            if chance or name != "chance":
                table.add_slot(f"body-cell-{name}", slot)
    return countdown


def _fold(title, key, opened=(), on_toggle=None):
    """A closed section of the details. It opens as the reader left it: ``opened``
    holds the keys open before a repaint, and ``on_toggle(key, open)`` records each
    change."""
    from nicegui import ui

    def _changed(e):
        if on_toggle is not None:
            on_toggle(key, bool(e.value))

    return ui.expansion(title, value=key in opened, on_value_change=_changed) \
        .classes(f"w-full {_t.LABEL}") \
        .props('header-class="text-base font-semibold"')


def _details(view, now=None, opened=(), on_toggle=None):
    """Everything under the glance, each in a closed section: one per symbol (its
    15-minute call, what moved its chance and its 08:36 call, the most recent first),
    then the day's calls, the context and the scorecard."""
    from nicegui import ui

    view = _as_dict(view)
    section = intraday_section(view)
    latest = dict(latest_entries(section))
    morning = _as_dict(view.get("symbols"))
    lead = chance_lead(section, now) if section else ""
    intraday_first = intraday_leads(view)
    chance = chance_enabled(view)

    def _intraday(name):
        entry = latest.get(name)
        if entry is not None:
            _latest_card(section, name, entry, now, title="15-minute call", chance=chance)
            if chance:
                _chance_block(entry, lead, "intraday")

    def _morning(name):
        sym = morning.get(name)
        if isinstance(sym, dict):
            _symbol_card(view, name, sym, now, title="08:36 call", chance=chance)
            if chance:
                _chance_block(sym, kind="morning")

    with ui.column().classes("w-full gap-2"):
        for name in glance_symbols(view):
            with _fold(f"{name} details", f"symbol:{name}", opened, on_toggle):
                with ui.column().classes("w-full gap-3"):
                    for part in ((_intraday, _morning) if intraday_first
                                 else (_morning, _intraday)):
                        part(name)
        if section:
            status = intraday_status(section, now)
            with _fold(status["title"], "timeline", opened, on_toggle):
                _timeline_panel(section, status, chance)
        with _fold("Context: headlines, flow alerts and the report",
                   "context", opened, on_toggle):
            _context_panel(view.get("context"))
        with _fold("Scorecard", "scorecard", opened, on_toggle):
            _scorecard_panel(view.get("scorecard"))


def _context_panel(context):
    from nicegui import ui

    from pages import ui_kit as kit

    with ui.column().classes(_CARD):
        ui.label("Shown beside the call for context. None of this was measured or "
                 "fed into the usual win rates above.").classes(_SMALL)
        href = report_href(context)
        if href:
            ui.link("Open today's report", href, new_tab=True).classes("text-sm")
        news = news_rows(context)
        flow = flow_rows(context)
        if news:
            ui.label("Headlines").classes(_t.EYEBROW)
            for n in news:
                with ui.row().classes("w-full items-baseline gap-2 no-wrap"):
                    ui.label(n["time"]).classes(f"{_SMALL} shrink-0")
                    (ui.link(n["title"], n["href"], new_tab=True) if n["href"]
                     else ui.label(n["title"])).classes("text-sm")
                    if n["source"]:
                        ui.label(n["source"]).classes(f"{_SMALL} shrink-0")
        if flow:
            ui.label("Flow alerts on these symbols today").classes(_t.EYEBROW)
            kit.table(flow_columns(), [dict(r, id=i) for i, r in enumerate(flow)])
        if not (href or news or flow):
            ui.label("Nothing to show yet.").classes(_SMALL)


def _scorecard_panel(scorecard):
    """The graded record: per symbol the edge calls, the cautions and every role,
    each beside the base rate it was published with. One line when nothing has
    been graded yet."""
    from nicegui import ui

    from pages import ui_kit as kit

    cards = scorecard_symbols(scorecard)
    with ui.column().classes(_CARD):
        if not has_scorecard(scorecard):
            ui.label(SCORECARD_EMPTY).classes(_SMALL)
            return
        if cards:
            ui.label(scorecard_heading(scorecard)).classes(_SMALL)
            ui.label("A win is the trade reaching its target before its stop inside the time "
                     "limit, as modelled from the volatility index; the live-chain column "
                     "repeats the walk at the volatility the 08:36 chain implied. Neither "
                     "is a fill.").classes(_SMALL)
        for name, card in cards:
            ui.label(f"{name} · {_days_word(card.get('days'))} graded").classes(
                f"text-base font-semibold {_t.LABEL}")
            ui.label(edge_line(card)).classes("text-sm")
            ui.label(caution_line(card)).classes("text-sm")
            kit.table(scorecard_columns(), scorecard_rows(card),
                      numeric=("n", "win", "loss", "timeout", "rate", "chain"))
        _intraday_scorecard(scorecard)


def _intraday_scorecard(scorecard):
    """The 15-minute calls' record per symbol and block: each rate beside the DAYS
    it rests on, never beside a row count."""
    from nicegui import ui

    from pages import ui_kit as kit

    symbols = intraday_scorecard_blocks(scorecard)
    if not symbols:
        return
    ui.label("Intraday calls").classes(f"text-base font-semibold {_t.LABEL}")
    ui.label(intraday_scorecard_heading(scorecard)).classes(_SMALL)
    ui.label(RATES_BASIS_TEXT + " " + INTRADAY_SCORECARD_NOTE).classes(_SMALL)
    for name, days, blocks in symbols:
        ui.label(f"{name} · {_days_word(days)} graded").classes(
            f"text-base font-semibold {_t.LABEL}")
        for block in blocks:
            data = block["data"]
            ui.label(f"{block['label']} · {_days_word(block['days'])}").classes(
                f"text-sm font-semibold {_t.LABEL}")
            ui.label(intraday_edge_line(data)).classes("text-sm")
            ui.label(intraday_caution_line(data)).classes("text-sm")
            ui.label("Win rate by confirmation (being recorded to be tested; "
                     "not in the probability)").classes(_SMALL)
            kit.table(intraday_word_columns(), intraday_word_rows(data),
                      numeric=("rate", "calls", "days"))


# ── the page ─────────────────────────────────────────────────────────────────
def render():
    """Build the Opening Scalp page: version-polling ``cache:options:scalp``."""
    import bus_client
    import shell as _shell
    from nicegui import run, ui

    from pages import copy as _copy
    from pages import ui_kit as kit
    from pages.ui_guard import guard, guard_async
    from pages.view_watch import watch_view

    # ``view`` is the last one read; ``sig`` what the clock said when it was painted;
    # ``open`` the detail sections the reader has open, kept across repaints;
    # ``countdown`` the label the clock tick refreshes.
    state = {"refresh": None, "view": None, "sig": None, "open": set(),
             "countdown": None}

    with kit.page():
        head = kit.header("Opening Scalp", view=VIEW)
        notice = ui.label("").classes(f"text-sm {_t.LABEL}")
        body = ui.column().classes("w-full gap-4")

    @guard
    def _on_refresh():
        try:
            bus_client.request("options", {"type": "scalp_refresh"})
        except Exception:  # noqa: BLE001 - say so; the page keeps the last call
            kit.set_busy(state["refresh"], False)
            kit.toast("error", "Could not ask for a refresh.")
            return
        kit.set_busy(state["refresh"])
        kit.toast("info", "Refresh requested. A new call appears here when it is built.")

    if _shell.may_enqueue():
        with head.actions:
            state["refresh"] = kit.button(
                "Refresh", icon="refresh", on_click=_on_refresh,
                tooltip="Rebuild the call. A call already shown today stays as published; "
                        "this fills in any symbol still waiting.")

    @guard
    def _toggle(key, is_open):
        (state["open"].add if is_open else state["open"].discard)(key)

    @guard
    def _paint(view):
        body.clear()
        now = _clock()
        state["view"], state["sig"] = view, clock_signature(view, now)
        line = state_line(view, now)
        notice.text = line or ""
        notice.set_visibility(bool(line))
        with body:
            state["countdown"] = _glance(view, now)
            if state["countdown"] is None:
                kit.empty(_copy.WAITING_OPTIONS)
            _details(view, now, state["open"], _toggle)

    @guard_async
    async def _changed():
        view = await run.io_bound(bus_client.read, VIEW)
        _paint(view)
        if state["refresh"] is not None:
            kit.set_busy(state["refresh"], False)

    @guard
    def _tick():
        """Repaint from the last view when only the CLOCK moved: a latest call goes
        past (or the day rolls) with no new view, e.g. a stalled service. Painted
        only when the clock-derived text changed, so an open table is not rebuilt
        every tick."""
        if state["view"] is None:
            return
        now = _clock()
        if clock_signature(state["view"], now) != state["sig"]:
            _paint(state["view"])
        elif state["countdown"] is not None:
            state["countdown"].text = countdown_text(state["view"], now)

    _paint(bus_client.read(VIEW))
    watch_view(VIEW, _changed)
    ui.timer(CLOCK_TICK_SEC, _tick)
