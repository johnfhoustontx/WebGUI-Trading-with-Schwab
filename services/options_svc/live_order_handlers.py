"""``cmd:live_order``: a trade, sent to the Schwab account as an order.

Design: docs/plans/2026-11-05-live-order-to-thinkorswim-design.md (Phases 1-2).
This is that stream's handler (``make_app``'s ``extra_consumers``), on a thread
of its own. It is never on ``cmd:options``' table or its slow lane.

Four commands, each answered on a cache view the ticket page watches:

* ``quote`` ``{trade_id}`` or ``{quote_id, spec}``: the trade's legs priced
  from a chain fetched FRESH (``maxAge=0``), with the default size, the max
  loss and the exits -> ``live_order.quote_key(trade_id or quote_id)``.
* ``preview`` ``{ticket_id, alias, contracts, limit}`` plus the same source:
  re-reads the trade, re-checks every limit, builds the order, has Schwab
  check it (``previewOrder``, which places nothing), and stores a TICKET: the
  exact payload, its digest, the account hash ->
  ``live_order.ticket_key(ticket_id)``.
* ``place`` ``{ticket_id}``: places the STORED payload, never one the page
  sends, after claiming the ticket ``previewed -> sending`` in one conditional
  UPDATE (``live_order_store.claim``).
* ``status`` ``{ticket_id}``: Schwab's state of a sent order.

**Replay.** A fresh consumer group replays its stream's backlog on its first
launch. Three guards, each enough on its own: a command older than
``max_command_age_sec`` (or with no time at all) is dropped unread; a ticket is
single-use; a ticket expires ``ticket_ttl_sec`` after its preview.

**An answer that never comes is not a failure.** If the place call times out or
errors after it may have reached Schwab, the ticket is ``unknown`` and is NEVER
sent again: the service lists the account's orders around that time and, on
exactly one match, records its id. The page says to check ThinkorSwim.

**Two sources of a trade.** A Paper Ledger trade idea (``trade_id``) is read
from the Ledger itself, never from the page's echo. Any other trade comes from
a detail panel as a SPEC: its ticker and legs, which ``live_order.read_spec``
checks as strictly (the one ticker allow-list, each leg, a structure that may be
sent, expiries that bound the loss). Either way the service prices the legs
itself, builds the payload itself, and places only what Schwab previewed.

**What reaches Tier 1.** The views carry the account's ALIAS only: never its
number or hash, and Schwab's answers are scrubbed (``live_order.scrub``) before
they are stored or published. The authenticator code is Tier 1's (``auth``):
the page asks for it before it enqueues a ``place``.

Imports nothing from ``compute`` (which has a line ceiling).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import math
import time
from zoneinfo import ZoneInfo

import requests

from repo_paths import PROXY_URL
from services import _degrade
from services.options_svc import live_order_store as store
from shared import futures_chain, live_order, live_orders_config, market_calendar, \
    tos_order_config

log = logging.getLogger(__name__)

AREA = "options.live_order"
_CT = ZoneInfo("America/Chicago")
_CALLER = "options_svc.live_order"
#: Seconds the service waits for the proxy. The place call's own Schwab timeout
#: is 30 s; past this the outcome is unknown, never a failure.
_TIMEOUT = {"chain": 30, "accounts": 20, "preview": 45, "place": 45, "read": 20}
#: The views stay this long: a ticket is a record the page may reopen today.
_VIEW_TTL_SEC = 24 * 3600
#: How far either side of the send the order list is searched for an order whose
#: answer never came back.
_RECONCILE_WINDOW_SEC = 120


# ── the outside world (each one patched in tests) ────────────────────────────

def _now() -> float:
    return time.time()


def _session() -> requests.Session:
    from proxy_client import _client_secret   # schwab-proxy/ is on sys.path (services._proxy)
    s = requests.Session()
    secret = _client_secret()
    if secret:
        s.headers["X-Proxy-Secret"] = secret
    s.headers["X-Caller"] = _CALLER
    return s


def _get(path, params=None, *, timeout):
    """``(status, json-or-text)`` of a GET to the proxy. Raises on no answer."""
    r = _session().get(f"{PROXY_URL}{path}", params=params, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def _post(path, body, *, timeout):
    """``(status, json-or-text)`` of a POST to the proxy. Raises on no answer."""
    r = _session().post(f"{PROXY_URL}{path}", json=body, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def _ledger_row(trade_id):
    """The Paper Ledger row with this id, or None."""
    from services.options_svc import idea_ledger
    import paper_trader                         # options-scanner/ (put on sys.path by idea_ledger)
    for t in paper_trader.get_all_trades():
        if t.get("trade_id") == trade_id:
            return t if idea_ledger.is_idea(t) else None
    return None


# ── small pieces ─────────────────────────────────────────────────────────────

class Refused(Exception):
    """A request this service will not act on, with a reason the page shows."""


def _age_sec(ts):
    """Seconds since an ISO enqueue time; None when there is none to read."""
    if not isinstance(ts, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return _now() - when.timestamp()


def _digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def _iso(epoch):
    return _dt.datetime.fromtimestamp(epoch, _CT).isoformat(timespec="seconds") \
        if epoch else None


def _utc_ms(epoch):
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _in_session(epoch) -> bool:
    return market_calendar.is_regular_hours(_dt.datetime.fromtimestamp(epoch, _CT))


def _exit_settings():
    return tos_order_config.settings()


def _publish(bus, key, payload) -> None:
    bus.cache_set(key, payload, ttl=_VIEW_TTL_SEC)


def _legs_of(row):
    """The idea's option legs in the builder's shape (no symbol yet)."""
    out = []
    for leg in row.get("legs") or ():
        if not isinstance(leg, dict):
            raise Refused("a leg of this idea cannot be read")
        kind = str(leg.get("kind", "")).lower()
        if kind not in ("call", "put"):
            raise Refused("this idea has a share leg; it cannot be sent from here")
        qty = leg.get("qty", 1)
        if isinstance(qty, float) and qty == int(qty):
            qty = int(qty)
        out.append({"kind": kind, "side": str(leg.get("side", "")).lower(),
                    "strike": leg.get("strike"), "qty": qty,
                    "expiration": str(leg.get("expiration") or row.get("expiration"))[:10]})
    if not out:
        raise Refused("this idea has no legs")
    return out


def _read_idea(trade_id, idea_posted=None):
    if not isinstance(trade_id, str) or not live_order.TRADE_ID_RE.match(trade_id):
        raise Refused("not a Paper Ledger trade")
    row = _ledger_row(trade_id)
    if row is None:
        raise Refused("no trade idea with this id in the Paper Ledger")
    if idea_posted is not None and row.get("idea_posted") != idea_posted:
        raise Refused("the Ledger's idea is not the one this ticket was opened for")
    symbol = str(row.get("symbol") or "")
    if futures_chain.is_feed_symbol(symbol):
        raise Refused("Schwab cannot place an order on /ES or /NQ options")
    expiration = str(row.get("expiration") or "")[:10]
    try:
        if _dt.date.fromisoformat(expiration) < _dt.datetime.fromtimestamp(_now(), _CT).date():
            raise Refused("this idea has expired")
    except ValueError:
        raise Refused("this idea's expiry cannot be read") from None
    return row, symbol, expiration


def _today():
    return _dt.datetime.fromtimestamp(_now(), _CT).date()


def _source(args):
    """The trade a command names: ``{trade_id, idea_posted, symbol, legs,
    label, strategy, posted_price, price_source}``. Raises Refused."""
    spec = args.get("spec")
    if spec is None:
        row, symbol, _expiration = _read_idea(args.get("trade_id"), args.get("idea_posted"))
        legs, lots = live_order.merge_legs(_legs_of(row))
        posted = abs(float(row.get("entry_credit") or 0.0)) or None
        return {"trade_id": row["trade_id"], "idea_posted": row.get("idea_posted"),
                "symbol": symbol, "legs": legs, "label": row.get("idea_label"),
                "strategy": row.get("strategy"),
                "posted_price": None if posted is None else posted / lots,
                "price_source": "idea"}
    quote_id = args.get("quote_id")
    if not isinstance(quote_id, str) or not live_order.TICKET_ID_RE.match(quote_id):
        raise Refused("this ticket's id cannot be read; open it again from the trade")
    try:
        got = live_order.read_spec(spec, _today())
    except ValueError as e:
        raise Refused(str(e)) from None
    if futures_chain.is_feed_symbol(got["symbol"]):
        raise Refused("Schwab cannot place an order on /ES or /NQ options")
    return {"trade_id": quote_id, "idea_posted": None, "symbol": got["symbol"],
            "legs": got["legs"], "label": got["label"], "strategy": None,
            "posted_price": got["price"], "price_source": "panel"}


def _side(legs, mids):
    """``(debit, net mid or None)``: the side off the legs, or, for a CUSTOM
    order that both buys and sells, off the mids. Raises Refused."""
    debit = live_order.is_debit(legs)
    priced = live_order.net_mid(legs, mids)
    if debit is None:
        if priced is None:
            raise Refused("this order both buys and sells, and with no two-sided "
                          "market on every leg it cannot tell which way it pays")
        debit = priced[1]
    return debit, priced


def _priced_legs(symbol, legs):
    """``(legs with symbol, mids)`` from a chain fetched fresh across every
    leg's expiry. Raises Refused."""
    exps = sorted({leg["expiration"] for leg in legs})
    try:
        status, chain = _get("/chains", {
            "symbol": symbol, "contractType": "ALL", "fromDate": exps[0],
            "toDate": exps[-1], "maxAge": 0}, timeout=_TIMEOUT["chain"])
    except requests.RequestException:
        raise Refused("the chain could not be read; try again") from None
    if status != 200 or not isinstance(chain, dict):
        raise Refused(f"the chain could not be read (proxy answered {status})")
    out, mids = [], []
    for leg in legs:
        c = live_order.find_contract(chain, leg["kind"], leg["expiration"], leg["strike"])
        if c is None or not isinstance(c.get("symbol"), str):
            raise Refused(f"the {leg['expiration']} {leg['strike']:g} {leg['kind']} "
                          "is not in today's chain")
        out.append({**leg, "symbol": c["symbol"]})
        mids.append(live_order.contract_mid(c))
    return out, mids


def _structure(legs):
    try:
        ctype = live_order.complex_type(legs)
    except ValueError as e:
        raise Refused(f"the legs cannot be read: {e}") from None
    if ctype not in live_order.SENDABLE_TYPES:
        raise Refused("this structure is not sent from here")
    try:
        live_order.check_expiries(legs)
    except ValueError as e:
        raise Refused(str(e)) from None
    return ctype


def _exits(legs, limit, debit, symbol):
    cfg = _exit_settings()
    pcts = cfg["debit"] if debit else cfg["credit"]
    target, stop = live_order.exit_prices(limit, debit, pcts, symbol,
                                          live_orders_config.ticks())
    variant = live_order.exit_variant(legs)
    return {"target": target, "stop": stop, "stop_on_order": variant == "bracket",
            "exit_tif": cfg["exit_tif"], "variant": variant, "pcts": pcts}


def _account_hash(alias):
    """The hash of the account this alias names. Tier 2 only: it never leaves."""
    last4 = live_orders_config.last4_for(alias)
    if last4 is None:
        raise Refused("that account is not one orders may go to")
    try:
        status, accounts = _get("/accounts", timeout=_TIMEOUT["accounts"])
    except requests.RequestException:
        raise Refused("the accounts could not be read; try again") from None
    if status != 200 or not isinstance(accounts, list):
        raise Refused(f"the accounts could not be read (proxy answered {status})")
    hashes = [a.get("hashValue") for a in accounts if isinstance(a, dict)
              and str(a.get("accountNumber", "")).endswith(last4)]
    if len(hashes) != 1 or not isinstance(hashes[0], str):
        raise Refused("that account is not linked to this Schwab sign-in")
    return hashes[0]


def _check_size(contracts, max_loss):
    """The hard ceilings. Raises Refused."""
    if isinstance(contracts, bool) or not isinstance(contracts, int) or contracts < 1:
        raise Refused("contracts must be a whole number of 1 or more")
    if contracts > live_orders_config.max_contracts():
        raise Refused(f"more than {live_orders_config.max_contracts()} contracts")
    if max_loss is None:
        raise Refused("this order's loss has no limit")
    if max_loss > live_orders_config.max_risk_usd():
        raise Refused(f"the max loss ${max_loss:,.2f} is more than the "
                      f"${live_orders_config.max_risk_usd():,.2f} ceiling")


# ── the views (alias only) ───────────────────────────────────────────────────

_TICKET_FIELDS = ("ticket_id", "trade_id", "idea_posted", "alias", "symbol",
                  "structure", "contracts", "debit", "max_loss", "verdict",
                  "messages", "status", "order_id", "order_status", "error")


def _ticket_view(t, extra=None) -> dict:
    """What the page may see of a ticket. Never the hash, the payload's account
    or Schwab's raw answer."""
    view = {k: t.get(k) for k in _TICKET_FIELDS}
    view["limit"] = t.get("limit_price")
    view["created_at"] = _iso(t.get("created_at"))
    view["expires_at"] = _iso(t.get("expires_at"))
    view["sent_at"] = _iso(t.get("sent_at"))
    view["expires_epoch"] = t.get("expires_at")
    payload = t.get("payload") or {}
    view["legs"] = [{"instruction": l.get("instruction"), "quantity": l.get("quantity"),
                     "symbol": (l.get("instrument") or {}).get("symbol")}
                    for l in payload.get("orderLegCollection") or ()]
    view.update(extra or {})
    return view


def _refusal_view(ticket_id, reason, args=None) -> dict:
    a = args or {}
    return {"ticket_id": ticket_id, "trade_id": a.get("trade_id"), "status": "refused",
            "alias": a.get("alias"), "error": reason, "messages": [],
            "checked_at": _iso(_now())}


# ── quote ────────────────────────────────────────────────────────────────────

def _quote(bus, args) -> None:
    from_panel = args.get("spec") is not None
    view_id = args.get("quote_id") if from_panel else args.get("trade_id")
    try:
        if from_panel and not (isinstance(view_id, str)
                               and live_order.TICKET_ID_RE.match(view_id)):
            raise ValueError("a panel's quote id is the page's own 32 hex")
        key = live_order.quote_key(view_id)
    except ValueError:
        log.warning("live_order quote: bad id %r ignored", view_id)
        return
    view = {"trade_id": view_id, "checked_at": _iso(_now()),
            "mode": live_orders_config.mode(), "aliases": live_orders_config.aliases(),
            "in_session": _in_session(_now())}
    try:
        src = _source(args)
        symbol = src["symbol"]
        legs, mids = _priced_legs(symbol, src["legs"])
        ctype = _structure(legs)
        debit, priced = _side(legs, mids)
        posted = src["posted_price"]
        exps = sorted({l["expiration"] for l in legs})
        view.update({
            "symbol": symbol, "structure": ctype, "strategy": src["strategy"],
            "idea_posted": src["idea_posted"], "idea_label": src["label"],
            "expiration": exps[0], "expirations": exps, "debit": debit,
            "posted_price": posted, "price_source": src["price_source"],
            "legs": [{**{k: l[k] for k in ("kind", "side", "strike", "qty", "expiration",
                                           "symbol")}, "mid": m}
                     for l, m in zip(legs, mids)],
        })
        if priced is None:
            view["error"] = "one leg has no two-sided market; the price cannot be read"
        else:
            price = live_order.round_to_tick(priced[0], symbol, live_orders_config.ticks())
            loss = live_order.max_loss_per_contract(legs, price, debit)
            drift = None if not posted else round((price - posted) / posted * 100, 1)
            view.update({
                "live_price": price, "max_loss_per_contract": loss,
                "max_profit_per_contract": live_order.max_profit_per_contract(legs, price, debit),
                "default_contracts": live_order.default_contracts(
                    loss, live_orders_config.default_risk_usd(),
                    live_orders_config.max_contracts()),
                "drift_pct": drift,
                "drift_warn": drift is not None and abs(drift) > live_orders_config.max_drift_pct(),
                "exits": _exits(legs, price, debit, symbol),
            })
    except Refused as e:
        view["error"] = str(e)
    except ValueError as e:
        view["error"] = f"the order cannot be priced: {e}"
    _publish(bus, key, view)


# ── preview ──────────────────────────────────────────────────────────────────

def _preview(bus, args) -> None:
    ticket_id = args.get("ticket_id")
    try:
        key = live_order.ticket_key(ticket_id)
    except ValueError:
        log.warning("live_order preview: bad ticket id ignored")
        return
    if store.get(ticket_id) is not None:
        log.warning("live_order preview: ticket %s already exists; repeat ignored", ticket_id)
        return
    try:
        if not live_orders_config.preview_allowed():
            raise Refused("live orders are off (Settings, Configuration, Live orders)")
        alias = args.get("alias")
        contracts = args.get("contracts")
        limit = args.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) \
                or not math.isfinite(limit) or limit <= 0:
            raise Refused("the limit price must be a positive number")
        src = _source(args)
        symbol = src["symbol"]
        legs, mids = _priced_legs(symbol, src["legs"])
        ctype = _structure(legs)
        debit, _priced = _side(legs, mids)
        ticks = live_orders_config.ticks()
        limit = live_order.round_to_tick(float(limit), symbol, ticks)
        loss = live_order.max_loss_per_contract(legs, limit, debit)
        _check_size(contracts, None if loss is None else round(loss * contracts, 2))
        exits = _exits(legs, limit, debit, symbol)
        payload = live_order.schwab_order(
            legs, contracts=contracts, limit=limit, variant=exits["variant"], debit=debit,
            pcts=exits["pcts"], exit_tif=exits["exit_tif"], root=symbol, ticks=ticks)
        account_hash = _account_hash(alias)
    except Refused as e:
        _publish(bus, key, _refusal_view(ticket_id, str(e), args))
        return
    except ValueError as e:
        _publish(bus, key, _refusal_view(ticket_id, f"the order cannot be built: {e}", args))
        return
    try:
        status, answer = _post("/orders/preview", {"account_hash": account_hash,
                                                   "order": payload},
                               timeout=_TIMEOUT["preview"])
    except requests.RequestException:
        status, answer = 502, {"error": "no answer from the proxy"}
    answer = live_order.scrub(answer)
    verdict, messages = live_order.preview_verdict(status, answer)
    now = _now()
    ticket = {
        "ticket_id": ticket_id, "trade_id": src["trade_id"],
        "idea_posted": src["idea_posted"], "alias": alias,
        "account_hash": account_hash, "symbol": symbol, "structure": ctype,
        "contracts": contracts, "limit_price": limit, "debit": int(bool(debit)),
        "max_loss": None if loss is None else round(loss * contracts, 2),
        "payload": payload, "digest": _digest(payload), "verdict": verdict,
        "messages": messages, "answer": answer,
        "status": "previewed" if verdict == "accepted" else "refused",
        "error": None if verdict == "accepted" else "Schwab did not pass the preview",
        "created_at": now, "updated_at": now,
        "expires_at": now + live_orders_config.ticket_ttl_sec(),
    }
    if not store.insert(ticket):
        log.warning("live_order preview: ticket %s raced a repeat; kept the first", ticket_id)
        return
    _publish(bus, key, _ticket_view(ticket, {"exits": exits,
                                             "in_session": _in_session(now)}))


# ── place ────────────────────────────────────────────────────────────────────

def _matches(order, payload) -> bool:
    """An order in Schwab's list is the one sent: the same legs, sizes and price."""
    if not isinstance(order, dict):
        return False

    def legs(o):
        return sorted((str((l.get("instrument") or {}).get("symbol")), l.get("instruction"),
                       float(l.get("quantity") or 0)) for l in o.get("orderLegCollection") or ())
    try:
        return legs(order) == legs(payload) and \
            abs(float(order.get("price")) - float(payload.get("price"))) < 0.005
    except (TypeError, ValueError):
        return False


def _reconcile(ticket) -> str | None:
    """The id of the one order in the account matching a ticket whose place
    answer never came back, or None. A READ: nothing is ever sent again."""
    sent = ticket.get("sent_at") or _now()
    try:
        status, orders = _get(f"/orders/{ticket['account_hash']}", {
            "from_time": _utc_ms(sent - _RECONCILE_WINDOW_SEC),
            "to_time": _utc_ms(sent + _RECONCILE_WINDOW_SEC)}, timeout=_TIMEOUT["read"])
    except requests.RequestException:
        return None
    if status != 200 or not isinstance(orders, list):
        return None
    found = [o for o in orders if _matches(o, ticket["payload"])]
    if len(found) != 1:
        return None
    oid = found[0].get("orderId")
    return str(oid) if oid is not None else None


def _place(bus, args) -> None:
    ticket_id = args.get("ticket_id")
    try:
        key = live_order.ticket_key(ticket_id)
    except ValueError:
        log.warning("live_order place: bad ticket id ignored")
        return
    t = store.get(ticket_id)
    if t is None:
        _publish(bus, key, _refusal_view(ticket_id, "no such ticket; preview again"))
        return
    now = _now()
    reason = None
    if not live_orders_config.place_allowed():
        reason = "live orders are not on (Settings, Configuration, Live orders)"
    elif not _in_session(now):
        reason = "outside the regular session (08:30 to 15:00 Central)"
    elif t["status"] != "previewed":
        reason = f"this ticket is {t['status']}; preview again"
    elif t["expires_at"] <= now:
        reason = "the preview has expired; preview again"
    elif _digest(t["payload"]) != t["digest"]:
        reason = "the stored order does not match its preview"
    else:
        try:
            _check_size(t["contracts"], t["max_loss"])
        except Refused as e:
            reason = str(e)
    if reason is not None:
        _publish(bus, key, _ticket_view(t, {"error": reason}))
        return
    if not store.claim(ticket_id, now):
        # A second delivery of the same Send: the first owns the ticket.
        log.warning("live_order place: ticket %s already claimed; nothing sent", ticket_id)
        return
    store.update(ticket_id, sent_at=now, updated_at=now)
    try:
        status, answer = _post("/orders/place", {"account_hash": t["account_hash"],
                                                 "order": t["payload"]},
                               timeout=_TIMEOUT["place"])
    except requests.RequestException:
        status, answer = None, {"unknown": True, "error": "no answer from the proxy"}
    except Exception:  # noqa: BLE001 - after the claim, any failure is an UNKNOWN outcome
        _degrade.degraded(AREA, detail="place")
        status, answer = None, {"unknown": True, "error": "the send failed part way"}
    answer = live_order.scrub(answer) if isinstance(answer, dict) else {"error": str(answer)[:300]}
    order_id = answer.get("order_id")
    if order_id and status in (200, 201):
        fields = {"status": "sent", "order_id": str(order_id), "error": None}
    elif answer.get("unknown") or status is None or (status or 0) >= 500:
        found = _reconcile({**t, "sent_at": now})
        fields = ({"status": "sent", "order_id": found,
                   "error": "Schwab's answer never came back; the order was found in the account"}
                  if found else
                  {"status": "unknown",
                   "error": "Schwab's answer never came back. Check ThinkorSwim before "
                            "sending again: the order may be working."})
    else:
        fields = {"status": "rejected",
                  "error": str(answer.get("error") or answer.get("detail")
                               or f"Schwab answered {status}")[:500]}
    store.update(ticket_id, updated_at=_now(), **fields)
    if fields["status"] == "sent":
        log.info("live_order: ticket %s sent as order %s (%s)", ticket_id,
                 fields["order_id"], t["alias"])
    else:
        log.warning("live_order: ticket %s %s", ticket_id, fields["status"])
    _publish(bus, key, _ticket_view(store.get(ticket_id)))


# ── status ───────────────────────────────────────────────────────────────────

def _status(bus, args) -> None:
    ticket_id = args.get("ticket_id")
    try:
        key = live_order.ticket_key(ticket_id)
    except ValueError:
        return
    t = store.get(ticket_id)
    if t is None:
        return
    if t["status"] == "previewed" and t["expires_at"] <= _now():
        store.update(ticket_id, status="expired", updated_at=_now())
    elif t["status"] == "unknown":
        found = _reconcile(t)
        if found:
            store.update(ticket_id, status="sent", order_id=found, updated_at=_now(),
                         error="Schwab's answer never came back; the order was found "
                               "in the account")
    elif t["status"] == "sent" and t.get("order_id"):
        try:
            status, order = _get(f"/orders/{t['account_hash']}/{t['order_id']}",
                                 timeout=_TIMEOUT["read"])
        except requests.RequestException:
            status, order = None, None
        if status == 200 and isinstance(order, dict) and isinstance(order.get("status"), str):
            store.update(ticket_id, order_status=order["status"][:40], updated_at=_now())
    _publish(bus, key, _ticket_view(store.get(ticket_id)))


_COMMANDS = {"quote": _quote, "preview": _preview, "place": _place, "status": _status}


def handle(bus, command) -> None:
    """Answer one ``cmd:live_order`` command. A command older than
    ``max_command_age_sec``, or with no time on it, is dropped unread."""
    fn = _COMMANDS.get(command.type)
    if fn is None:
        log.warning("unknown live_order command %r ignored", command.type)
        return
    age = _age_sec(getattr(command, "ts", None))
    if age is None or age > live_orders_config.max_command_age_sec() or age < -60:
        log.warning("live_order %s dropped: %s", command.type,
                    "no time on it" if age is None else f"{age:.0f} s old")
        return
    args = command.args if isinstance(command.args, dict) else {}
    fn(bus, args)
