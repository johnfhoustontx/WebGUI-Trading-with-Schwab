"""The after-hours price warning the public Rescue, Calculator and Simulator
share.

Each tool's window in ``config/sessions.toml`` (``rescue_public``,
``tools_public``) is when prices are LIVE. With ``after_hours = true`` the
service still answers outside it, on whatever Schwab returns after the close,
and the page says so: bid, ask and mark may be stale or incorrect. The service
decides what runs; this module only decides what the page SAYS, so every read
here degrades to "no warning" rather than raising.
"""
from __future__ import annotations

import datetime as dt

from nicegui import ui

from pages import copy
from pages import ui_kit as kit
from pages.ui_guard import guard

try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:  # noqa: BLE001 - no tz database: fall back to UTC-6
    CT = dt.timezone(dt.timedelta(hours=-6))

#: How often an open page re-checks, so the warning appears at the close and
#: goes at the open without a reload.
RECHECK_SEC = 60.0


def line(live_now: bool, allowed: bool, window) -> str:
    """The warning to show, or "" while prices are live or when the tool does
    not run after hours (the page then says it is closed instead)."""
    if live_now or not allowed:
        return ""
    return copy.AFTER_HOURS_PRICES.format(start=window["start"], end=window["end"])


def state(name: str, now=None) -> tuple[bool, bool]:
    """``(live_now, after_hours_allowed)`` for window ``name``. On any failure
    it answers "live": a missing warning is better than a false one."""
    try:
        from shared import market_calendar
        now = now or dt.datetime.now(CT)
        return (market_calendar.in_window(name, now),
                market_calendar.after_hours_allowed(name))
    except Exception:  # noqa: BLE001 - the words, not the gate; the service decides
        return True, False


def mount(name: str, window) -> ui.row:
    """A warning row that shows only outside the live window, re-checked every
    ``RECHECK_SEC`` so an open page follows the close and the open."""
    row = kit.notice("", icon="schedule")
    row.classes("public-after-hours")
    text = row.default_slot.children[-1]

    @guard
    def _refresh():
        msg = line(*state(name), window)
        text.text = msg
        row.set_visibility(bool(msg))

    _refresh()
    ui.timer(RECHECK_SEC, _refresh)
    return row
