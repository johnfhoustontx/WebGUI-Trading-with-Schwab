"""The page kit: one look and one behaviour for every screen (Tier-1).

Every page builds its header line, control bar, fields, buttons, loading
region, table, empty state, confirm dialog and toast from here, so two screens
cannot drift apart. The standard - and why each rule is what it is - is
``docs/plans/2026-09-19-app-ui-consistency-design.md``;
``tests/test_ui_kit_guard.py`` fails when a page builds a button, dialog, toast
or table of its own.

Tier-1 safe: imports ``nicegui``, the theme, the busy spinner, the Symbol-field
helpers, ``bus_client`` and ``shell`` - nothing outside the allow-list - so the
public live process can render a page built from it. Decisions are PURE
module-level functions (``freshness``, ``toast_args``, ``button_classes``,
``table_columns``), unit-tested without a browser; the builders stay thin.
"""
import contextlib
import datetime as _dt
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
            ui.tooltip(tooltip).props("delay=350 max-width=340px")
    return b


def icon_button(icon, *, tooltip, on_click=None):
    """An icon-only button (per row, per panel). The tooltip is REQUIRED: an
    icon alone does not say what it does."""
    b = ui.button(icon=icon, color=None, on_click=on_click) \
        .props("flat round dense size=sm").classes(_t.MUTED)
    with b:
        ui.tooltip(tooltip).props("delay=350")
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
    button left spinning when the answer never comes."""
    st = _busy_state(btn)
    if busy:
        btn.props(add="loading")
        btn.disable()
        st["deadline"] = time.monotonic() + timeout
        st["timer"].active = True
    else:
        btn.props(remove="loading")
        btn.enable()
        st["deadline"] = None
        st["timer"].active = False


# ── page frame, header line, status line, notice ────────────────────────────
TITLE = f"text-h6 font-semibold {_t.LABEL}"


def page(width="full"):
    """The page column. ``"form"`` caps a settings-style page at a readable
    width; everything else is full width."""
    return ui.column().classes(
        "w-full gap-4" if width == "full" else "w-full max-w-3xl gap-4")


def header(title, *, view=None, stale=False, poll_sec=5.0):
    """The page's one header line: the title left; the Updated stamp and then
    the page actions right - add the primary action LAST so it sits rightmost.

    ``view`` is the bus view the page's data comes from; its ``:ts`` side key
    (the time the publisher last confirmed it current) drives the stamp, read
    off the loop every ``poll_sec``. No view, no stamp. ``stale=True`` only for
    a view published on a SCHEDULE: it then turns amber past the nav badge's
    own threshold (``alerts.stale_after``). An on-demand or once-a-day view
    leaves it off and is never called stale - its age says nothing. On the
    PUBLIC origin the title is omitted - live_main names the screen itself."""
    with ui.row().classes("w-full items-center gap-3 flex-wrap min-h-[38px]") as row:
        title_lbl = None if shell.is_public() else ui.label(title).classes(TITLE)
        ui.space()
        stamp = ui.label("").classes(f"text-xs {_t.MUTED}")
        stamp.set_visibility(view is not None)
        actions = ui.row().classes("items-center gap-2 no-wrap")
    state = {"cls": _t.MUTED}

    def set_stamp(ts, stale_after_sec=None, now=None):
        now = now or _dt.datetime.now(_dt.timezone.utc)
        text, st = freshness(ts, now, stale_after_sec)
        stamp.text = text
        cls = FRESHNESS_CLASS[st]
        if cls != state["cls"]:
            stamp.classes(remove=state["cls"], add=cls)
            state["cls"] = cls

    if view is not None:
        @guard_async
        async def _poll():
            _ver, ts = await run.io_bound(bus_client.read_meta, view)
            now = _dt.datetime.now(_dt.timezone.utc)
            set_stamp(ts, _stale_after(view, now) if stale else None, now)

        ui.timer(0.1, _poll, once=True)
        ui.timer(poll_sec, _poll)
    return SimpleNamespace(row=row, title=title_lbl, stamp=stamp, actions=actions,
                           set_stamp=set_stamp)


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


def number_field(label, *, value=None, min=None, max=None, step=None,
                 width="w-28", format=None, on_change=None):
    """A labelled number whose range is checked when you LEAVE it, not per
    keystroke; the message shows in red under the field. The range is not
    passed to Quasar, which would silently clamp instead of saying so."""
    checks = {"Enter a number": lambda v: v is not None}
    if min is not None:
        checks[f"At least {min:g}"] = lambda v, lo=min: v is None or v >= lo
    if max is not None:
        checks[f"At most {max:g}"] = lambda v, hi=max: v is None or v <= hi
    with field(label, grow=width == "w-full"):
        n = ui.number(value=value, step=step, format=format, on_change=on_change,
                      validation=checks).props(FIELD_PROPS).classes(width)
    n.without_auto_validation()
    n.on("blur", lambda _e: n.validate(return_result=False))
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
    bind_symbol_load(inp, on_load, tab=tab)
    return inp


# A page that writes the Symbol field from code (a hand-off) marks it loaded.
symbol_loaded = mark_symbol_loaded


def symbol_error(inp, text=None):
    """Show - or, with ``None``, clear - the message under a Symbol field."""
    inp.error = text or None


def gate(go, *fields):
    """Keep ``go`` disabled while any of ``fields`` fails its check. Returns the
    sync function, for a page that sets a value from code."""
    def sync(_e=None):
        ok = [f.validate() for f in fields]      # a list: validate EVERY field
        if all(ok):
            go.enable()
        else:
            go.disable()
    for f in fields:
        f.on("blur", sync)
    sync()
    return sync
