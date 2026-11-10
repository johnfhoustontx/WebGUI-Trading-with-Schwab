"""The "New trade idea" banner under the header (live-order design, Phase 1).

When the hourly trade idea posts and is booked into the Paper Ledger,
``cache:options:trade_idea`` moves. Every private page's ``_layout`` mounts this
banner, which offers **Open order ticket** (the ticket for ``trade=<id>``, as a
window over the page the operator is on) and **Dismiss**. It never sends anything itself.

**In the app only.** Nothing goes to Telegram, Discord, X, ntfy or the site: a
ticket link in a chat with a wide audience would show who acts on which idea.
It is mounted by ``main._layout`` alone, so the public process never draws it.

Shown only while ``config/live_orders.toml`` has live orders not off and
``[prompt] banner`` on, and only for an idea younger than
``[prompt] banner_max_age_min``. A dismissed idea stays dismissed for this
process (a single-user app, so every tab of the one operator).
"""
from __future__ import annotations

import datetime as _dt
from collections import OrderedDict

from nicegui import ui

import bus_client
from pages import send_order
from pages import ui_kit as kit
from pages.options import theme as _t
from pages.ui_guard import guard
from pages.view_watch import watch_view
from shared import live_order, live_orders_config

VIEW = "options:trade_idea"

#: Trade ids dismissed by the operator, newest last; the oldest are forgotten.
_DISMISSED: "OrderedDict[str, None]" = OrderedDict()
_DISMISSED_MAX = 64


def dismiss(trade_id: str) -> None:
    _DISMISSED[trade_id] = None
    _DISMISSED.move_to_end(trade_id)
    while len(_DISMISSED) > _DISMISSED_MAX:
        _DISMISSED.popitem(last=False)


def _expiry_words(raw) -> str:
    try:
        d = _dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return ""
    return f"{d.day} {d.strftime('%b %y')}"


def banner_for(payload, now: _dt.datetime, *, mode: str, prompt: dict,
               dismissed=()) -> dict | None:
    """``{trade_id, text, href}`` for the banner, or None. PURE.

    ``now`` is timezone-aware; the view's ``at`` is the post time."""
    if mode == "off" or not prompt.get("banner"):
        return None
    if not isinstance(payload, dict) or payload.get("status") != "posted":
        return None
    ledger = payload.get("ledger") or {}
    trade_id = ledger.get("trade_id") if isinstance(ledger, dict) else None
    if ledger.get("status") != "booked" or not isinstance(trade_id, str) \
            or not live_order.TRADE_ID_RE.match(trade_id) or trade_id in dismissed:
        return None
    try:
        at = _dt.datetime.fromisoformat(str(payload.get("at")))
    except ValueError:
        return None
    if at.tzinfo is None:
        return None
    age_min = (now - at).total_seconds() / 60
    if age_min < 0 or age_min > prompt.get("banner_max_age_min", 30):
        return None
    idea = payload.get("idea") or {}
    name = idea.get("label") or " ".join(
        str(x) for x in (idea.get("symbol"), str(idea.get("type") or "")
                         .replace("_", " ").title()) if x)
    expiry = _expiry_words(idea.get("expiration"))
    text = f"New trade idea: {name}" + (f", {expiry}" if expiry else "")
    return {"trade_id": trade_id, "text": text, "href": f"/send-order?trade={trade_id}"}


def render() -> None:
    """Mount the banner; it repaints when the idea view moves."""
    box = ui.row().classes("w-full")

    @guard
    def _paint():
        got = banner_for(bus_client.read(VIEW), _dt.datetime.now(_dt.timezone.utc),
                         mode=live_orders_config.mode(),
                         prompt=live_orders_config.prompt(), dismissed=_DISMISSED)
        box.clear()
        if got is None:
            box.set_visibility(False)
            return
        box.set_visibility(True)
        with box, ui.row().classes(
                f"{_t.CARD} w-full items-center gap-3 flex-wrap"):
            ui.icon("lightbulb").classes(_t.TXT_WARN)
            ui.label(got["text"]).classes(f"text-sm font-semibold {_t.LABEL}")
            ui.space()
            kit.button("Open order ticket", kind="primary", icon="send",
                       on_click=lambda: send_order.open_window(trade=got["trade_id"]))
            kit.button("Dismiss", kind="secondary", icon="close",
                       on_click=lambda: (dismiss(got["trade_id"]), _paint()))

    _paint()
    watch_view(VIEW, _paint, interval=5.0)
    # The view moves only when an idea posts; this drops one that has aged out.
    ui.timer(60.0, _paint)
