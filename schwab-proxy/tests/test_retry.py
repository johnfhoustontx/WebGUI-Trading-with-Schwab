"""Bounded-retry behavior for the proxy's Schwab calls.

Reads (TokenManager.api_request and trader_request GET) retry transient
failures up to MAX_RETRIES with backoff. Order POSTs do NOT retry — a lost
response on a submitted order must never be re-sent. time.sleep is patched out
so these run instantly.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import schwab_proxy  # noqa: E402


class _FakeResp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data if data is not None else {}
        self.text = text

    def json(self):
        return self._data


class _FakeSession:
    """session.get returning a scripted sequence of responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls += 1
        # Repeat the last scripted response if the sequence runs short.
        idx = min(self.calls - 1, len(self._responses) - 1)
        r = self._responses[idx]
        if isinstance(r, Exception):
            raise r
        return r


class _FakeTokenMgr:
    """Minimal stand-in for TokenManager to drive api_request directly."""

    def __init__(self, session):
        self.session = session
        self.tokens = {"AccessToken": "tok"}
        self._lock = __import__("threading").Lock()

    def ensure_valid_token(self):
        pass

    def _rate_limit(self):
        pass

    def _refresh(self):
        pass


def _no_sleep(monkeypatch):
    monkeypatch.setattr(schwab_proxy.time, "sleep", lambda *_a, **_k: None)


# ---------------------------------------------------------------- api_request

def test_api_request_recovers_after_transient(monkeypatch):
    _no_sleep(monkeypatch)
    sess = _FakeSession([
        _FakeResp(502, text="bad gateway"),
        _FakeResp(502, text="bad gateway"),
        _FakeResp(200, data={"ok": True}),
    ])
    mgr = _FakeTokenMgr(sess)
    res = schwab_proxy.TokenManager.api_request(mgr, "/quotes", params={"symbols": "SPY"})
    assert res["status_code"] == 200
    assert res["data"] == {"ok": True}
    assert sess.calls == 3


def test_api_request_gives_up_and_returns_last_error(monkeypatch):
    _no_sleep(monkeypatch)
    sess = _FakeSession([_FakeResp(404, text="not found")])
    mgr = _FakeTokenMgr(sess)
    res = schwab_proxy.TokenManager.api_request(mgr, "/quotes", params={"symbols": "ZZZZ"})
    assert res["status_code"] == 404
    assert res["data"] is None
    assert "not found" in res["error"]
    assert sess.calls == schwab_proxy.MAX_RETRIES


def test_api_request_no_retry_on_success(monkeypatch):
    _no_sleep(monkeypatch)
    sess = _FakeSession([_FakeResp(200, data={"ok": True})])
    mgr = _FakeTokenMgr(sess)
    res = schwab_proxy.TokenManager.api_request(mgr, "/quotes")
    assert res["status_code"] == 200
    assert sess.calls == 1


# -------------------------------------------------------------- trader_request

class _TraderSession:
    """Stand-in for token_mgr.session (trader_request now uses the pooled session)."""

    def __init__(self, counters, get_responses, post_responses):
        self._c = counters
        self._get = get_responses
        self._post = post_responses

    def get(self, url, headers=None, timeout=None):
        self._c["get"] += 1
        seq = self._get or [_FakeResp(200, data=[{"hashValue": "h"}], text="x")]
        return seq[min(self._c["get"] - 1, len(seq) - 1)]

    def post(self, url, headers=None, json=None, timeout=None):
        self._c["post"] += 1
        seq = self._post or [_FakeResp(201, data={"ok": True}, text="x")]
        return seq[min(self._c["post"] - 1, len(seq) - 1)]


class _TraderTokenMgr:
    tokens = {"AccessToken": "tok"}
    _lock = __import__("threading").Lock()

    def ensure_valid_token(self):
        pass

    def _refresh(self):
        pass


def _wire_trader(monkeypatch, get_responses=None, post_responses=None):
    counters = {"get": 0, "post": 0}
    tm = _TraderTokenMgr()
    tm.session = _TraderSession(counters, get_responses, post_responses)
    monkeypatch.setattr(schwab_proxy, "token_mgr", tm, raising=False)
    return counters


def test_trader_get_recovers_after_transient(monkeypatch):
    _no_sleep(monkeypatch)
    counters = _wire_trader(monkeypatch, get_responses=[
        _FakeResp(503, text="unavailable"),
        _FakeResp(200, data=[{"hashValue": "h"}], text="x"),
    ])
    res = schwab_proxy.trader_request("GET", "/accounts/accountNumbers")
    assert res["status_code"] == 200
    assert counters["get"] == 2


def test_trader_post_is_not_retried(monkeypatch):
    _no_sleep(monkeypatch)
    counters = _wire_trader(monkeypatch, post_responses=[_FakeResp(502, text="bad gateway")])
    res = schwab_proxy.trader_request("POST", "/accounts/h/orders", json_body={"x": 1})
    assert res["status_code"] == 502
    assert counters["post"] == 1  # exactly one attempt — no duplicate submission
