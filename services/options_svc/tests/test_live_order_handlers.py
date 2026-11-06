"""cmd:live_order: a trade -> a Schwab order (live-order design, Phases 1-3).

Nothing here reaches the network or a real store: the proxy is a fake answering
from a table, the Ledger row is handed in, and the ticket store is a temp file.
"""
import ast
import datetime as _dt
import json
import pathlib
import types
from zoneinfo import ZoneInfo

import pytest
import requests

from services.options_svc import live_order_handlers as h
from services.options_svc import live_order_store as store
from shared import live_order, live_orders_config

CT = ZoneInfo("America/Chicago")
NOW = _dt.datetime(2026, 11, 6, 10, 0, tzinfo=CT).timestamp()      # a Friday, in session
EXP = "2026-11-06"
TID = "a" * 32
TRADE = "ab12cd34"
POSTED = "2026-11-06T09:00:00-06:00"


def _contract(symbol, bid, ask):
    return [{"symbol": symbol, "bid": bid, "ask": ask}]


CHAIN = {
    "underlyingPrice": 102.0,
    "callExpDateMap": {f"{EXP}:0": {
        "100.0": _contract("SPY   261106C00100000", 2.40, 2.60),
        "105.0": _contract("SPY   261106C00105000", 0.90, 1.10)}},
    "putExpDateMap": {f"{EXP}:0": {
        "95.0": _contract("SPY   261106P00095000", 0.70, 0.80),
        "90.0": _contract("SPY   261106P00090000", 0.20, 0.30)}},
}


def _row(**kw):
    row = {"trade_id": TRADE, "source": "trade_idea", "status": "OPEN", "symbol": "SPY",
           "strategy": "BULL_CALL_SPREAD", "expiration": EXP, "idea_posted": POSTED,
           "idea_label": "SPY bull call spread", "entry_credit": -1.40,
           "legs": [{"kind": "call", "side": "long", "strike": 100, "qty": 1, "expiration": EXP},
                    {"kind": "call", "side": "short", "strike": 105, "qty": 1, "expiration": EXP}]}
    row.update(kw)
    return row


class FakeBus:
    def __init__(self):
        self.views = {}

    def cache_set(self, key, payload, event=None, skip_unchanged=False, ttl=None):
        self.views[key] = json.loads(json.dumps(payload))
        return 1


class FakeProxy:
    """Answers the handler's ``_get`` / ``_post`` from a table, recording each call."""
    def __init__(self):
        self.calls = []
        self.place = (201, {"order_id": "9001", "unknown": False, "error": None})
        self.listed = []
        self.order = {"status": "WORKING", "cancelable": True}
        self.cancel = (200, {"cancelled": True, "error": None})

    def get(self, path, params=None, *, timeout):
        self.calls.append(("GET", path, params))
        if path == "/chains":
            return 200, CHAIN
        if path == "/accounts":
            return 200, [{"accountNumber": "55551191", "hashValue": "HASH-1191"},
                         {"accountNumber": "55556763", "hashValue": "HASH-6763"},
                         {"accountNumber": "55552878", "hashValue": "HASH-2878"}]
        if path == "/orders/HASH-1191":
            return 200, self.listed
        if path.startswith("/orders/HASH-1191/"):
            return 200, self.order
        return 404, {"detail": "no route"}

    def post(self, path, body, *, timeout):
        self.calls.append(("POST", path, body))
        if path == "/orders/preview":
            return 200, {"schwab": {"orderStrategy": {"accountNumber": "55551191"},
                                    "orderValidationResult": {"warns": [{"message": "wide market"}]}},
                         "error": None}
        if path == "/orders/place":
            if isinstance(self.place, Exception):
                raise self.place
            return self.place
        return 404, {"detail": "no route"}

    def delete(self, path, *, timeout):
        self.calls.append(("DELETE", path, None))
        if isinstance(self.cancel, Exception):
            raise self.cancel
        return self.cancel

    def deletes(self):
        return [c for c in self.calls if c[0] == "DELETE"]

    def posts(self, path):
        return [c for c in self.calls if c[0] == "POST" and c[1] == path]


CFG = {**live_orders_config.DEFAULTS, "mode": "live",
       "accounts": [{"last4": "1191", "alias": "Trading account", "default": True},
                    {"last4": "6763", "alias": "IRA"}]}


@pytest.fixture
def env(monkeypatch, tmp_path):
    proxy = FakeProxy()
    rows = {TRADE: _row()}
    clock = {"now": NOW}
    cfg = dict(CFG)
    monkeypatch.setattr(live_orders_config, "load", lambda: cfg)
    monkeypatch.setattr(h, "_get", proxy.get)
    monkeypatch.setattr(h, "_post", proxy.post)
    monkeypatch.setattr(h, "_delete", proxy.delete)
    monkeypatch.setattr(h, "_ledger_row", lambda tid: rows.get(tid))
    monkeypatch.setattr(h, "_now", lambda: clock["now"])
    monkeypatch.setattr(h, "_exit_settings", lambda: {
        "quantity": 1, "exit_tif": "GTC",
        "debit": {"target_pct": 100.0, "stop_pct": 50.0},
        "credit": {"target_pct": 50.0, "stop_pct": 100.0}})
    monkeypatch.setattr(store.repo_paths, "LIVE_ORDERS_DB", tmp_path / "live_orders.db")
    return types.SimpleNamespace(proxy=proxy, rows=rows, clock=clock, cfg=cfg, bus=FakeBus())


def _cmd(type_, args, age=0.0, ts="now", base=NOW):
    if ts == "now":
        ts = _dt.datetime.fromtimestamp(base - age, _dt.timezone.utc).isoformat()
    return types.SimpleNamespace(type=type_, args=args, ts=ts)


def _preview(env, **kw):
    args = {"ticket_id": TID, "trade_id": TRADE, "idea_posted": POSTED,
            "alias": "Trading account", "contracts": 2, "limit": 1.50, **kw}
    h.handle(env.bus, _cmd("preview", args, base=env.clock["now"]))
    return env.bus.views.get(live_order.ticket_key(args["ticket_id"]))


def _place(env, ticket_id=TID):
    h.handle(env.bus, _cmd("place", {"ticket_id": ticket_id}, base=env.clock["now"]))
    return env.bus.views.get(live_order.ticket_key(ticket_id))


def _no_account_digits(view):
    text = json.dumps(view)
    for secret in ("HASH-", "5555", "1191", "6763"):
        assert secret not in text, secret


# ── quote ────────────────────────────────────────────────────────────────────

def test_quote_prices_the_idea_from_a_fresh_chain(env):
    h.handle(env.bus, _cmd("quote", {"trade_id": TRADE}))
    v = env.bus.views[live_order.quote_key(TRADE)]
    assert "error" not in v
    assert v["live_price"] == 1.50                      # 2.50 - 1.00, on the penny step
    assert v["debit"] is True and v["structure"] == "VERTICAL"
    assert v["max_loss_per_contract"] == 150.0
    assert v["max_profit_per_contract"] == 350.0
    assert v["default_contracts"] == 6                  # 6 x $150 = $900 <= $1,000
    assert v["exits"]["stop_on_order"] is False         # a spread: target only
    assert v["exits"]["target"] == 3.00
    assert v["aliases"] == ["Trading account", "IRA"]
    assert v["drift_pct"] == 7.1 and v["drift_warn"] is False
    (chain_call,) = [c for c in env.proxy.calls if c[1] == "/chains"]
    assert chain_call[2]["maxAge"] == 0
    _no_account_digits(v)


def test_quote_says_why_it_cannot_price(env):
    env.rows[TRADE] = _row(legs=[{"kind": "call", "side": "long", "strike": 110, "qty": 1}])
    h.handle(env.bus, _cmd("quote", {"trade_id": TRADE}))
    assert "not in today's chain" in env.bus.views[live_order.quote_key(TRADE)]["error"]


# ── preview ──────────────────────────────────────────────────────────────────

def test_preview_stores_a_ticket_and_publishes_the_alias_only(env):
    v = _preview(env)
    assert v["status"] == "previewed" and v["verdict"] == "accepted"
    assert v["alias"] == "Trading account" and v["contracts"] == 2 and v["limit"] == 1.50
    assert v["messages"] == ["warn: wide market"]
    assert v["max_loss"] == 300.0
    _no_account_digits(v)
    t = store.get(TID)
    assert t["account_hash"] == "HASH-1191"
    assert t["payload"]["orderStrategyType"] == "TRIGGER"
    assert len(t["payload"]["childOrderStrategies"]) == 1       # target, no stop
    assert t["answer"]["schwab"]["orderStrategy"]["accountNumber"] == "…1191"
    assert t["expires_at"] == NOW + 120
    (sent,) = env.proxy.posts("/orders/preview")
    assert sent[2]["account_hash"] == "HASH-1191"


def test_preview_goes_to_the_account_chosen(env):
    _preview(env, alias="IRA")
    assert store.get(TID)["account_hash"] == "HASH-6763"


@pytest.mark.parametrize("change, said", [
    ({"alias": "Brokerage 2878"}, "not one orders may go to"),
    ({"contracts": 51}, "more than 50 contracts"),
    ({"contracts": 34}, "ceiling"),                          # 34 x $150 = $5,100
    ({"contracts": 0}, "whole number"),
    ({"contracts": 2.0}, "whole number"),
    ({"limit": -1}, "positive"),
    ({"idea_posted": "2026-11-05T09:00:00-06:00"}, "not the one"),
    ({"trade_id": "zz999999"}, "no trade idea"),
])
def test_preview_refusals(env, change, said):
    v = _preview(env, **change)
    assert v["status"] == "refused" and said in v["error"]
    assert store.get(TID) is None
    assert env.proxy.posts("/orders/preview") == []


def test_preview_refuses_with_the_switch_off(env):
    env.cfg["mode"] = "off"
    assert "off" in _preview(env)["error"]
    assert env.proxy.calls == []


def test_preview_refuses_a_feed_symbol_and_sends_a_phase_2_butterfly(env):
    env.rows[TRADE] = _row(symbol="FOP.ES")
    assert "/ES" in _preview(env)["error"]
    # Held as two middle legs: merged to the 1-2-1 Schwab takes.
    env.rows[TRADE] = _row(legs=[
        {"kind": "call", "side": "long", "strike": 95, "qty": 1, "expiration": EXP},
        {"kind": "call", "side": "short", "strike": 100, "qty": 1, "expiration": EXP},
        {"kind": "call", "side": "short", "strike": 100, "qty": 1, "expiration": EXP},
        {"kind": "call", "side": "long", "strike": 105, "qty": 1, "expiration": EXP}])
    CHAIN["callExpDateMap"][f"{EXP}:0"]["95.0"] = _contract("SPY   261106C00095000", 7.0, 7.2)
    try:
        v = _preview(env, ticket_id="b" * 32, limit=1.0)
    finally:
        del CHAIN["callExpDateMap"][f"{EXP}:0"]["95.0"]
    assert v["status"] == "previewed" and v["structure"] == "BUTTERFLY"
    payload = store.get("b" * 32)["payload"]
    assert payload["complexOrderStrategyType"] == "BUTTERFLY"
    assert sorted(l["quantity"] for l in payload["orderLegCollection"]) == [2, 2, 4]


def test_a_repeated_preview_changes_nothing(env):
    _preview(env)
    _preview(env, contracts=5)
    assert store.get(TID)["contracts"] == 2
    assert len(env.proxy.posts("/orders/preview")) == 1


def test_a_rejected_preview_cannot_be_sent(env):
    def rejecting(path, body, *, timeout):
        return 200, {"schwab": {"orderValidationResult": {
            "rejects": [{"message": "not approved"}]}}, "error": None}
    env.proxy.post = rejecting
    h._post = rejecting
    v = _preview(env)
    assert v["status"] == "refused" and v["verdict"] == "rejected"


# ── place ────────────────────────────────────────────────────────────────────

def test_place_sends_the_stored_order_once(env):
    _preview(env)
    v = _place(env)
    assert v["status"] == "sent" and v["order_id"] == "9001"
    (sent,) = env.proxy.posts("/orders/place")
    assert sent[2] == {"account_hash": "HASH-1191", "order": store.get(TID)["payload"]}
    _no_account_digits(v)
    _place(env)                                          # a second delivery
    assert len(env.proxy.posts("/orders/place")) == 1


def test_place_never_takes_the_order_from_the_command(env):
    _preview(env)
    h.handle(env.bus, _cmd("place", {"ticket_id": TID, "order": {"price": "0.05"},
                                     "account_hash": "HASH-2878"}))
    (sent,) = env.proxy.posts("/orders/place")
    assert sent[2]["account_hash"] == "HASH-1191"
    assert sent[2]["order"]["price"] == "1.50"


@pytest.mark.parametrize("setup, said", [
    (lambda e: e.cfg.update(mode="preview"), "not on"),
    (lambda e: e.clock.update(now=NOW + 121), "expired"),
    (lambda e: e.clock.update(now=_dt.datetime(2026, 11, 6, 15, 30, tzinfo=CT).timestamp()),
     "outside the regular session"),
    (lambda e: e.cfg.update(max_contracts=1), "more than 1 contracts"),
])
def test_place_refusals_send_nothing(env, setup, said):
    _preview(env)
    setup(env)
    v = _place(env)
    assert said in v["error"]
    assert env.proxy.posts("/orders/place") == []
    assert store.get(TID)["status"] == "previewed"


def test_place_refuses_an_altered_stored_order(env):
    _preview(env)
    conn = store.connect()
    with conn:
        conn.execute("UPDATE tickets SET payload = ? WHERE ticket_id = ?",
                     (json.dumps({"price": "0.05"}), TID))
    conn.close()
    assert "does not match" in _place(env)["error"]
    assert env.proxy.posts("/orders/place") == []


def test_no_answer_is_unknown_and_never_sent_again(env):
    _preview(env)
    env.proxy.place = requests.exceptions.ReadTimeout("slow")
    v = _place(env)
    assert v["status"] == "unknown" and "Check ThinkorSwim" in v["error"]
    _place(env)
    h.handle(env.bus, _cmd("status", {"ticket_id": TID}))
    assert len(env.proxy.posts("/orders/place")) == 1


def test_an_unknown_order_found_in_the_account_is_recorded(env):
    _preview(env)
    payload = store.get(TID)["payload"]
    env.proxy.place = (502, {"order_id": None, "unknown": True, "error": "timeout"})
    env.proxy.listed = [{"orderId": 4242, "price": 1.5,
                         "orderLegCollection": payload["orderLegCollection"]},
                        {"orderId": 1, "price": 2.0,
                         "orderLegCollection": payload["orderLegCollection"]}]
    v = _place(env)
    assert v["status"] == "sent" and v["order_id"] == "4242"
    (listed,) = [c for c in env.proxy.calls if c[1] == "/orders/HASH-1191"]
    assert listed[2]["from_time"].endswith("Z") and listed[2]["to_time"].endswith("Z")


def test_two_matches_stay_unknown(env):
    _preview(env)
    payload = store.get(TID)["payload"]
    env.proxy.place = (503, {"order_id": None, "unknown": True, "error": "5xx"})
    env.proxy.listed = [{"orderId": n, "price": "1.50",
                         "orderLegCollection": payload["orderLegCollection"]} for n in (1, 2)]
    assert _place(env)["status"] == "unknown"


def test_a_refusal_from_schwab_is_rejected(env):
    _preview(env)
    env.proxy.place = (400, {"order_id": None, "unknown": False, "error": "buying power"})
    v = _place(env)
    assert v["status"] == "rejected" and "buying power" in v["error"]


# ── status ───────────────────────────────────────────────────────────────────

def test_status_reads_a_sent_order(env):
    _preview(env)
    _place(env)
    env.proxy.order = {"status": "FILLED"}
    h.handle(env.bus, _cmd("status", {"ticket_id": TID}))
    assert env.bus.views[live_order.ticket_key(TID)]["order_status"] == "FILLED"


def test_status_expires_an_old_preview(env):
    _preview(env)
    env.clock["now"] = NOW + 500
    h.handle(env.bus, _cmd("status", {"ticket_id": TID}, base=env.clock["now"]))
    assert store.get(TID)["status"] == "expired"


# ── cancel (Phase 3) ─────────────────────────────────────────────────────────

def _cancel(env, ticket_id=TID):
    h.handle(env.bus, _cmd("cancel", {"ticket_id": ticket_id}, base=env.clock["now"]))
    return env.bus.views.get(live_order.ticket_key(ticket_id))


def test_cancel_deletes_the_tickets_own_order_once(env):
    _preview(env)
    _place(env)
    assert env.bus.views[live_order.ticket_key(TID)]["can_cancel"] is True
    v = _cancel(env)
    assert env.proxy.deletes() == [("DELETE", "/orders/HASH-1191/9001", None)]
    assert "Cancel sent" in v["error"]
    _no_account_digits(v)


def test_cancel_reads_schwab_first_and_sends_nothing_when_it_cannot(env):
    _preview(env)
    _place(env)
    env.proxy.order = {"status": "FILLED", "cancelable": False}
    v = _cancel(env)
    assert env.proxy.deletes() == []
    assert "FILLED" in v["error"] and v["order_status"] == "FILLED"
    assert v["can_cancel"] is False


@pytest.mark.parametrize("setup, said", [
    (lambda env: None, "no such ticket"),
    (lambda env: _preview(env), "previewed"),
    (lambda env: (_preview(env), env.cfg.update(mode="off")), "off"),
])
def test_cancel_refusals_send_nothing(env, setup, said):
    setup(env)
    v = _cancel(env)
    assert env.proxy.deletes() == []
    assert said in v["error"]


def test_cancel_works_after_live_is_turned_back_to_preview(env):
    _preview(env)
    _place(env)
    env.cfg["mode"] = "preview"
    _cancel(env)
    assert len(env.proxy.deletes()) == 1


@pytest.mark.parametrize("answer, said", [
    ((400, {"cancelled": False, "error": "not cancelable"}), "refused the cancel"),
    (requests.ConnectionError("down"), "No answer"),
    ((503, {"cancelled": None, "error": "x"}), "No answer"),
])
def test_a_cancel_that_did_not_take_says_so(env, answer, said):
    _preview(env)
    _place(env)
    env.proxy.cancel = answer
    v = _cancel(env)
    assert said in v["error"]
    assert len(env.proxy.deletes()) == 1


# ── today's orders (Phase 3) ─────────────────────────────────────────────────

def test_today_lists_the_days_sent_orders_by_alias(env):
    _preview(env)
    _place(env)
    _preview(env, ticket_id="b" * 32, contracts=1)              # previewed, never sent
    env.proxy.order = {"status": "FILLED"}
    h.handle(env.bus, _cmd("today", {}, base=env.clock["now"]))
    view = env.bus.views[live_order.TODAY_KEY]
    (row,) = view["orders"]
    assert row["ticket_id"] == TID and row["alias"] == "Trading account"
    assert row["order_status"] == "FILLED" and row["can_cancel"] is False
    assert row["contracts"] == 2 and row["structure"] == "VERTICAL"
    _no_account_digits(view)
    assert "HASH" not in json.dumps(view)


def test_today_starts_at_midnight_central(env):
    _preview(env)
    _place(env)
    env.clock["now"] = NOW + 24 * 3600
    h.handle(env.bus, _cmd("today", {}, base=env.clock["now"]))
    assert env.bus.views[live_order.TODAY_KEY]["orders"] == []


def test_a_send_republishes_the_days_list(env):
    _preview(env)
    _place(env)
    assert [r["ticket_id"] for r in env.bus.views[live_order.TODAY_KEY]["orders"]] == [TID]


# ── the age gate ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    _cmd("place", {"ticket_id": TID}, age=31),
    _cmd("place", {"ticket_id": TID}, ts=None),
    _cmd("place", {"ticket_id": TID}, ts="yesterday"),
    _cmd("place", {"ticket_id": TID}, ts="2026-11-06T16:00:00"),      # no zone
    _cmd("launch", {"ticket_id": TID}),
])
def test_old_timeless_and_unknown_commands_are_dropped(env, cmd):
    _preview(env)
    h.handle(env.bus, cmd)
    assert env.proxy.posts("/orders/place") == []
    assert store.get(TID)["status"] == "previewed"


# ── source pins ──────────────────────────────────────────────────────────────

SRC = pathlib.Path(h.__file__).read_text(encoding="utf-8")


def test_it_imports_nothing_from_compute():
    tree = ast.parse(SRC)
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
             for a in n.names}
    assert "compute" not in names and not any("compute" in (m or "") for m in mods)


def test_it_writes_only_its_own_views():
    tree = ast.parse(SRC)
    sets = [ast.unparse(n.args[0]) for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "cache_set"]
    assert sets == ["key"]                                       # inside _publish
    published = {ast.unparse(n.args[1]) for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_publish"}
    assert published == {"key", "live_order.TODAY_KEY"}
    keys = {ast.unparse(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "key"
                                                 for t in n.targets)}
    assert keys == {"live_order.quote_key(view_id)", "live_order.ticket_key(ticket_id)"}


def test_the_ticket_view_never_names_the_hash():
    assert "account_hash" not in h._TICKET_FIELDS


# ── Phase 2: a trade from any detail panel ───────────────────────────────────

QID = "c" * 32
FAR = "2026-12-18"
FAR_CHAIN = {
    "callExpDateMap": {f"{EXP}:0": CHAIN["callExpDateMap"][f"{EXP}:0"],
                       f"{FAR}:42": {"100.0": _contract("SPY   261218C00100000", 4.40, 4.60)}},
    "putExpDateMap": CHAIN["putExpDateMap"],
}


def _spec(legs=None, **kw):
    s = {"symbol": "SPY", "label": "SPY Put Credit Spread", "price": 0.50,
         "legs": legs or [
             {"kind": "put", "side": "short", "strike": 95, "qty": 1, "expiration": EXP},
             {"kind": "put", "side": "long", "strike": 90, "qty": 1, "expiration": EXP}]}
    s.update(kw)
    return s


def _spec_quote(env, spec):
    h.handle(env.bus, _cmd("quote", {"quote_id": QID, "spec": spec}, base=env.clock["now"]))
    return env.bus.views[live_order.quote_key(QID)]


def test_a_panels_trade_is_quoted_and_previewed_from_its_own_legs(env):
    v = _spec_quote(env, _spec())
    assert "error" not in v, v.get("error")
    assert v["structure"] == "VERTICAL" and v["debit"] is False
    assert v["live_price"] == 0.50 and v["price_source"] == "panel"
    assert v["idea_label"] == "SPY Put Credit Spread"
    p = _preview(env, trade_id=None, idea_posted=None, quote_id=QID, spec=_spec(),
                 limit=0.50)
    assert p["status"] == "previewed"
    t = store.get(TID)
    assert t["trade_id"] == QID and t["idea_posted"] is None
    assert t["payload"]["orderType"] == "NET_CREDIT"
    _no_account_digits(p)


@pytest.mark.parametrize("spec, said", [
    (_spec(symbol="FOP.ES"), "/ES"),
    (_spec(symbol="BAD KEY"), "ticker"),
    (_spec(legs=[{"kind": "call", "side": "short", "strike": 100, "expiration": EXP}]),
     "no limit"),
    (_spec(legs=[{"kind": "call", "side": "long", "strike": 100, "expiration": EXP},
                 {"kind": "call", "side": "short", "strike": 100, "expiration": FAR}]),
     "outlives"),
])
def test_a_panels_trade_is_checked_as_strictly_as_an_idea(env, spec, said):
    v = _spec_quote(env, spec)
    assert said in v["error"]
    p = _preview(env, trade_id=None, idea_posted=None, quote_id=QID, spec=spec)
    assert p["status"] == "refused" and said in p["error"]
    assert env.proxy.posts("/orders/preview") == []


def test_a_panels_trade_needs_the_pages_own_quote_id(env):
    h.handle(env.bus, _cmd("quote", {"quote_id": "not-hex", "spec": _spec()}))
    assert env.bus.views == {}
    p = _preview(env, trade_id=None, quote_id="ab12cd34", spec=_spec())
    assert "id cannot be read" in p["error"]


def test_a_calendar_reads_both_expiries_from_one_chain(env, monkeypatch):
    calls = []

    def get(path, params=None, *, timeout):
        calls.append(params)
        return (200, FAR_CHAIN) if path == "/chains" else env.proxy.get(path, params,
                                                                        timeout=timeout)
    monkeypatch.setattr(h, "_get", get)
    cal = _spec(legs=[{"kind": "call", "side": "short", "strike": 100, "expiration": EXP},
                      {"kind": "call", "side": "long", "strike": 100, "expiration": FAR}])
    v = _spec_quote(env, cal)
    assert "error" not in v, v.get("error")
    assert v["structure"] == "CALENDAR" and v["debit"] is True
    assert v["live_price"] == 2.00                         # 4.50 - 2.50
    assert v["max_loss_per_contract"] == 200.0             # the debit
    assert v["max_profit_per_contract"] is None
    assert v["expirations"] == [EXP, FAR]
    assert (calls[0]["fromDate"], calls[0]["toDate"]) == (EXP, FAR)


def test_a_custom_order_that_buys_and_sells_reads_its_side_off_the_mids(env):
    ratio = _spec(legs=[{"kind": "call", "side": "long", "strike": 100, "expiration": EXP},
                        {"kind": "call", "side": "short", "strike": 105, "qty": 2,
                         "expiration": EXP}])
    # 2.50 - 2 x 1.00 = 0.50 paid... but more calls are sold than bought.
    assert "no limit" in _spec_quote(env, ratio)["error"]
    back = _spec(legs=[{"kind": "call", "side": "short", "strike": 100, "expiration": EXP},
                       {"kind": "call", "side": "long", "strike": 105, "qty": 2,
                        "expiration": EXP}])
    v = _spec_quote(env, back)
    assert v["structure"] == "CUSTOM"
    assert v["debit"] is False and v["live_price"] == 0.50      # 2.50 in, 2.00 out
