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
