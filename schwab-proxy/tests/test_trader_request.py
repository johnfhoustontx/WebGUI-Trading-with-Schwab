"""Regression tests for trader_request header handling.

Schwab's Trader API rejects a `Content-Type: application/json` header on a
bodyless GET with an opaque 400 ({"errors":[{"status":500,...}]}), which
silently broke /accounts, /positions and /transactions. These tests pin the
fix: GETs must NOT carry a Content-Type, and the POST path must still forward
its JSON body (requests sets Content-Type from json= automatically).
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import schwab_proxy  # noqa: E402


class _FakeResp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data if data is not None else []
        self.text = text

    def json(self):
        return self._data


class _FakeTokenMgr:
    tokens = {"AccessToken": "tok"}

    def ensure_valid_token(self):
        pass


def _wire(monkeypatch, capture):
    monkeypatch.setattr(schwab_proxy, "token_mgr", _FakeTokenMgr(), raising=False)

    def fake_get(url, headers=None, timeout=None):
        capture["method"] = "GET"
        capture["headers"] = headers
        capture["url"] = url
        return _FakeResp(200, [{"hashValue": "h"}])

    def fake_post(url, headers=None, json=None, timeout=None):
        capture["method"] = "POST"
        capture["headers"] = headers
        capture["json"] = json
        capture["url"] = url
        return _FakeResp(201, {"ok": True})

    monkeypatch.setattr(schwab_proxy.requests, "get", fake_get)
    monkeypatch.setattr(schwab_proxy.requests, "post", fake_post)


def test_trader_get_omits_content_type(monkeypatch):
    cap = {}
    _wire(monkeypatch, cap)
    res = schwab_proxy.trader_request("GET", "/accounts/accountNumbers")
    assert res["status_code"] == 200
    # The bug: a Content-Type on a bodyless GET → Schwab 400. Guard against it.
    assert "Content-Type" not in cap["headers"]
    assert cap["headers"]["Authorization"].startswith("Bearer ")
    assert cap["headers"]["Accept"] == "application/json"


def test_trader_post_forwards_json_body(monkeypatch):
    cap = {}
    _wire(monkeypatch, cap)
    body = {"orderType": "MARKET"}
    res = schwab_proxy.trader_request("POST", "/accounts/x/orders", json_body=body)
    assert res["status_code"] == 201
    assert cap["json"] == body
    # requests sets Content-Type from json=; we don't set it statically.
    assert "Content-Type" not in cap["headers"]
