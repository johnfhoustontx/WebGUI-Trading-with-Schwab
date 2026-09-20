"""The page kit: one look and one behaviour for every screen (Tier-1).

Every page builds its header line, control bar, fields, buttons, loading
region, table, empty state, confirm dialog and toast from here, so two screens
cannot drift apart. The standard - and why each rule is what it is - is
``docs/plans/2026-09-19-app-ui-consistency-design.md``.
``tests/test_ui_kit_guard.py`` fails when a page builds a button, dialog, toast
or table of its own, or loads its own font; its ``ALLOWED`` ratchet names the
pages not yet migrated, and a count there may only fall.

Tier-1 safe: imports ``nicegui``, the theme, the busy spinner, the Symbol-field
helpers, ``bus_client`` and ``shell`` - nothing outside the allow-list - so the
public live process can render a page built from it. Decisions are PURE
module-level functions (``freshness``, ``toast_args``, ``button_classes``,
``table_columns``), unit-tested without a browser; the builders stay thin.
"""
import contextlib
import datetime as _dt
import inspect
import logging
import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from nicegui import run, ui

import bus_client
import shell
from pages import busy as _busy
from pages.options import theme as _t
from pages.options.inputs import (bind_symbol_load, mark_symbol_loaded,
                                  select_all_on_focus)
from pages.ui_guard import guard, guard_async

CT = ZoneInfo("America/Chicago")
_log = logging.getLogger(__name__)

# ── freshness: the header's "Updated" stamp ─────────────────────────────────
WAITING_TEXT = "Waiting for data"
FRESHNESS_CLASS = {"waiting": _t.MUTED, "fresh": _t.MUTED, "stale": _t.TXT_WARN}


def _parse_ts(ts):
    """An ISO stamp as an aware datetime, or None. A naive stamp is UTC: the bus
    writes ``datetime.now(timezone.utc).isoformat()``."""
    if not ts:
        return None
    try:
        when = _dt.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when


def freshness(ts, now, stale_after_sec=None):
    """``(text, state)`` for a view's last-confirmed stamp. PURE.

    ``state`` is ``"waiting"`` (nothing published - never a made-up time),
    ``"fresh"`` or ``"stale"`` (older than ``stale_after_sec``). A ``None``
    threshold means the view is not due to publish now, so its age says
    nothing and it is never stale. Central time everywhere; the day is named
    when it is not today's."""
    when = _parse_ts(ts)
    if when is None:
        return WAITING_TEXT, "waiting"
    if now.tzinfo is None:          # read like a stamp: naive means UTC
        now = now.replace(tzinfo=_dt.timezone.utc)
    local = when.astimezone(CT)
    clock = local.strftime("%I:%M %p").lstrip("0")
    if local.date() != now.astimezone(CT).date():
        clock = f"{local.strftime('%b')} {local.day} {clock}"
    clock += " CT"
    if stale_after_sec is not None and (now - when).total_seconds() > stale_after_sec:
        return f"Stale · updated {clock}", "stale"
    return f"Updated {clock}", "fresh"


def _stale_after(view, now):
    """The nav badge's own per-view threshold, so the stamp and the badge agree.
    ``alerts`` is imported lazily: it imports the scanner page, which imports
    this kit."""
    import alerts
    if not alerts.expects_updates(view, now):
        return None
    return alerts.stale_after(view, now)


# ── toasts ───────────────────────────────────────────────────────────────────
TOAST_POSITION = "bottom"
_TOAST = {"info": ("info", 4000), "ok": ("positive", 4000),
          "warn": ("warning", 8000), "error": ("negative", 8000)}


def toast_args(kind, text):
    """The ``ui.notify`` arguments for a toast. PURE. One position, a type
    always, and a longer life for anything the reader has to act on."""
    if kind not in _TOAST:
        raise ValueError(f"unknown toast kind {kind!r}; use one of {sorted(_TOAST)}")
    qtype, timeout = _TOAST[kind]
    return {"message": text, "type": qtype, "position": TOAST_POSITION,
            "timeout": timeout, "multi_line": len(text) > 80}


def toast(kind, text):
    """Report the OUTCOME of an action. Validation is shown inline, waiting is
    shown by a spinner - neither is a toast."""
    ui.notify(**toast_args(kind, text))


# ── buttons ──────────────────────────────────────────────────────────────────
# Four kinds for pages: primary (one per area), secondary (everything else),
# danger (outline), quiet (text only). danger_solid is the confirm dialog's own.
BUTTON_KINDS = ("primary", "secondary", "danger", "quiet", "danger_solid")
_KIND_TOKEN = {"primary": "BTN_PRIMARY", "secondary": "BTN", "danger": "BTN_DANGER",
               "quiet": "BTN_QUIET", "danger_solid": "BTN_DANGER_SOLID"}
BUSY_TIMEOUT_SEC = _busy.BUSY_TIMEOUT_SEC


def button_classes(kind, tokens=None):
    """The class string for a button kind. PURE. ``tokens`` lets the Appearance
    preview draw with unsaved colours; pages never pass it."""
    if kind not in _KIND_TOKEN:
        raise ValueError(f"unknown button kind {kind!r}; use one of {BUTTON_KINDS}")
    return (tokens or _t._TOKENS)[_KIND_TOKEN[kind]]


def button(text, *, kind="secondary", icon=None, on_click=None, tooltip=None,
           tokens=None):
    """A labelled button: sentence case, verb first, one of the four kinds."""
    b = ui.button(text, icon=icon, color=None, on_click=on_click) \
        .props("no-caps unelevated").classes(button_classes(kind, tokens))
    if tooltip:
        with b:
            # The width is a CLASS: as a prop it becomes an inline style, which
            # the app's Tailwind-only standard bans.
            ui.tooltip(tooltip).props("delay=350").classes("max-w-[340px]")
    return b


def icon_button(icon, *, tooltip, on_click=None):
    """An icon-only button (per row, per panel). The tooltip is REQUIRED: an
    icon alone does not say what it does."""
    b = ui.button(icon=icon, color=None, on_click=on_click) \
        .props("flat round dense size=sm").classes(_t.MUTED)
    with b:
        # Capped like every other tooltip, and this is the one that runs long:
        # with no label beside it, it carries the whole explanation.
        ui.tooltip(tooltip).props("delay=350").classes("max-w-[340px]")
    return b


def _busy_state(btn):
    """One backstop timer per button, created on first use and reused, so a
    long session does not accumulate timers (the busy.py reasoning)."""
    st = getattr(btn, "_kit_busy", None)
    if st is None:
        st = {"deadline": None}

        def _tick():
            if st["deadline"] is not None and time.monotonic() >= st["deadline"]:
                set_busy(btn, False)

        st["tick"] = _tick
        with btn.parent_slot:
            st["timer"] = ui.timer(1.0, guard(_tick), active=False)
        btn._kit_busy = st
    return st


def set_busy(btn, busy=True, *, timeout=BUSY_TIMEOUT_SEC):
    """Show a button's own spinner and hold it disabled until the result lands
    (``set_busy(btn, False)``) or ``timeout`` passes - no double submits, and no
    button left spinning when the answer never comes.

    Releasing re-applies the button's ``gate`` if it has one, rather than
    enabling outright: the answer landing must not hand back a Go that the
    fields do not currently allow."""
    st = _busy_state(btn)
    if busy:
        btn.props(add="loading")
        btn.disable()
        st["deadline"] = time.monotonic() + timeout
        st["timer"].active = True
    else:
        btn.props(remove="loading")
        st["deadline"] = None          # cleared FIRST: the gate reads it
        st["timer"].active = False
        release = getattr(btn, "_kit_gate", None)
        if release is not None:
            release()
        else:
            btn.enable()


# ── page frame, header line, status line, notice ────────────────────────────
TITLE = f"text-h6 font-semibold {_t.LABEL}"


PAGE_WIDTHS = ("full", "form")


def page(width="full"):
    """The page column. ``"form"`` caps a settings-style page at a readable
    width; ``"full"`` is full width. A typo raises rather than silently
    choosing one of them."""
    if width not in PAGE_WIDTHS:
        raise ValueError(f"unknown page width {width!r}; use one of {PAGE_WIDTHS}")
    return ui.column().classes(
        "w-full gap-4" if width == "full" else "w-full max-w-3xl gap-4")


def header(title, *, view=None, stale=False, poll_sec=5.0, _now=None):
    """The page's one header line: the title left; the Updated stamp and then
    the page actions right - add the primary action LAST so it sits rightmost.

    ``view`` is the bus view the page's data comes from; its ``:ts`` side key
    (the time the publisher last confirmed it current) drives the stamp, read
    off the loop every ``poll_sec``. No view, no stamp. ``stale=True`` only for
    a view published on a SCHEDULE: it then turns amber past the nav badge's
    own threshold (``alerts.stale_after``). An on-demand or once-a-day view
    leaves it off and is never called stale - its age says nothing. On the
    PUBLIC origin the title is omitted - live_main names the screen itself."""
    now_fn = _now or (lambda: _dt.datetime.now(_dt.timezone.utc))
    with ui.row().classes("w-full items-center gap-3 flex-wrap min-h-[38px]") as row:
        title_lbl = None if shell.is_public() else ui.label(title).classes(TITLE)
        ui.space()
        stamp = ui.label("").classes(f"text-xs {_t.MUTED}")
        stamp.set_visibility(view is not None)
        actions = ui.row().classes("items-center gap-2 no-wrap")
    state = {"cls": _t.MUTED, "ts": None, "warned": False}

    def set_stamp(ts, stale_after_sec=None, now=None):
        now = now or now_fn()
        text, st = freshness(ts, now, stale_after_sec)
        stamp.text = text
        cls = FRESHNESS_CLASS[st]
        if cls != state["cls"]:
            stamp.classes(remove=state["cls"], add=cls)
            state["cls"] = cls

    @guard_async
    async def poll():
        # The fallback keys on whether the READ worked, never on whether it
        # carried a time. A read that fails must not FREEZE the stamp - the last
        # good time keeps advancing towards stale, so a dead bus reads as falling
        # behind rather than as a view that is quietly fine. But a read that
        # SUCCEEDS and finds nothing is a real absence (a TTL expiry, a flush, a
        # renamed view), and showing the old time there would be exactly the
        # made-up reading this stamp promises never to give.
        ts, ok = None, False
        try:
            meta = await run.io_bound(bus_client.read_meta, view)
            ok = meta is not None     # None = the app is stopping, not an empty view
            ts = meta[1] if meta else None
        except Exception:         # noqa: BLE001 - one warning, then keep polling
            if not state["warned"]:
                state["warned"] = True
                _log.warning("ui_kit header: cannot read %s; showing the last "
                             "stamp and letting it age", view, exc_info=True)
        if ok:
            state["ts"] = ts          # believe an empty read
            state["warned"] = False   # so a SECOND outage is logged as well
        else:
            ts = state["ts"]
        now = now_fn()
        set_stamp(ts, _stale_after(view, now) if stale else None, now)

    if view is not None:
        # ONE timer: a repeating ui.timer fires immediately once the client
        # connects, so a 0.1s once-timer beside it just read the key twice.
        ui.timer(poll_sec, poll)
    return SimpleNamespace(row=row, title=title_lbl, stamp=stamp, actions=actions,
                           set_stamp=set_stamp, poll=poll if view is not None else None)


def status_line(text=""):
    """The one line of counts a board shows above its table ("12 trades ·
    3 open"). The time lives in the header stamp, never here."""
    return ui.label(text).classes(_t.EYEBROW)


_S = _t.THEME["semantic"]
NOTICE = (f"w-full items-center gap-3 rounded-[10px] px-3 py-2 "
          f"bg-[{_S['warning']}]/10 border border-[{_S['warning']}]/30")


def notice(text, *, icon="info"):
    """A one-line notice across the page (a pending restart, a service note).
    Returns the row, so a caller may add a button inside ``with notice(...)``."""
    with ui.row().classes(NOTICE) as row:
        ui.icon(icon).classes(_t.TXT_WARN)
        ui.label(text).classes(f"text-sm grow {_t.LABEL}")
    return row


# ── control bar and fields ──────────────────────────────────────────────────
FIELD_PROPS = "dense hide-bottom-space"


def control_bar():
    """The card that holds a page's fields: left to right, labels above, and
    the Go button right after the last field (``with control_bar(): ...``)."""
    return ui.row().classes(f"{_t.CARD} w-full items-end gap-x-4 gap-y-2 flex-wrap")


@contextlib.contextmanager
def field(label, *, grow=False):
    """A labelled slot: the label ABOVE (never floating, never placeholder-only),
    then whatever control the caller builds inside it."""
    with ui.column().classes("gap-1 w-full" if grow else "gap-1") as col:
        ui.label(label).classes(_t.EYEBROW)
        yield col


def text_field(label, *, value="", placeholder="", width="w-40", on_change=None):
    """A labelled text input. A placeholder is an example value, never the label."""
    with field(label, grow=width == "w-full"):
        inp = ui.input(value=value, placeholder=placeholder or None,
                       on_change=on_change).props(FIELD_PROPS).classes(width)
    return inp


def select_field(label, options, *, value=None, width="w-40", on_change=None, **kw):
    """A labelled dropdown. A filter on what is on screen applies on change."""
    with field(label, grow=width == "w-full"):
        sel = ui.select(options, value=value, on_change=on_change, **kw) \
            .props(f"{FIELD_PROPS} options-dense").classes(width)
    return sel


def field_valid(f):
    """Whether a field's own validation passes - WITHOUT painting an error.

    ``validate()`` is the wrong tool for asking: it SHOWS the first failing
    message, so using it to decide whether Go is live turns an untouched form
    red; and on a field with no validators it sets ``error = None``, which wipes
    a message the page put there by hand (a Symbol field's "No such ticker").

    Not pure - it calls the field's own validators, which are the page's code.
    Anything it cannot ANSWER counts as valid: a rule that raises, and an async
    rule, which has no synchronous answer to give. Gating is the wrong place to
    decide a field is bad, and the wrong place to take the page down - this runs
    inside ``render()`` and again on every value change, so one bad rule would
    mean the page never renders at all. The field's own blur ``validate()``
    still surfaces the real error underneath."""
    rules = getattr(f, "validation", None)
    if rules is None:
        return True
    value = getattr(f, "value", None)
    try:
        if isinstance(rules, dict):
            return all(check(value) for check in rules.values())
        result = rules(value)
    except Exception:     # noqa: BLE001 - a page's rule may not break gating
        return True
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)   # never leave it un-awaited
        if callable(close):
            close()
        return True                              # an async rule cannot answer here
    return result is None


def _clear_error_once_valid(f):
    """Drop a message the field has outgrown. Without this the field stays red
    over a good number until the reader blurs it a second time."""
    if f.error is not None and field_valid(f):
        f.error = None


def number_field(label, *, value=None, min=None, max=None, step=None, integer=False,
                 width="w-28", format=None, on_change=None):
    """A labelled number whose range is checked when you LEAVE it, not per
    keystroke; the message shows in red under the field. The range is not
    passed to Quasar: ``ui.number.sanitize`` clamps a value to the ``min``/``max``
    PROPS on blur, which silently rewrites what the reader typed instead of
    saying what was wrong with it. ``integer=True`` refuses a fraction.

    ⚠ ``integer`` must NOT pass ``precision=0`` for the same reason. ``sanitize``
    is registered on blur by ``ui.number`` itself, BEFORE the check added here,
    and a precision makes it ROUND: a typed 2.5 became 2 (and 3.7 became 4) with
    nothing said, which reaches a paper-trade Quantity. Without min/max props, a
    format or a precision, ``sanitize`` is a no-op and the typed value survives
    to be reported. A test that asks ``validate()`` cannot see this - it is the
    blur ORDER that decides, so that test fires the blur listeners."""
    checks = {"Enter a number": lambda v: v is not None}
    if integer:
        checks["Whole numbers only"] = \
            lambda v: v is None or float(v).is_integer()
    if min is not None:
        checks[f"At least {min:g}"] = lambda v, lo=min: v is None or v >= lo
    if max is not None:
        checks[f"At most {max:g}"] = lambda v, hi=max: v is None or v <= hi
    with field(label, grow=width == "w-full"):
        n = ui.number(value=value, step=step, format=format, on_change=on_change,
                      validation=checks).props(FIELD_PROPS).classes(width)
    n.without_auto_validation()
    n.on("blur", lambda _e: n.validate(return_result=False))
    n.on_value_change(lambda _e: _clear_error_once_valid(n))
    return n


def symbol_field(label="Symbol", *, value="", on_load, tab=True, width="w-[110px]"):
    """The one Symbol behaviour: uppercase; the whole ticker selected on click or
    tab-in; Enter loads; tab-out loads only a CHANGED symbol (the dedup is seeded
    from ``value``, so tabbing through the default is no load); an unknown
    ticker is reported under it (``symbol_error``). The page's Go button calls
    ``on_load`` directly, which always reloads."""
    with field(label):
        inp = ui.input(value=value) \
            .props(f"{FIELD_PROPS} spellcheck=false").classes(f"{width} font-semibold")
    select_all_on_focus(inp)
    bind_symbol_load(inp, on_load, tab=tab, enter_always=True)
    return inp


# A page that writes the Symbol field from code (a hand-off) marks it loaded.
symbol_loaded = mark_symbol_loaded


def symbol_error(inp, text=None):
    """Show - or, with ``None``, clear - the message under a Symbol field.

    Set ``inp.value`` BEFORE calling this: the field validates on change, so a
    value written afterwards clears the message you just put there.

    Reporting a message also forgets the symbol the dedup last fired on, so the
    reader can tab out again and retry the SAME ticker. Without that, a symbol
    the service rejected could never be retried from the field itself - it is
    still 'the symbol we loaded', and only the page's Go button would work."""
    inp.error = text or None
    last = getattr(inp, "_symbol_load_last", None)
    if text and last is not None:
        last["sym"] = ""


def gate(go, *fields):
    """Keep ``go`` disabled while any of ``fields`` fails its check. Returns the
    sync function, for a page that sets a value from code.

    The check is SILENT (``field_valid``) - holding Go is not the moment to turn
    an untouched form red; each field paints its own message on ITS blur. Runs
    on change as well as blur, so correcting a value hands Go back without a
    second blur, and never enables a button that is mid-command.

    ⚠ A field whose validation is an ASYNC function is treated as valid, since
    there is no synchronous answer to read: Go can be live for the moment it
    takes NiceGUI to paint the error underneath. Give a gated field synchronous
    rules - ``number_field`` does, and ``select_field`` forwards a
    ``validation=`` straight through, so this is reachable from a page."""
    def sync(_e=None):
        ok = [field_valid(f) for f in fields]     # a list: check EVERY field
        busy = getattr(go, "_kit_busy", None)
        if all(ok) and not (busy and busy["deadline"] is not None):
            go.enable()
        else:
            go.disable()
    for f in fields:
        f.on("blur", sync)
        if hasattr(f, "on_value_change"):
            f.on_value_change(sync)
    go._kit_gate = sync
    sync()
    return sync


# ── region, empty state, table ──────────────────────────────────────────────
# While the region is empty - the first load, and every repaint that clears it
# first - `absolute inset-0` resolves to a box of zero height, so the spinner is
# there and invisible. Reserved only while spinning, so a short region does not
# carry a hole under it afterwards.
REGION_MIN_H = "min-h-[120px]"


def region(text="Loading…", *, classes="w-full", timeout=_busy.BUSY_TIMEOUT_SEC):
    """A block whose contents a repaint replaces. Repaint ``content`` (clear and
    rebuild it); the spinner lives on ``outer``, so a clear can never delete it
    - the bug five pages had. ``busy.show()`` on first load and every refresh.

    ``show()`` also reserves ``REGION_MIN_H`` on ``outer`` and ``hide()`` gives
    it back, so the spinner has something to sit in while the region is empty;
    ``timeout`` is the backstop that hides it if the data never lands, and it
    releases the height too. Raise ``timeout`` for a region backing a
    legitimately slow fetch - a backstop that fires while the work is still
    running says "finished" when nothing is."""
    outer = ui.element("div").classes(classes)
    with outer:
        content = ui.column().classes("w-full gap-3")
    spin = _busy.build_busy(outer, text, timeout=timeout)

    def show(msg=None):
        outer.classes(add=REGION_MIN_H)
        spin.show(msg)

    def hide():
        outer.classes(remove=REGION_MIN_H)
        spin.hide()

    def tick():
        spin.tick()
        if not spin.visible():          # the backstop hid it: release the height
            outer.classes(remove=REGION_MIN_H)

    # The watchdog calls busy.py's OWN hide(), which knows nothing about the
    # height added here, so the timer runs this wrapper instead.
    spin.timer.callback = guard(tick)
    busy = SimpleNamespace(element=spin.element, label=spin.label, show=show,
                           hide=hide, visible=spin.visible, tick=tick,
                           timer=spin.timer)
    return SimpleNamespace(outer=outer, content=content, busy=busy)


EMPTY = f"w-full text-center text-[13px] {_t.MUTED} py-6"


def empty(text):
    """The one empty-state line. "Nothing published yet" (copy.WAITING_*) and
    "nothing to report" stay worded differently - the caller picks the words."""
    return ui.label(text).classes(EMPTY)


TABLE_PROPS = "dense flat"
# Composes a page's own ``_row_class`` (e.g. the scanner's stale dimming) with
# the selected-row accent, so neither has to give way.
ROW_CLASS_FN = ("row => [row._row_class, row._selected ? 'kit-row-selected' : '']"
                ".filter(Boolean).join(' ')")


def table_columns(columns, *, numeric=()):
    """Column defaults. PURE - returns new dicts. Every data column sortable
    (``actions`` never; an explicit ``sortable: False`` stays); ``numeric``
    columns right-aligned, the rest left."""
    out = []
    for col in columns:
        c = dict(col)
        if c.get("name") == "actions":
            c["sortable"] = False
        else:
            c.setdefault("sortable", True)
        c["align"] = "right" if c.get("name") in numeric else c.get("align", "left")
        out.append(c)
    return out


def mark_selected(rows, row_id, *, key="id"):
    """Stamp ``_selected`` so exactly the clicked row carries the accent."""
    for r in rows:
        r["_selected"] = row_id is not None and r.get(key) == row_id
    return rows


def _pagination(rows_per_page, rows_number):
    """Quasar's ``pagination`` v-model. PURE.

    ⚠ ``rows_number`` is the key that puts Quasar in SERVER mode, and nothing
    else does: with it, a page or sort click emits ``request`` and the page
    answers with that page's rows; without it Quasar pages CLIENT-side over the
    rows it was already given. So a page that deliberately holds rows back must
    pass BOTH, and server mode with no page size would send every row - the one
    thing asking for it means to avoid - so that combination raises rather than
    quietly doing the opposite of what was asked."""
    if rows_number is None:
        return {"rowsPerPage": rows_per_page} if rows_per_page else None
    if not rows_per_page:
        raise ValueError("rows_number is server-side paging and needs a "
                         "rows_per_page to page BY; rows_per_page=0 sends every row")
    return {"sortBy": None, "descending": False, "page": 1,
            "rowsPerPage": rows_per_page, "rowsNumber": rows_number}


def table(columns, rows=None, *, row_key="id", numeric=(), rows_per_page=0,
          rows_number=None, classes="w-full"):
    """The one table: dense, flat, sticky header (the app-wide ``TABLE_CSS``),
    numbers right-aligned, sortable columns, and the selected row drawn from
    ``_selected`` (``mark_selected``). ``rows_per_page=0`` shows every row - and
    hides the "Records per page" footer with it, which otherwise sits under a
    table that has no pages.

    ``rows_number`` is the WHOLE list's length and switches to SERVER-side
    paging (see :func:`_pagination`): the page then answers the table's
    ``request`` event with that page's rows and a fresh ``rowsNumber``. Only a
    page whose full list is too big to ship needs it - the Strategy Finder sends
    50 rows of up to ~510, ~190 KB against ~1.96 MB."""
    t = ui.table(columns=table_columns(columns, numeric=numeric),
                 rows=list(rows or []), row_key=row_key,
                 pagination=_pagination(rows_per_page, rows_number)) \
        .classes(classes).props(TABLE_PROPS)
    # Written to _props directly: a props STRING would be re-parsed and mangle
    # the quotes inside the arrow function (the scanner.py precedent).
    t._props[":table-row-class-fn"] = ROW_CLASS_FN
    return t


# ── confirm dialog ──────────────────────────────────────────────────────────
# ``ns-app`` on the card itself: a dialog is teleported to <body>, outside the
# shell's content column, so without it a field in ``content`` would be stock.
# The width is ONE rule rather than a min/max pair: a 360px minimum plus the
# dialog's own margin overflows a narrow phone, and this app is used from one.
CONFIRM_CARD = f"ns-app {_t.CARD} w-[min(520px,calc(100vw-48px))] gap-3"

# Enter confirms - but only a real one. Auto-repeat would re-fire it while the
# key is held, an IME commit (``isComposing``) is the reader choosing a
# character rather than confirming, and Enter inside a textarea is a newline.
# Quasar focuses ``q-dialog__inner`` - the CARD'S PARENT - when a dialog opens,
# so this must be listened for on the DIALOG; on the card it never fires.
CONFIRM_ENTER_JS = ("(e) => { if (!e.repeat && !e.isComposing && "
                    "e.target.tagName !== 'TEXTAREA') emit(); }")


def confirm(title, body="", *, confirm_text, on_confirm, danger=False,
            ephemeral=False):
    """The one confirm dialog: a title, one sentence, then Cancel and the
    confirm, right-aligned in that order. Enter confirms, Esc cancels. A
    destructive action passes ``danger=True`` - the only place solid red appears.

    Add any inputs to ``handle.content`` before ``open()``. ``on_confirm`` runs
    first - it may be a COROUTINE function, which is awaited; returning
    ``False`` keeps the dialog open (a check that failed), and anything else
    closes it. It runs at most once per open, and a trigger arriving WHILE it
    runs is ignored, so a click followed by a queued Enter cannot act twice.
    ⚠ ``handle.run`` is therefore a COROUTINE function: a caller firing it
    itself (rather than through the button or Enter) must await it, or the
    action simply never happens.
    Build the dialog at the page's own level, never inside a container a repaint
    clears - a dialog deletes itself with its slot (the swing.py precedent).
    Build it ONCE and retitle it per use (``handle.title.text``,
    ``handle.body.text``) rather than a new dialog per click, which would leave
    one behind in the page each time; a caller that genuinely builds one per
    click passes ``ephemeral=True`` and it deletes itself once closed. ⚠ Such a
    handle is SINGLE-USE - reopening it after it closes reaches a deleted
    element, which NiceGUI answers with a warning and no dialog."""
    with ui.dialog() as dlg, ui.card().classes(CONFIRM_CARD):
        title_lbl = ui.label(title).classes(f"text-subtitle1 font-semibold {_t.LABEL}")
        body_lbl = ui.label(body).classes(f"text-sm {_t.MUTED}")
        body_lbl.set_visibility(bool(body))
        content = ui.column().classes("w-full gap-2")
        with ui.row().classes("w-full justify-end gap-2 pt-1") as actions:
            cancel = button("Cancel", kind="secondary", on_click=dlg.close)
            ok = button(confirm_text, kind="danger_solid" if danger else "primary")
    st = {"done": False, "running": False}

    @guard_async
    async def run_(_e=None):
        # ``running`` as well as ``done``: an awaited action leaves a window in
        # which ``done`` is not set yet, and a queued Enter lands squarely in it.
        if st["done"] or st["running"] or not ok.enabled:
            return
        st["running"] = True
        try:
            result = on_confirm()
            if inspect.isawaitable(result):
                result = await result
        finally:
            st["running"] = False
        if result is False:
            return
        st["done"] = True
        dlg.close()

    @guard
    def _on_toggle(e):
        # On the DIALOG's own value, so a page that opens ``handle.dialog``
        # directly gets a live confirm rather than a spent one.
        if e.value:
            st["done"] = False
            body_lbl.set_visibility(bool(body_lbl.text))
        elif ephemeral:
            dlg.delete()

    def open_():
        dlg.open()

    dlg.on_value_change(_on_toggle)
    ok.on_click(run_)
    dlg.on("keydown.enter", run_, js_handler=CONFIRM_ENTER_JS)
    return SimpleNamespace(dialog=dlg, title=title_lbl, content=content, body=body_lbl,
                           actions=actions, cancel=cancel, confirm=ok, run=run_,
                           open=open_, close=dlg.close)


# ── information dialog, section title ──────────────────────────────────────
def info_dialog(title, *, width="w-[720px]"):
    """An information dialog: a title, a close ✕ top-right, no footer (the
    standard - a dialog that ASKS is ``confirm``). Put the body in
    ``handle.content``; build it once and repaint the content per use."""
    # ns-app on the card: a dialog is teleported outside the shell's column.
    with ui.dialog() as dlg, ui.card().classes(f"ns-app {_t.CARD} {width} max-w-full gap-3"):
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            title_lbl = ui.label(title).classes(f"text-subtitle1 font-semibold {_t.LABEL}")
            ui.space()
            icon_button("close", tooltip="Close", on_click=dlg.close)
        content = ui.column().classes("w-full gap-3")
    return SimpleNamespace(dialog=dlg, title=title_lbl, content=content,
                           open=dlg.open, close=dlg.close)


def section_title(text):
    """A heading inside a page ("Open positions", "Analytics")."""
    return ui.label(text).classes(f"text-subtitle2 font-semibold {_t.LABEL}")
