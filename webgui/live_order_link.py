"""The way from a trade's detail panel to the live-order ticket (Phase 2).

Design: docs/plans/2026-11-05-live-order-to-thinkorswim-design.md. Every panel
built by ``pages/options/detail.py`` (Market Scanner, Swing, Captured Signals,
Paper Ledger, Calculator) offers **Send Live Order** beside **Copy ThinkorSwim
order**, for the same trade:

* a Paper Ledger trade idea opens ``/send-order?trade=<id>``, and the service
  reads the idea from the Ledger itself;
* any other trade is turned into a SPEC (its ticker, legs, a label and the
  price the panel showed), stashed here under a random token, and opens
  ``/send-order?spec=<token>``. The service checks the spec as strictly as an
  idea (``live_order.read_spec``), prices the legs itself and builds the order
  itself.

PRIVATE only. ``detail.py`` is also drawn by the public process (the public
Calculator), so it imports this module LAZILY and only behind
``shell.is_public()`` being false; ``webgui/tests/test_send_order.py`` pins that.
The stash is this process's memory: the app has one user, and a token outlives
a reload of the ticket but not a restart (the page then says to open it again).
"""
from __future__ import annotations

import datetime as _dt
import uuid
from collections import OrderedDict
from zoneinfo import ZoneInfo

from pages.options import tos_order
from shared import futures_chain, live_order, live_orders_config

_CT = ZoneInfo("America/Chicago")
#: Tokens kept: one per click, the oldest dropped first.
STASH_MAX = 64
_STASH: "OrderedDict[str, dict]" = OrderedDict()

BUTTON_TEXT = "Send Live Order"


def _today():
    return _dt.datetime.now(_CT).date()


def spec_for(signal):
    """A panel's trade as a spec ``{symbol, legs, label, price}``, or None when
    it holds shares or its legs cannot be read. Read through ``tos_order``,
    the same reading the Copy ThinkorSwim order button uses."""
    s = signal if isinstance(signal, dict) else {}
    legs = tos_order.position_legs(s)
    shape = tos_order.structure(s)
    if not legs or not shape or any(l["right"] == "STOCK" for l in legs):
        return None
    symbol = str(s.get("symbol") or "")
    price = tos_order.entry_price(s)
    lots = shape.get("lots") or 1
    return {
        "symbol": symbol,
        "label": f"{symbol.lstrip('$')} {str(shape.get('name') or '').title()}".strip(),
        "price": None if price is None else round(price / lots, 4),
        "legs": [{"kind": l["right"].lower(), "side": "long" if l["qty"] > 0 else "short",
                  "strike": l["strike"], "qty": abs(l["qty"]),
                  "expiration": l["expiry"].isoformat()} for l in legs],
    }


def offer(signal, today=None):
    """What the panel's button opens: ``{"href": ...}`` for a Ledger trade idea,
    ``{"spec": ...}`` for any other trade a ticket may send, or None (live
    orders off, shares, an /ES or /NQ contract, or legs ``read_spec`` refuses)."""
    if live_orders_config.mode() == "off":
        return None
    s = signal if isinstance(signal, dict) else {}
    idea = s.get("live_idea")
    if isinstance(idea, str) and live_order.TRADE_ID_RE.match(idea):
        return {"href": f"/send-order?trade={idea}"}
    if futures_chain.is_feed_symbol(str(s.get("symbol") or "")):
        return None
    spec = spec_for(s)
    if spec is None:
        return None
    try:
        live_order.read_spec(spec, today or _today())
    except ValueError:
        return None
    return {"spec": spec}


def stash(spec) -> str:
    """Keep ``spec`` under a new token (32 hex, the ticket's quote id)."""
    token = uuid.uuid4().hex
    _STASH[token] = spec
    while len(_STASH) > STASH_MAX:
        _STASH.popitem(last=False)
    return token


def take(token):
    """The spec stashed under ``token``, or None. Not removed: a reload of the
    ticket finds it again."""
    if not isinstance(token, str) or not live_order.TICKET_ID_RE.match(token):
        return None
    return _STASH.get(token)


def href_for(found) -> str | None:
    """The ticket's address for :func:`offer`'s answer (stashing a spec)."""
    if not found:
        return None
    if "href" in found:
        return found["href"]
    return f"/send-order?spec={stash(found['spec'])}"
