"""POST /orders/preview: ask Schwab to CHECK an order, and place nothing.

Phase 0 of docs/plans/2026-11-05-live-order-to-thinkorswim-design.md. The route
is off unless config/live_orders.toml says ``mode = "preview"``, and the request
it sends goes to exactly one Schwab endpoint, ``previewOrder``.
"""
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi import HTTPException

import schwab_proxy

HASH = "A1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D6"
ORDER = {"orderType": "LIMIT", "price": "1.50", "orderStrategyType": "SINGLE"}


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.text = json.dumps(body) if body is not None else ""
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def schwab(monkeypatch):
    """A fake Schwab session that records every request and answers from a queue."""
    sent = []
    answers = []

    def post(url, headers=None, data=None, timeout=None):
        sent.append({"method": "POST", "url": url, "headers": dict(headers or {}),
                     "body": json.loads(data)})
        return answers.pop(0) if answers else _Resp(200, {"orderValidationResult": {}})

    def refuse(*a, **k):
        raise AssertionError("the preview made a request that was not a POST")

    refreshed = []
    mgr = types.SimpleNamespace(
        session=types.SimpleNamespace(post=post, get=refuse, put=refuse,
                                      delete=refuse, request=refuse),
        tokens={"AccessToken": "t1"},
        ensure_valid_token=lambda: None,
        _lock=types.SimpleNamespace(__enter__=lambda s: None, __exit__=lambda s, *a: None),
    )

    class _Lock:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False

    mgr._lock = _Lock()

    def _refresh():
        refreshed.append(1)
        mgr.tokens["AccessToken"] = "t2"

    mgr._refresh = _refresh
    monkeypatch.setattr(schwab_proxy, "token_mgr", mgr, raising=False)
    monkeypatch.setattr(schwab_proxy.api_call_counter, "record", lambda: None)
    return types.SimpleNamespace(sent=sent, answers=answers, refreshed=refreshed)


@pytest.fixture
def preview_on(monkeypatch):
    monkeypatch.setattr(schwab_proxy._live_orders_config, "preview_allowed", lambda: True)


# ── trader_preview ───────────────────────────────────────────────────────────

def test_it_posts_the_order_to_preview_order_and_nowhere_else(schwab):
    out = schwab_proxy.trader_preview(HASH, ORDER)
    assert out == {"status_code": 200, "data": {"orderValidationResult": {}}, "error": None}
    (req,) = schwab.sent
    assert req["url"] == f"https://api.schwabapi.com/trader/v1/accounts/{HASH}/previewOrder"
    assert req["body"] == ORDER
    assert req["headers"]["Authorization"] == "Bearer t1"
    assert req["headers"]["Content-Type"] == "application/json"


def test_a_rejection_keeps_schwabs_reasons(schwab):
    schwab.answers.append(_Resp(400, {"message": "stop orders not allowed"}))
    out = schwab_proxy.trader_preview(HASH, ORDER)
    assert out["status_code"] == 400
    assert out["data"] == {"message": "stop orders not allowed"}
    assert "stop orders not allowed" in out["error"]


def test_a_401_refreshes_the_token_and_asks_once_more(schwab):
    schwab.answers.extend([_Resp(401, None), _Resp(200, {"ok": 1})])
    out = schwab_proxy.trader_preview(HASH, ORDER)
    assert out["status_code"] == 200
    assert schwab.refreshed == [1]
    assert [r["headers"]["Authorization"] for r in schwab.sent] == ["Bearer t1", "Bearer t2"]


def test_a_server_error_is_not_retried(schwab):
    schwab.answers.extend([_Resp(503, {"e": 1}), _Resp(200, {"ok": 1})])
    assert schwab_proxy.trader_preview(HASH, ORDER)["status_code"] == 503
    assert len(schwab.sent) == 1


@pytest.mark.parametrize("bad_hash", [
    None, "", "short", "../orders", HASH + "/../orders", HASH + "?x=1",
    "A" * 129, 12345678901234567,
])
def test_a_bad_account_hash_is_refused_before_any_request(schwab, bad_hash):
    with pytest.raises(ValueError):
        schwab_proxy.trader_preview(bad_hash, ORDER)
    assert schwab.sent == []


@pytest.mark.parametrize("bad_order", [None, {}, [], "LIMIT", {"x": "y" * 20_000}])
def test_a_bad_order_is_refused_before_any_request(schwab, bad_order):
    with pytest.raises(ValueError):
        schwab_proxy.trader_preview(HASH, bad_order)
    assert schwab.sent == []


# ── the route ────────────────────────────────────────────────────────────────

def test_the_route_refuses_while_live_orders_are_off(schwab, monkeypatch):
    monkeypatch.setattr(schwab_proxy._live_orders_config, "preview_allowed", lambda: False)
    with pytest.raises(HTTPException) as err:
        schwab_proxy.preview_order({"account_hash": HASH, "order": ORDER})
    assert err.value.status_code == 403
    assert schwab.sent == []


def test_the_route_reads_the_shipped_switch_as_off(schwab):
    """No monkeypatch: the tracked config file ships ``mode = "off"``."""
    with pytest.raises(HTTPException) as err:
        schwab_proxy.preview_order({"account_hash": HASH, "order": ORDER})
    assert err.value.status_code == 403
    assert schwab.sent == []


def test_the_route_passes_schwabs_answer_and_status_through(schwab, preview_on):
    schwab.answers.append(_Resp(400, {"message": "no"}))
    resp = schwab_proxy.preview_order({"account_hash": HASH, "order": ORDER})
    assert resp.status_code == 400
    assert json.loads(resp.body) == {"schwab": {"message": "no"},
                                     "error": json.dumps({"message": "no"})}


def test_the_route_answers_400_on_a_bad_request(schwab, preview_on):
    with pytest.raises(HTTPException) as err:
        schwab_proxy.preview_order({"account_hash": "nope", "order": ORDER})
    assert err.value.status_code == 400
    assert schwab.sent == []


def test_health_reports_the_live_orders_mode():
    src = pathlib.Path(schwab_proxy.__file__).read_text(encoding="utf-8")
    assert '"live_orders": _live_orders_config.mode()' in src


# ── over HTTP: the secret, the switch, the body ─────────────────────────────

SECRET = "s3cret-for-tests"


@pytest.fixture
def client(schwab, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(schwab_proxy, "PROXY_SHARED_SECRET", SECRET)
    return TestClient(schwab_proxy.app)


def test_http_without_the_secret_is_refused(client, schwab, preview_on):
    body = {"account_hash": HASH, "order": ORDER}
    assert client.post("/orders/preview", json=body).status_code == 401
    assert client.post("/orders/preview", json=body,
                       headers={"X-Proxy-Secret": "wrong"}).status_code == 401
    assert schwab.sent == []


def test_http_with_no_secret_configured_refuses_everyone(client, schwab, preview_on,
                                                         monkeypatch):
    monkeypatch.setattr(schwab_proxy, "PROXY_SHARED_SECRET", None)
    r = client.post("/orders/preview", json={"account_hash": HASH, "order": ORDER},
                    headers={"X-Proxy-Secret": SECRET})
    assert r.status_code == 503
    assert schwab.sent == []


def test_http_with_the_secret_and_the_switch_off_is_refused(client, schwab):
    r = client.post("/orders/preview", json={"account_hash": HASH, "order": ORDER},
                    headers={"X-Proxy-Secret": SECRET})
    assert r.status_code == 403
    assert schwab.sent == []


def test_http_with_the_secret_and_the_switch_on_previews(client, schwab, preview_on):
    r = client.post("/orders/preview", json={"account_hash": HASH, "order": ORDER},
                    headers={"X-Proxy-Secret": SECRET})
    assert r.status_code == 200
    assert r.json() == {"schwab": {"orderValidationResult": {}}, "error": None}
    assert [s["url"].rsplit("/", 1)[1] for s in schwab.sent] == ["previewOrder"]
