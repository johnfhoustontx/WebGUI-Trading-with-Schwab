"""The collection poll's requests reach the limiter marked as priority.

Three hops, each of which can silently drop the mark: the client sends
``X-Priority``, the handler reads it for the duration of ONE request, and
``TokenManager._rate_limit`` hands it to the gate. See test_rate_gate.py for
what the gate does with it, and audit PF-02 for why.
"""
import pathlib
import sys
import types

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402
import proxy_client  # noqa: E402
import rate_gate  # noqa: E402
import schwab_proxy as sp  # noqa: E402


# ----------------------------------------------------------------- the client

class _Resp:
    status_code = 200
    headers = {}

    def json(self):
        return {"ok": True}


class _Session:
    def __init__(self):
        self.headers, self.sent = {}, []

    def get(self, url, **kw):
        self.sent.append(kw)
        return _Resp()


def _client():
    c = proxy_client.SchwabPyProxyClient("http://proxy")
    c.session = _Session()
    return c


def test_the_client_says_it_can_mark_a_request():
    assert proxy_client.SchwabPyProxyClient.supports_priority is True


def test_a_priority_chain_request_carries_the_header():
    c = _client()
    c.get_option_chain("SPY", contract_type="ALL", priority=True)
    assert c.session.sent[0]["headers"] == {"X-Priority": "1"}


def test_an_ordinary_request_is_sent_exactly_as_before():
    """No ``headers`` argument at all: a session double that predates the lane
    still works, and nothing rides on a header nobody asked for."""
    c = _client()
    c.get_option_chain("SPY", contract_type="ALL")
    c.get_quotes(["SPY"])
    assert all("headers" not in kw for kw in c.session.sent)


def test_the_mark_is_not_a_request_parameter():
    """It must never reach Schwab, and never change which stored chain matches."""
    c = _client()
    c.get_option_chain("SPY", contract_type="ALL", priority=True)
    assert "priority" not in c.session.sent[0]["params"]


# ---------------------------------------------------------------- the handler

class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), (" 1 ", True), ("TRUE", True),
    ("0", False), ("", False), ("no", False), (None, False), ("2", False),
])
def test_only_an_explicit_mark_is_read_as_priority(value, expected):
    headers = {} if value is None else {"x-priority": value}
    assert sp._wants_priority(_Req(headers)) is expected


class _Gateway:
    degrades = {}

    def __init__(self):
        self.lanes = []

    def chains(self, params, caller, max_age=None):
        self.lanes.append(sp._PRIORITY_REQUEST.get())
        return ms.Served("pass", 0.0, data={"ok": 1})

    def quotes(self, symbols, caller, max_age=None):
        self.lanes.append(sp._PRIORITY_REQUEST.get())
        return ms.Served("pass", 0.0, data={"ok": 1})


@pytest.fixture
def gateway(monkeypatch):
    g = _Gateway()
    monkeypatch.setattr(sp, "_GATEWAY", g)
    monkeypatch.setattr(sp, "_CALLERS_SEEN", {"unknown"})
    return g


def test_a_marked_chain_request_is_fetched_in_the_priority_lane(gateway):
    client = TestClient(sp.app)
    assert client.get("/chains?symbol=SPY", headers={"X-Priority": "1"}).status_code == 200
    assert gateway.lanes == [True]


def test_an_unmarked_chain_request_is_ordinary(gateway):
    client = TestClient(sp.app)
    client.get("/chains?symbol=SPY")
    assert gateway.lanes == [False]


def test_the_mark_lasts_for_one_request_only(gateway):
    """The handlers run on pooled threads. A mark that outlived its request
    would promote whatever that thread served next."""
    client = TestClient(sp.app)
    for _ in range(6):
        client.get("/chains?symbol=SPY", headers={"X-Priority": "1"})
    for _ in range(6):
        client.get("/chains?symbol=SPY")
        client.get("/quotes?symbols=SPY")
    assert gateway.lanes == [True] * 6 + [False] * 12
    assert sp._PRIORITY_REQUEST.get() is False


def test_a_marked_quote_request_is_priority_too(gateway):
    TestClient(sp.app).get("/quotes?symbols=SPY", headers={"X-Priority": "1"})
    assert gateway.lanes == [True]


def test_the_mark_is_cleared_when_the_fetch_fails(monkeypatch):
    class _Failing(_Gateway):
        def chains(self, params, caller, max_age=None):
            raise ms.UpstreamError(502, "down")

    monkeypatch.setattr(sp, "_GATEWAY", _Failing())
    monkeypatch.setattr(sp, "_CALLERS_SEEN", {"unknown"})
    with pytest.raises(Exception):
        sp.get_option_chain(_Req({"x-priority": "1"}), "SPY")
    assert sp._PRIORITY_REQUEST.get() is False


# ---------------------------------------------------------------- the limiter

class _Gate:
    def __init__(self):
        self.seen = []

    def acquire(self, priority=False):
        self.seen.append(priority)


def test_the_limiter_passes_the_requests_lane_to_the_gate(monkeypatch):
    monkeypatch.setattr(sp.api_call_counter, "record", lambda *a, **k: None)
    obj = types.SimpleNamespace(_gate=_Gate())
    sp.TokenManager._rate_limit(obj)
    token = sp._PRIORITY_REQUEST.set(True)
    try:
        sp.TokenManager._rate_limit(obj)
    finally:
        sp._PRIORITY_REQUEST.reset(token)
    assert obj._gate.seen == [False, True]


def test_every_call_is_still_counted_once(monkeypatch):
    counted = []
    monkeypatch.setattr(sp.api_call_counter, "record", lambda *a, **k: counted.append(1))
    obj = types.SimpleNamespace(_gate=_Gate())
    sp.TokenManager._rate_limit(obj)
    sp.TokenManager._rate_limit(obj)
    assert len(counted) == 2


def test_the_gate_reads_its_run_length_from_the_settings(monkeypatch):
    monkeypatch.setattr(sp._marketdata_config, "section",
                        lambda name: {"priority_run": 7} if name == "limiter" else {})
    assert sp._new_gate().run_length() == 7


def test_the_gate_uses_the_proxys_request_interval(monkeypatch):
    monkeypatch.setattr(sp, "MIN_REQUEST_INTERVAL", 0.37)
    assert sp._new_gate().interval() == 0.37


def test_the_shipped_setting_is_the_gates_own_default():
    from shared import marketdata_config
    assert marketdata_config.DEFAULTS["limiter"]["priority_run"] == rate_gate.DEFAULT_PRIORITY_RUN
    assert marketdata_config.section("limiter")["priority_run"] == rate_gate.DEFAULT_PRIORITY_RUN
