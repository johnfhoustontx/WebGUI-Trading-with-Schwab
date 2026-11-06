"""DELETE /orders/{hash}/{order_id}: cancel one working order (live-order
design, Phase 3).

A cancel places nothing, so it is allowed whenever live orders are not off
(turning ``live`` back to ``preview`` must leave the way to pull an order the
app already sent). It is still a write: one attempt, a 401 refreshes and asks
once more, and no answer is ``unknown`` (``cancelled: None``), never a retry.
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


class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


class _Lock:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


@pytest.fixture
def schwab(monkeypatch):
    sent, answers, refreshed = [], [], []

    def delete(url, headers=None, timeout=None):
        sent.append({"url": url, "headers": dict(headers or {})})
        a = answers.pop(0) if answers else _Resp(200)
        if isinstance(a, Exception):
            raise a
        return a

    def refuse(*a, **k):
        raise AssertionError("a cancel sent something other than one DELETE")

    mgr = types.SimpleNamespace(
        session=types.SimpleNamespace(delete=delete, post=refuse, get=refuse, put=refuse,
                                      request=refuse),
        tokens={"AccessToken": "t1"}, ensure_valid_token=lambda: None, _lock=_Lock())

    def _refresh():
        refreshed.append(1)
        mgr.tokens["AccessToken"] = "t2"

    mgr._refresh = _refresh
    monkeypatch.setattr(schwab_proxy, "token_mgr", mgr, raising=False)
    monkeypatch.setattr(schwab_proxy.api_call_counter, "record", lambda: None)
    return types.SimpleNamespace(sent=sent, answers=answers, refreshed=refreshed)


@pytest.fixture
def mode(monkeypatch):
    box = {"mode": "live"}
    monkeypatch.setattr(schwab_proxy._live_orders_config, "load", lambda: {"mode": box["mode"]})
    return box


# ── trader_cancel ────────────────────────────────────────────────────────────

def test_it_deletes_once_at_the_order(schwab):
    out = schwab_proxy.trader_cancel(HASH, "1001")
    assert out == {"status_code": 200, "cancelled": True, "error": None}
    (req,) = schwab.sent
    assert req["url"] == f"https://api.schwabapi.com/trader/v1/accounts/{HASH}/orders/1001"


def test_a_refusal_says_not_cancelled(schwab):
    schwab.answers.append(_Resp(400, '{"message":"order is not cancelable"}'))
    out = schwab_proxy.trader_cancel(HASH, "1001")
    assert out["cancelled"] is False and out["status_code"] == 400
    assert "not cancelable" in out["error"]


@pytest.mark.parametrize("answer", [requests.exceptions.ReadTimeout("slow"), _Resp(503, "x")])
def test_no_answer_is_unknown_and_never_retried(schwab, answer):
    schwab.answers.extend([answer, _Resp(200)])
    out = schwab_proxy.trader_cancel(HASH, "1001")
    assert out["cancelled"] is None
    assert len(schwab.sent) == 1


def test_a_401_refreshes_and_asks_once_more(schwab):
    schwab.answers.extend([_Resp(401), _Resp(200)])
    assert schwab_proxy.trader_cancel(HASH, "1001")["cancelled"] is True
    assert schwab.refreshed == [1]
    assert [r["headers"]["Authorization"] for r in schwab.sent] == ["Bearer t1", "Bearer t2"]


@pytest.mark.parametrize("bad_hash, bad_id", [
    (None, "1001"), ("short", "1001"), (HASH + "/../x", "1001"),
    (HASH, "abc"), (HASH, "1001/../2"), (HASH, ""), (HASH, "1" * 21), (HASH, None)])
def test_a_bad_hash_or_id_is_refused_before_any_request(schwab, bad_hash, bad_id):
    with pytest.raises(ValueError):
        schwab_proxy.trader_cancel(bad_hash, bad_id)
    assert schwab.sent == []


# ── the route ────────────────────────────────────────────────────────────────

def test_the_route_refuses_while_off(schwab, mode):
    mode["mode"] = "off"
    with pytest.raises(HTTPException) as err:
        schwab_proxy.cancel_order(HASH, "1001")
    assert err.value.status_code == 403
    assert schwab.sent == []


def test_the_route_reads_the_shipped_switch_as_off(schwab):
    with pytest.raises(HTTPException) as err:
        schwab_proxy.cancel_order(HASH, "1001")
    assert err.value.status_code == 403
    assert schwab.sent == []


@pytest.mark.parametrize("m", ["preview", "live"])
def test_the_route_cancels_in_preview_and_live(schwab, mode, m):
    mode["mode"] = m
    resp = schwab_proxy.cancel_order(HASH, "1001")
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"cancelled": True, "error": None}


def test_the_route_answers_400_on_a_bad_id(schwab, mode):
    with pytest.raises(HTTPException) as err:
        schwab_proxy.cancel_order(HASH, "x1")
    assert err.value.status_code == 400
    assert schwab.sent == []


# ── over HTTP ────────────────────────────────────────────────────────────────

SECRET = "s3cret-for-tests"


def test_http_cancel_needs_the_secret(schwab, mode, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(schwab_proxy, "PROXY_SHARED_SECRET", SECRET)
    client = TestClient(schwab_proxy.app)
    assert client.delete(f"/orders/{HASH}/1001").status_code == 401
    assert schwab.sent == []
    r = client.delete(f"/orders/{HASH}/1001", headers={"X-Proxy-Secret": SECRET})
    assert r.status_code == 200 and r.json()["cancelled"] is True
