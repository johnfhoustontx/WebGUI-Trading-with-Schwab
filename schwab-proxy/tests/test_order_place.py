"""POST /orders/place and the two order reads (live-order design, Phase 1).

The place route is the one way this proxy can put an order in a Schwab account.
It is off unless config/live_orders.toml says ``mode = "live"``, it sends one
request to exactly one endpoint, and it never retries once the request may have
reached Schwab: an answer it did not get is ``unknown``, never a failure.
Nothing here reaches the network.
"""
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
import requests
from fastapi import HTTPException

import schwab_proxy

HASH = "A1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D6"
ORDER = {"orderType": "LIMIT", "price": "1.50", "orderStrategyType": "SINGLE"}


class _Resp:
    def __init__(self, status, body=None, location=None):
        self.status_code = status
        self.text = json.dumps(body) if body is not None else ""
        self._body = body
        self.headers = {"Location": location} if location else {}

    def json(self):
        return self._body


class _Lock:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


@pytest.fixture
def schwab(monkeypatch):
    """A fake Schwab session: records requests, answers from a queue (an
    exception in the queue is raised)."""
    sent, answers, refreshed = [], [], []

    def _answer():
        a = answers.pop(0) if answers else _Resp(
            201, location=f"https://api.schwabapi.com/trader/v1/accounts/{HASH}/orders/1001")
        if isinstance(a, Exception):
            raise a
        return a

    def post(url, headers=None, data=None, timeout=None):
        sent.append({"method": "POST", "url": url, "headers": dict(headers or {}),
                     "body": json.loads(data)})
        return _answer()

    def get(url, headers=None, timeout=None, **k):
        sent.append({"method": "GET", "url": url})
        return _answer()

    def refuse(*a, **k):
        raise AssertionError("an unexpected request method")

    mgr = types.SimpleNamespace(
        session=types.SimpleNamespace(post=post, get=get, put=refuse, delete=refuse,
                                      request=refuse),
        tokens={"AccessToken": "t1"}, ensure_valid_token=lambda: None, _lock=_Lock())

    def _refresh():
        refreshed.append(1)
        mgr.tokens["AccessToken"] = "t2"

    mgr._refresh = _refresh
    monkeypatch.setattr(schwab_proxy, "token_mgr", mgr, raising=False)
    monkeypatch.setattr(schwab_proxy.api_call_counter, "record", lambda: None)
    monkeypatch.setattr(schwab_proxy.time, "sleep", lambda s: None)
    return types.SimpleNamespace(sent=sent, answers=answers, refreshed=refreshed)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(schwab_proxy._live_orders_config, "place_allowed", lambda: True)


# ── trader_place ─────────────────────────────────────────────────────────────

def test_it_posts_once_to_the_orders_endpoint_and_reads_the_order_id(schwab):
    out = schwab_proxy.trader_place(HASH, ORDER)
    assert out == {"status_code": 201, "order_id": "1001", "unknown": False, "error": None}
    (req,) = schwab.sent
    assert req["url"] == f"https://api.schwabapi.com/trader/v1/accounts/{HASH}/orders"
    assert req["body"] == ORDER


def test_accepted_with_no_order_id_is_unknown(schwab):
    schwab.answers.append(_Resp(201))
    out = schwab_proxy.trader_place(HASH, ORDER)
    assert out["unknown"] is True and out["order_id"] is None


def test_no_answer_is_unknown_and_never_retried(schwab):
    schwab.answers.extend([requests.exceptions.ReadTimeout("slow"), _Resp(201, location="x/2")])
    out = schwab_proxy.trader_place(HASH, ORDER)
    assert out["unknown"] is True and out["status_code"] == 502
    assert len(schwab.sent) == 1


def test_a_server_error_is_unknown_and_never_retried(schwab):
    schwab.answers.extend([_Resp(503, {"e": 1}), _Resp(201, location="x/2")])
    out = schwab_proxy.trader_place(HASH, ORDER)
    assert out["unknown"] is True and out["status_code"] == 503
    assert len(schwab.sent) == 1


def test_a_refusal_is_not_unknown(schwab):
    schwab.answers.append(_Resp(400, {"message": "buying power"}))
    out = schwab_proxy.trader_place(HASH, ORDER)
    assert out["unknown"] is False and out["status_code"] == 400
    assert "buying power" in out["error"]


def test_a_401_refreshes_and_asks_once_more(schwab):
    schwab.answers.extend([_Resp(401), _Resp(201, location="x/orders/77")])
    out = schwab_proxy.trader_place(HASH, ORDER)
    assert out["order_id"] == "77"
    assert schwab.refreshed == [1]
    assert [r["headers"]["Authorization"] for r in schwab.sent] == ["Bearer t1", "Bearer t2"]


@pytest.mark.parametrize("bad_hash", [None, "", "short", HASH + "/../x", "A" * 129])
def test_a_bad_hash_is_refused_before_any_request(schwab, bad_hash):
    with pytest.raises(ValueError):
        schwab_proxy.trader_place(bad_hash, ORDER)
    assert schwab.sent == []


@pytest.mark.parametrize("location, want", [
    ("https://api.schwabapi.com/trader/v1/accounts/H/orders/1234567890", "1234567890"),
    ("/orders/55/", "55"), ("/orders/abc", None), ("", None), (None, None)])
def test_order_id_from_location(location, want):
    assert schwab_proxy._order_id_from(location) == want


# ── the route ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["off", "preview"])
def test_the_route_refuses_unless_live(schwab, monkeypatch, mode):
    monkeypatch.setattr(schwab_proxy._live_orders_config, "load", lambda: {"mode": mode})
    with pytest.raises(HTTPException) as err:
        schwab_proxy.place_order({"account_hash": HASH, "order": ORDER})
    assert err.value.status_code == 403
    assert schwab.sent == []


def test_the_route_reads_the_shipped_switch_as_off(schwab):
    with pytest.raises(HTTPException) as err:
        schwab_proxy.place_order({"account_hash": HASH, "order": ORDER})
    assert err.value.status_code == 403
    assert schwab.sent == []


def test_the_route_places_when_live(schwab, live):
    resp = schwab_proxy.place_order({"account_hash": HASH, "order": ORDER})
    assert resp.status_code == 201
    assert json.loads(resp.body) == {"order_id": "1001", "unknown": False, "error": None}


def test_the_route_answers_400_on_a_bad_request(schwab, live):
    with pytest.raises(HTTPException) as err:
        schwab_proxy.place_order({"account_hash": HASH, "order": "LIMIT"})
    assert err.value.status_code == 400
    assert schwab.sent == []


# ── the two reads ────────────────────────────────────────────────────────────

def test_get_order_reads_one_order(schwab):
    schwab.answers.append(_Resp(200, {"status": "WORKING", "orderId": 1001}))
    assert schwab_proxy.get_order(HASH, "1001") == {"status": "WORKING", "orderId": 1001}
    assert schwab.sent[0]["url"].endswith(f"/accounts/{HASH}/orders/1001")


@pytest.mark.parametrize("order_id", ["abc", "1001/../x", "", "1" * 21])
def test_get_order_refuses_a_bad_id(schwab, order_id):
    with pytest.raises(HTTPException) as err:
        schwab_proxy.get_order(HASH, order_id)
    assert err.value.status_code == 400
    assert schwab.sent == []


def test_list_orders_passes_the_window(schwab):
    schwab.answers.append(_Resp(200, [{"orderId": 1}]))
    out = schwab_proxy.list_orders(HASH, "2026-11-06T14:30:00.000Z", "2026-11-06T14:40:00.000Z")
    assert out == [{"orderId": 1}]
    url = schwab.sent[0]["url"]
    assert f"/accounts/{HASH}/orders?fromEnteredTime=2026-11-06T14%3A30%3A00.000Z" in url
    assert "toEnteredTime=2026-11-06T14%3A40%3A00.000Z" in url


@pytest.mark.parametrize("t", ["2026-11-06", "2026-11-06T14:30:00", "now", "2026-11-06T14:30:00Z&x=1"])
def test_list_orders_refuses_a_bad_time(schwab, t):
    with pytest.raises(HTTPException) as err:
        schwab_proxy.list_orders(HASH, t, "2026-11-06T14:40:00.000Z")
    assert err.value.status_code == 400
    assert schwab.sent == []


# ── over HTTP ────────────────────────────────────────────────────────────────

SECRET = "s3cret-for-tests"


@pytest.fixture
def client(schwab, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(schwab_proxy, "PROXY_SHARED_SECRET", SECRET)
    return TestClient(schwab_proxy.app)


def test_http_place_needs_the_secret(client, schwab, live):
    body = {"account_hash": HASH, "order": ORDER}
    assert client.post("/orders/place", json=body).status_code == 401
    assert schwab.sent == []


def test_http_place_with_the_secret_and_live(client, schwab, live):
    r = client.post("/orders/place", json={"account_hash": HASH, "order": ORDER},
                    headers={"X-Proxy-Secret": SECRET})
    assert r.status_code == 201 and r.json()["order_id"] == "1001"


def test_http_reads_need_the_secret(client, schwab):
    assert client.get(f"/orders/{HASH}/1001").status_code == 401
    assert client.get(f"/orders/{HASH}", params={
        "from_time": "2026-11-06T14:30:00.000Z",
        "to_time": "2026-11-06T14:40:00.000Z"}).status_code == 401
    assert schwab.sent == []
