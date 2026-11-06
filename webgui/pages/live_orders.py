"""Today's Live Orders (/live-orders): every order the app sent to the Schwab
account since midnight Central, with Schwab's status and a Cancel for one still
working (live-order design, Phase 3).

The page asks and shows; the options service acts:

* on load and on Refresh it enqueues ``today``; the service reads each open
  order from Schwab and publishes ``live_order.TODAY_KEY`` (alias only, never
  an account number or hash). While an order is still working the page asks
  again every ``status_poll_sec``.
* **Cancel** (after a confirm) enqueues ``cancel {ticket_id}``. The service
  reads the order from Schwab first and cancels only when Schwab says it can be
  cancelled; once the opening order has filled, its exits are orders of their
  own, managed in ThinkorSwim.

PRIVATE only: the route is in ``main.py`` alone, never in ``live_screens``, and
``bus_client.request`` refuses on the public read-only process.
"""
from __future__ import annotations

import time

from nicegui import ui

import bus_client
from pages import fmt
from pages import ui_kit as kit
from pages.options import theme as _t
from pages.ui_guard import guard
from shared import live_order, live_orders_config

_STATUS_WORDS = {
    "sending": "Sending", "sent": "Sent", "unknown": "No answer from Schwab",
    "rejected": "Schwab refused it",
}

#: A row's status in one of three FIXED classes (never built at run time).
TONE_CLASS = {"open": _t.TXT_WARN, "done": _t.LABEL, "bad": _t.TXT_NEG}


#: Schwab's ``complexOrderStrategyType`` in words; ``NONE`` is one option.
STRUCTURE_WORDS = {"NONE": "single option"}


def _view(key: str) -> str:
    return key[len("cache:"):]


# ── pure helpers ────────────────────────────────────────────────────────────

def status_words(row) -> str:
    """``Sent: filled`` / ``No answer from Schwab`` for one row."""
    if not isinstance(row, dict):
        return ""
    words = _STATUS_WORDS.get(row.get("status"), str(row.get("status") or ""))
    if row.get("order_status"):
        words += f": {str(row['order_status']).replace('_', ' ').lower()}"
    return words


def tone(row) -> str:
    """``open`` while Schwab may still act on it, ``bad`` when refused or not
    known, ``done`` once Schwab says it is finished."""
    status = (row or {}).get("status")
    if status in ("rejected", "unknown"):
        return "bad"
    if status == "sent" and live_order.order_is_final(row.get("order_status")):
        return "bad" if str(row.get("order_status")).upper() == "REJECTED" else "done"
    return "open"


def order_line(row) -> str:
    """``10:02:11 · Trading account · SPY VERTICAL · 2 at 1.50 debit``."""
    sent = str(row.get("sent_at") or "")
    when = sent[11:19] if len(sent) >= 19 and sent[10] == "T" else "?"
    side = "debit" if row.get("debit") else "credit"
    shape = STRUCTURE_WORDS.get(row.get("structure"),
                                str(row.get("structure") or "").replace("_", " ").lower())
    limit = row.get("limit")
    price = "?" if limit is None else fmt.price(limit)
    return (f"{when} · {row.get('alias') or '?'} · {row.get('symbol') or '?'} "
            f"{shape} · "
            f"{row.get('contracts') or '?'} at {price} {side}")


def any_open(view) -> bool:
    """Whether a row is still worth asking Schwab about."""
    return any(r.get("status") == "unknown" or r.get("can_cancel")
               for r in (view or {}).get("orders") or ())


# ── page ────────────────────────────────────────────────────────────────────

def render() -> None:
    view = _view(live_order.TODAY_KEY)
    st = {"ver": None, "data": None, "last_ask": 0.0, "cancel_id": None}

    with kit.page(width="form"):
        with kit.header("Today's Live Orders").actions:
            refresh_btn = kit.button("Refresh", kind="secondary", icon="refresh")
        mode_note = kit.notice("Live orders are off (Settings, Configuration, Live orders): "
                               "nothing can be sent or cancelled from the app.")
        mode_note.set_visibility(False)
        with ui.column().classes(f"{_t.CARD} w-full gap-2"):
            checked = ui.label("Asking Schwab…").classes(f"text-xs {_t.MUTED}")
            rows_box = ui.column().classes("w-full gap-2")
        ui.label("Once an order fills, its take-profit (and a single option's stop) "
                 "are working orders of their own: change or cancel them in "
                 "ThinkorSwim.").classes(f"text-xs {_t.MUTED}")

    cancel_dlg = kit.confirm("Cancel this order?", "", confirm_text="Cancel order",
                             on_confirm=lambda: _cancel(), danger=True)

    def _request(type_, args):
        try:
            bus_client.request(live_order.DOMAIN, {"type": type_, "args": args})
            return True
        except Exception:         # noqa: BLE001 - reported on screen, not raised
            kit.toast("error", "Could not reach the options service")
            return False

    @guard
    def _paint():
        data = st["data"] or {}
        mode_note.set_visibility(data.get("mode", live_orders_config.mode()) == "off")
        stamp = str(data.get("checked_at") or "")
        checked.text = (f"Read from Schwab at {stamp[11:19]} Central." if len(stamp) >= 19
                        else "Asking Schwab…")
        rows_box.clear()
        with rows_box:
            rows = data.get("orders") or []
            if not rows and data:
                ui.label("No live order sent today.").classes(f"text-sm {_t.LABEL}")
            for row in rows:
                with ui.row().classes("w-full items-center gap-x-3 gap-y-1 flex-wrap"):
                    with ui.column().classes("gap-0 grow min-w-0"):
                        ui.label(order_line(row)).classes(f"text-sm {_t.LABEL}")
                        ui.label(status_words(row)).classes(
                            f"text-sm font-semibold {TONE_CLASS[tone(row)]}")
                        if row.get("error"):
                            ui.label(str(row["error"])).classes(f"text-xs {_t.MUTED}")
                        if row.get("max_loss") is not None:
                            ui.label(f"Max loss {fmt.money(row['max_loss'])}").classes(
                                f"text-xs {_t.MUTED}")
                    if row.get("can_cancel"):
                        kit.button("Cancel order", kind="danger", icon="cancel",
                                   on_click=_cancel_for(row))

    def _cancel_for(row):
        # The row is bound HERE, not as a lambda default: NiceGUI hands a
        # one-parameter handler the click event, which would replace it.
        return lambda: _ask_cancel(row)

    def _ask_cancel(row):
        st["cancel_id"] = row.get("ticket_id")
        cancel_dlg.body.text = (f"{order_line(row)}. Schwab is asked to cancel it; if it "
                                "has already filled, nothing changes.")
        cancel_dlg.open()

    def _cancel():
        tid = st["cancel_id"]
        if isinstance(tid, str) and live_order.TICKET_ID_RE.match(tid):
            if _request("cancel", {"ticket_id": tid}):
                kit.toast("info", "Cancel asked of Schwab")

    @guard
    def _ask(_e=None):
        st["last_ask"] = time.time()
        if _request("today", {}):
            kit.set_busy(refresh_btn)

    @guard
    def _tick():
        ver = bus_client.read_version(view)
        if ver != st["ver"]:
            st["ver"] = ver
            got = bus_client.read(view)
            if got is not None:
                st["data"] = got
                kit.set_busy(refresh_btn, False)
                _paint()
        if any_open(st["data"]) and \
                time.time() - st["last_ask"] >= live_orders_config.status_poll_sec():
            st["last_ask"] = time.time()
            _request("today", {})

    refresh_btn.on_click(_ask)
    st["ver"] = bus_client.read_version(view)
    st["data"] = bus_client.read(view)
    _paint()
    _ask()
    ui.timer(1.0, _tick)
