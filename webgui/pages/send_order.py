"""Send Live Order: a trade as a real order in the Schwab account (live-order
design, Phases 1-3). Two ways in:

* ``/send-order?trade=<id>``: a Paper Ledger trade idea (the banner, or an
  idea row's detail panel); the service reads the idea from the Ledger;
* ``/send-order?spec=<token>``: any other trade's detail panel, whose legs
  ``live_order_link`` stashed under that token; the service checks them with
  ``live_order.read_spec`` and prices them itself.

The page asks and shows; the options service acts. Every number the service
will send is worked out here with the SAME pure builder it uses
(``shared/live_order.py``), so what the ticket shows is what is sent:

1. **Quote**: on load (and on Refresh price) the page enqueues ``quote``; the
   service prices the trade's legs from a chain fetched fresh and publishes
   ``live_order.quote_key(trade or token)``.
2. **Preview**: enqueues ``preview`` with a ticket id made here; Schwab checks
   the order and places nothing. Changing the limit, the size or the account
   drops the preview.
3. **Send**: lit only while a passed preview is younger than its time limit,
   live orders are on and the regular session is open. It asks for the
   authenticator code when ``live_unlock.needed()`` says so, then a final
   confirm that names the size, the account and the limit, and only then
   enqueues ``place``. The service sends the order it STORED at the preview,
   never one from this page.
4. **Cancel** (Phase 3): while Schwab has not said the sent order is done, a
   confirm enqueues ``cancel``; the service asks Schwab first and cancels only
   an order Schwab says can be cancelled. **Today's live orders** lists every
   order sent today (``pages/live_orders.py``).

PRIVATE only: ``bus_client.request`` refuses on the public read-only process,
the route is registered in ``main.py`` alone, and nothing here names an account
number: the dropdown and every view carry the account's alias.
"""
from __future__ import annotations

import math
import time
import uuid

from nicegui import ui

import bus_client
import live_order_link
import live_unlock
import shell as _shell
from pages import fmt
from pages import ui_kit as kit
from pages.options import theme as _t
from pages.options import tos_order
from pages.ui_guard import guard
from shared import live_order, live_orders_config

_STATUS_WORDS = {
    "previewed": "Preview passed", "refused": "Not sent", "sending": "Sending",
    "sent": "Sent to Schwab", "unknown": "No answer from Schwab",
    "rejected": "Schwab refused it", "expired": "The preview expired",
}

#: The risk line's three states, each a FIXED class (never built at run time).
RISK_CLASS = {"ok": _t.LABEL, "warn": _t.TXT_WARN, "over": _t.TXT_NEG}


def _view(key: str) -> str:
    """A ``cache:`` key as ``bus_client``'s view name."""
    return key[len("cache:"):]


# ── pure helpers ────────────────────────────────────────────────────────────

def valid_trade(trade) -> bool:
    return isinstance(trade, str) and bool(live_order.TRADE_ID_RE.match(trade))


def source_for(trade, spec_token):
    """``(view id, the source a command carries)`` for the page's address, or
    ``(None, reason)`` when it names nothing the page can open."""
    if valid_trade(trade):
        return trade, {"trade_id": trade}
    if spec_token is not None:
        spec = live_order_link.take(spec_token)
        if spec is None:
            return None, ("This ticket is no longer held (the app restarted, or it is "
                          "an old link). Open it again from the trade's detail panel.")
        return spec_token, {"quote_id": spec_token, "spec": spec}
    return None, ("Open this page from a trade's detail panel (Send Live Order), "
                  "or from the New trade idea banner.")


def risk_total(loss_per_contract, contracts):
    """The order's max loss in dollars, or None when it cannot be worked out."""
    if loss_per_contract is None or not isinstance(contracts, int) or contracts < 1:
        return None
    return round(loss_per_contract * contracts, 2)


def risk_tone(total, default_risk, max_risk) -> str:
    """``ok`` up to the default, ``warn`` past it (allowed), ``over`` past the
    ceiling or unbounded (refused)."""
    if total is None or total > max_risk:
        return "over"
    return "warn" if total > default_risk else "ok"


def as_contracts(value):
    """The Contracts box's value as a whole number, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value != int(value) or value < 1:
        return None
    return int(value)


def as_limit(value, symbol):
    """The Limit box's value on the price step, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value <= 0:
        return None
    return live_order.round_to_tick(float(value), symbol, live_orders_config.ticks())


def priced(quote, limit, contracts):
    """``{loss, profit, target, stop, stop_on_order}`` for the typed limit and
    size, from the quote's legs, or None when the quote cannot price them."""
    if not isinstance(quote, dict) or not quote.get("legs") or limit is None:
        return None
    legs, debit, symbol = quote["legs"], quote.get("debit"), quote.get("symbol", "")
    exits = quote.get("exits") or {}
    try:
        loss = live_order.max_loss_per_contract(legs, limit, debit)
        profit = live_order.max_profit_per_contract(legs, limit, debit)
        target, stop = live_order.exit_prices(limit, debit, exits.get("pcts") or {},
                                              symbol, live_orders_config.ticks())
    except (ValueError, KeyError, TypeError):
        return None
    return {"loss": risk_total(loss, contracts),
            "profit": None if profit is None or contracts is None
            else round(profit * contracts, 2),
            # Two expiries: the gain depends on the far leg's volatility, so
            # None there is "cannot be read", not "no limit".
            "profit_known": len(quote.get("expirations") or ()) < 2,
            "target": target, "stop": stop,
            "stop_on_order": bool(exits.get("stop_on_order")),
            "exit_tif": exits.get("exit_tif", "GTC")}


def paste_text(quote, limit, contracts):
    """The same order in ThinkorSwim's paste format, to compare with the script
    already checked; None when it cannot be written."""
    if not isinstance(quote, dict) or limit is None or contracts is None:
        return None
    signal = {"symbol": quote.get("symbol"), "expiration": quote.get("expiration"),
              "type": quote.get("strategy"), "legs": quote.get("legs"),
              "net_cost": -limit if quote.get("debit") else limit}
    cfg = {**tos_order.settings(), "quantity": contracts}
    got = tos_order.order(signal, cfg)
    return got["text"] if got else None


def send_ready(ticket, quote, now):
    """``(ready, why not)`` for the Send button."""
    if not isinstance(ticket, dict) or ticket.get("status") != "previewed":
        return False, "Preview the order first."
    if (quote or {}).get("mode") != "live":
        return False, "Live orders are not on (Settings, Configuration, Live orders)."
    if not (quote or {}).get("in_session"):
        return False, "Orders are sent in the regular session only (08:30 to 15:00 Central)."
    expires = ticket.get("expires_epoch")
    if not isinstance(expires, (int, float)) or expires <= now:
        return False, "The preview has expired; preview again."
    return True, ""


def status_text(ticket) -> str:
    if not isinstance(ticket, dict):
        return ""
    words = _STATUS_WORDS.get(ticket.get("status"), str(ticket.get("status") or ""))
    if ticket.get("order_id"):
        words += f": order {ticket['order_id']}"
    if ticket.get("order_status"):
        words += f" ({str(ticket['order_status']).replace('_', ' ').lower()})"
    return words


#: A ticket past these has gone to Schwab: the page holds it and offers no second
#: preview (reload the page for a new ticket).
SENT_STATES = frozenset({"sending", "sent", "unknown", "rejected"})


def locked(ticket) -> bool:
    return isinstance(ticket, dict) and ticket.get("status") in SENT_STATES


def still_working(ticket) -> bool:
    """Whether the page should keep asking Schwab for this order's state."""
    if not isinstance(ticket, dict):
        return False
    if ticket.get("status") == "unknown":
        return True
    return ticket.get("status") == "sent" and \
        not live_order.order_is_final(ticket.get("order_status"))


def can_cancel(ticket) -> bool:
    """Whether the ticket offers Cancel: sent, and Schwab has not said the
    order is done. The service asks Schwab again before it cancels."""
    return isinstance(ticket, dict) and \
        live_order.offers_cancel(ticket.get("status"), ticket.get("order_status"))


def leg_line(leg) -> str:
    side = "Buy" if leg.get("side") == "long" else "Sell"
    qty = leg.get("qty", 1)
    mid = leg.get("mid")
    mid_text = "no market" if mid is None else f"mid {fmt.price(mid)}"
    return (f"{side} {qty} {fmt.strike(leg.get('strike'))} "
            f"{str(leg.get('kind', '')).capitalize()} {leg.get('expiration', '')}: {mid_text}")


def read_time(stamp) -> str:
    """``10:02:11`` from the service's ISO stamp (already Central)."""
    text = str(stamp or "")
    return text[11:19] if len(text) >= 19 and text[10] == "T" else "?"


def _money(v):
    return "no limit" if v is None else fmt.money(v)


# ── page ────────────────────────────────────────────────────────────────────

def render(trade: str | None, client: str, spec: str | None = None) -> None:
    with kit.page(width="form"):
        kit.header("Send Live Order")
        view_id, source = source_for(trade, spec)
        if view_id is None:
            kit.notice(source)
            return
        _ticket_page(view_id, source, client)


def _ticket_page(view_id: str, source: dict, client: str) -> None:
    quote_view = _view(live_order.quote_key(view_id))
    st = {"quote": None, "ticket": None, "ticket_view": None, "quote_ver": None,
          "ticket_ver": None, "risk_cls": RISK_CLASS["ok"], "last_status": 0.0,
          "seeded": False}
    aliases = live_orders_config.aliases()

    with ui.column().classes(f"{_t.CARD} w-full gap-2"):
        title = ui.label("Reading the live price…").classes(
            f"text-lg font-semibold {_t.LABEL}")
        legs_box = ui.column().classes("w-full gap-0")
        price_line = ui.label("").classes(f"text-sm {_t.MUTED}")
        drift_line = ui.label("").classes(f"text-sm {_t.TXT_WARN}")
        drift_line.set_visibility(False)
        quote_error = ui.label("").classes(f"text-sm {_t.TXT_NEG}")
        quote_error.set_visibility(False)

    with ui.column().classes(f"{_t.CARD} w-full gap-3"):
        with ui.row().classes("w-full items-end gap-x-4 gap-y-2 flex-wrap"):
            limit_in = kit.number_field("Limit price", min=0.01, step=0.01, width="w-32",
                                       format="%.2f")
            size_in = kit.number_field("Contracts", min=1,
                                       max=live_orders_config.max_contracts(),
                                       step=1, integer=True, width="w-28")
            account_in = kit.select_field(
                "Account", aliases, value=aliases[0] if aliases else None, width="w-48")
        risk_line = ui.label("").classes(f"text-sm font-semibold {RISK_CLASS['ok']}")
        profit_line = ui.label("").classes(f"text-sm {_t.LABEL}")
        exits_line = ui.label("").classes(f"text-sm {_t.LABEL}")
        stop_note = ui.label(
            "The stop is not on this order: Schwab's API refuses a stop on a spread. "
            "Set it in ThinkorSwim once the order fills.").classes(f"text-sm {_t.TXT_WARN}")
        stop_note.set_visibility(False)
        ui.label("The exits are priced off the limit, not the fill: a better fill "
                 "leaves them a little wider than a pasted order's.").classes(
            f"text-xs {_t.MUTED}")
        if not aliases:
            kit.notice("No account is set up for live orders. Add one to "
                       "config/local/live_orders.toml (see the tracked file).")
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            refresh_btn = kit.button("Refresh price", kind="secondary", icon="refresh")
            preview_btn = kit.button("Preview", kind="secondary", icon="fact_check")
            send_btn = kit.button("Send", kind="primary", icon="send")
            ui.space()
            why = ui.label("").classes(f"text-sm {_t.MUTED}")

    with ui.column().classes(f"{_t.CARD} w-full gap-2"):
        kit.section_title("Schwab's answer")
        ticket_line = ui.label("Not previewed yet.").classes(f"text-sm {_t.LABEL}")
        messages_box = ui.column().classes("w-full gap-0")
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            cancel_btn = kit.button("Cancel order", kind="danger", icon="cancel")
            cancel_btn.set_visibility(False)
            kit.button("Today's live orders", kind="quiet", icon="list_alt",
                       on_click=lambda: _shell.navigate_to("/live-orders"))
        kit.section_title("The same order for ThinkorSwim's paste")
        paste_line = ui.label("").classes(
            f"text-xs font-mono whitespace-pre-wrap break-words {_t.MUTED}")

    code_dlg = kit.confirm("Authenticator code", "Live orders ask for your code "
                           "before the first send of each day.",
                           confirm_text="Unlock", on_confirm=lambda: _check_code())
    with code_dlg.content:
        code_in = kit.text_field("Code", placeholder="6 digits", width="w-32")
        code_error = ui.label("").classes(f"text-sm {_t.TXT_NEG}")
    send_dlg = kit.confirm("Send this order?", "", confirm_text="Send order",
                           on_confirm=lambda: _place(), danger=True)
    cancel_dlg = kit.confirm("Cancel this order?", "Schwab is asked to cancel the "
                             "opening order. If it has already filled, nothing changes: "
                             "its exits are then managed in ThinkorSwim.",
                             confirm_text="Cancel order", on_confirm=lambda: _cancel(),
                             danger=True)

    # ── reading the page's inputs ───────────────────────────────────────────
    def _inputs():
        q = st["quote"] or {}
        return (as_limit(limit_in.value, q.get("symbol", "")),
                as_contracts(size_in.value), account_in.value)

    def _request(type_, args):
        try:
            bus_client.request(live_order.DOMAIN, {"type": type_, "args": args})
            return True
        except Exception:         # noqa: BLE001 - reported on screen, not raised
            kit.toast("error", "Could not reach the options service")
            return False

    def _spinning(btn):
        """Whether ``kit.set_busy`` is holding this button."""
        return (getattr(btn, "_kit_busy", None) or {}).get("deadline") is not None

    def _release(btn):
        if _spinning(btn):
            kit.set_busy(btn, False)

    # ── painting ────────────────────────────────────────────────────────────
    @guard
    def _paint_quote():
        q = st["quote"] or {}
        if q.get("structure"):
            title.text = (f"{q.get('idea_label') or q.get('symbol')}: "
                          f"{'Debit' if q.get('debit') else 'Credit'}, "
                          f"expires {q.get('expiration')}")
        legs_box.clear()
        with legs_box:
            for leg in q.get("legs") or ():
                ui.label(leg_line(leg)).classes(f"text-sm {_t.LABEL}")
        if q.get("live_price") is not None:
            posted = q.get("posted_price")
            said = "posted at" if q.get("price_source") == "idea" else "the panel showed"
            price_line.text = (f"Live mid {fmt.price(q['live_price'])}"
                               + (f", {said} {fmt.price(posted)}" if posted else "")
                               + f". Read at {read_time(q.get('checked_at'))} Central.")
        since = "the idea was posted" if q.get("price_source") == "idea" \
            else "the price the panel showed"
        drift_line.text = (f"The price has moved {fmt.pct(q.get('drift_pct'), signed=True)} "
                           f"since {since}." if q.get("drift_warn") else "")
        drift_line.set_visibility(bool(q.get("drift_warn")))
        quote_error.text = q.get("error") or ""
        quote_error.set_visibility(bool(q.get("error")))
        if not st["seeded"] and q.get("live_price") is not None:
            st["seeded"] = True
            limit_in.value = q["live_price"]
            size_in.value = q.get("default_contracts") or 1
        _paint_order()

    @guard
    def _paint_order(_e=None):
        q = st["quote"] or {}
        limit, contracts, _alias = _inputs()
        p = priced(q, limit, contracts)
        if p is None:
            risk_line.text = profit_line.text = exits_line.text = ""
            stop_note.set_visibility(False)
            paste_line.text = ""
        else:
            tone = risk_tone(p["loss"], live_orders_config.default_risk_usd(),
                             live_orders_config.max_risk_usd())
            risk_line.text = f"Max loss {_money(p['loss'])}" + {
                "ok": "",
                "warn": f", more than the usual {fmt.money(live_orders_config.default_risk_usd())}",
                "over": f": over the {fmt.money(live_orders_config.max_risk_usd())} "
                        "a single order may risk"}[tone]
            cls = RISK_CLASS[tone]
            if cls != st["risk_cls"]:
                risk_line.classes(remove=st["risk_cls"], add=cls)
                st["risk_cls"] = cls
            profit_line.text = (f"Max profit {_money(p['profit'])}" if p["profit_known"]
                                else "Max profit depends on the far leg's volatility "
                                     "at the near expiry")
            exits_line.text = (f"Take profit at {fmt.price(p['target'])}"
                               + (f", stop at {fmt.price(p['stop'])}" if p["stop_on_order"]
                                  else "") + f" ({p['exit_tif']})")
            stop_note.set_visibility(not p["stop_on_order"])
            paste_line.text = paste_text(q, limit, contracts) or ""
        _paint_buttons()

    @guard
    def _paint_buttons():
        q = st["quote"] or {}
        limit, contracts, alias = _inputs()
        p = priced(q, limit, contracts)
        waiting = st["ticket_view"] is not None and st["ticket"] is None
        can_preview = (p is not None and alias in aliases and contracts is not None
                       and contracts <= live_orders_config.max_contracts()
                       and risk_tone(p["loss"], live_orders_config.default_risk_usd(),
                                     live_orders_config.max_risk_usd()) != "over"
                       and q.get("mode") in ("preview", "live")
                       and not waiting and not locked(st["ticket"]))
        for b, on in ((preview_btn, can_preview),
                      (refresh_btn, not locked(st["ticket"]))):
            if _spinning(b):
                continue                  # held by its own spinner until the answer lands
            if on:
                b.enable()
            else:
                b.disable()
        for f in (limit_in, size_in, account_in):
            if locked(st["ticket"]):
                f.disable()
            else:
                f.enable()
        ready, reason = send_ready(st["ticket"], q, time.time())
        if ready:
            send_btn.enable()
        else:
            send_btn.disable()
        why.text = "" if ready or locked(st["ticket"]) else reason

    @guard
    def _paint_ticket():
        t = st["ticket"]
        ticket_line.text = status_text(t) + (f". {t['error']}" if t and t.get("error") else "")
        messages_box.clear()
        with messages_box:
            for m in (t or {}).get("messages") or ():
                ui.label(str(m)).classes(f"text-sm {_t.MUTED}")
        cancel_btn.set_visibility(can_cancel(t))
        _paint_buttons()

    # ── actions ─────────────────────────────────────────────────────────────
    def _drop_preview(_e=None):
        """A change to the limit, size or account drops the preview."""
        if locked(st["ticket"]):
            return
        if st["ticket"] is not None or st["ticket_view"] is not None:
            st["ticket"], st["ticket_view"], st["ticket_ver"] = None, None, None
            ticket_line.text = "Changed since the preview: preview again."
            messages_box.clear()
        _release(preview_btn)
        _paint_order()

    @guard
    def _refresh(_e=None):
        if _request("quote", dict(source)):
            kit.set_busy(refresh_btn)

    @guard
    def _preview(_e=None):
        limit, contracts, alias = _inputs()
        ticket_id = uuid.uuid4().hex
        args = {"ticket_id": ticket_id, **source,
                "alias": alias, "contracts": contracts, "limit": limit}
        if "trade_id" in source:
            args["idea_posted"] = (st["quote"] or {}).get("idea_posted")
        if _request("preview", args):
            st["ticket"], st["ticket_ver"] = None, None
            st["ticket_view"] = _view(live_order.ticket_key(ticket_id))
            ticket_line.text = "Asking Schwab to check the order…"
            messages_box.clear()
            kit.set_busy(preview_btn)
            _paint_buttons()

    @guard
    def _ask_send(_e=None):
        if not send_ready(st["ticket"], st["quote"], time.time())[0]:
            return
        if live_unlock.needed():
            code_in.value = ""
            code_error.text = ""
            code_dlg.open()
            return
        _ask_confirm()

    def _ask_confirm():
        t = st["ticket"]
        send_dlg.body.text = (f"{t['contracts']} x {(st['quote'] or {}).get('idea_label') or t.get('symbol')} "
                              f"at {fmt.price(t['limit'])} limit, to the {t['alias']}. "
                              "This places a real order.")
        send_dlg.open()

    def _check_code():
        if not live_unlock.unlock(code_in.value, client):
            code_error.text = live_unlock.REFUSED
            return False
        ui.timer(0.1, _ask_confirm, once=True)
        return True

    def _place():
        t = st["ticket"]
        if not send_ready(t, st["quote"], time.time())[0]:
            kit.toast("warn", "The preview is no longer valid; preview again")
            return
        if _request("place", {"ticket_id": t["ticket_id"]}):
            st["ticket"] = {**t, "status": "sending"}
            _paint_ticket()

    def _ask_cancel(_e=None):
        if can_cancel(st["ticket"]):
            cancel_dlg.open()

    def _cancel():
        t = st["ticket"]
        if can_cancel(t) and _request("cancel", {"ticket_id": t["ticket_id"]}):
            kit.set_busy(cancel_btn)

    # ── watching the two views ──────────────────────────────────────────────
    @guard
    def _tick():
        ver = bus_client.read_version(quote_view)
        if ver != st["quote_ver"]:
            st["quote_ver"] = ver
            st["quote"] = bus_client.read(quote_view)
            _release(refresh_btn)
            _paint_quote()
        view = st["ticket_view"]
        if view is not None:
            ver = bus_client.read_version(view)
            if ver != st["ticket_ver"]:
                st["ticket_ver"] = ver
                got = bus_client.read(view)
                if got is not None:
                    st["ticket"] = got
                    _release(preview_btn)
                    _release(cancel_btn)
                    _paint_ticket()
            t = st["ticket"]
            now = time.time()
            if still_working(t) and now - st["last_status"] >= live_orders_config.status_poll_sec():
                st["last_status"] = now
                _request("status", {"ticket_id": t["ticket_id"]})
        _paint_buttons()

    limit_in.on_value_change(_drop_preview)
    size_in.on_value_change(_drop_preview)
    account_in.on_value_change(_drop_preview)
    refresh_btn.on_click(_refresh)
    preview_btn.on_click(_preview)
    send_btn.on_click(_ask_send)
    cancel_btn.on_click(_ask_cancel)

    # An earlier quote (this trade's, from a previous visit) paints at once; the
    # fresh one asked for below replaces it when it lands.
    st["quote_ver"] = bus_client.read_version(quote_view)
    st["quote"] = bus_client.read(quote_view)
    _refresh()
    _paint_quote()
    ui.timer(1.0, _tick)
