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

Pure helpers (``role_rows``, ``headline``, ``input_rows``, ``state_line`` ...)
carry every fact; ``render`` holds widgets and wiring. Tier 1: this reads ONE
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

ROLE_ORDER = ("long_with", "long_against", "credit_with", "credit_against")

_CT = ZoneInfo("America/Chicago")

NO_EDGE = "No measured edge"
CAUTION = "Caution: measured to lose more often than usual"
EDGE = "Measured edge"
SCORECARD_EMPTY = "No graded days yet."

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
            "interval": _interval(role.get("lo"), role.get("hi")),
            "n": _count(role.get("n")), "n_base": _count(role.get("n_base")),
            "samples": f"{_count(role.get('n'))} / {_count(role.get('n_base'))}",
            "strikes": _strikes(role.get("strikes")), "entry": _entry_text(role),
            "target": _points(role.get("win_points")),
            "stop": _points(role.get("loss_points")),
            "_row_class": _ROW_CLASS[tone], "_mark_class": _MARK_CLASS[tone],
            "_rate_class": _RATE_CLASS[tone]})
    return rows


def role_columns():
    """Seven columns, so the table fits a phone with little sideways scroll: the
    mark sits under the trade's name and the interval under its rate."""
    return [
        {"name": "trade", "label": "Trade", "field": "trade", "align": "left"},
        {"name": "usual", "label": "Usual win rate", "field": "usual"},
        {"name": "rate", "label": "This morning's rate", "field": "rate"},
        {"name": "samples", "label": "Samples (bucket / all)", "field": "samples"},
        {"name": "strikes", "label": "Strikes", "field": "strikes"},
        {"name": "entry", "label": "Entry", "field": "entry"},
        {"name": "target", "label": "Underlying move", "field": "target"},
    ]


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


# ── context: not in the probability ──────────────────────────────────────────
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

_CARD = f"w-full {_t.CARD} gap-3"
_SMALL = f"text-[12px] {_t.MUTED}"


def _symbol_card(view, name, sym, now=None):
    from nicegui import ui

    from pages import ui_kit as kit

    rows = role_rows(sym)
    has_edge = sym.get("highlight") in {r["id"] for r in rows}
    with ui.column().classes(_CARD):
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            ui.label(name).classes(f"text-lg font-semibold {_t.LABEL}")
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
            table = kit.table(role_columns(), rows,
                              numeric=("usual", "rate", "samples", "strikes", "entry",
                                       "target"))
            table.add_slot("body-cell-trade", _TRADE_SLOT)
            table.add_slot("body-cell-usual", _USUAL_SLOT)
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


def _context_panel(context):
    from nicegui import ui

    from pages import ui_kit as kit

    with ui.column().classes(_CARD):
        kit.section_title("Not in the probability")
        ui.label("Shown beside the call for context. None of this was measured or "
                 "fed into the win rates above.").classes(_SMALL)
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
        kit.section_title("Scorecard")
        if not cards:
            ui.label(SCORECARD_EMPTY).classes(_SMALL)
            return
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

    state = {"refresh": None}

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

    def _paint(view):
        body.clear()
        now = _clock()
        line = state_line(view, now)
        notice.text = line or ""
        notice.set_visibility(bool(line))
        symbols = _as_dict(_as_dict(view).get("symbols"))
        with body:
            if not symbols:
                kit.empty(_copy.WAITING_OPTIONS)
            for name, sym in symbols.items():
                if isinstance(sym, dict):
                    _symbol_card(view, name, sym, now)
            _context_panel(_as_dict(view).get("context"))
            _scorecard_panel(_as_dict(view).get("scorecard"))

    @guard_async
    async def _changed():
        view = await run.io_bound(bus_client.read, VIEW)
        _paint(view)
        if state["refresh"] is not None:
            kit.set_busy(state["refresh"], False)

    _paint(bus_client.read(VIEW))
    watch_view(VIEW, _changed)
