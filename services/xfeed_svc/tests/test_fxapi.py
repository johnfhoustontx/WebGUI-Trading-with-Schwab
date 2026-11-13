import json
import socket
import threading
import time

import pytest
import requests

from services.xfeed_svc import config
from services.xfeed_svc.fxapi import FxClient


class Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self.text = "" if body is None else body if isinstance(body, str) else json.dumps(body)
        self.closed = False

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, queue):
        self.queue = list(queue)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        nxt = self.queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def ok(n, bottom="next"):
    return Resp(200, {"code": 200, "results": [{"id": str(i)} for i in range(n)],
                      "cursor": {"top": "t", "bottom": bottom}})


def nf():
    return Resp(404, {"code": 404, "results": [], "cursor": {"top": None, "bottom": None}})


def client(queue, **over):
    sleeps = []
    cfg = {**config.DEFAULTS["fxembed"], **over}
    s = FakeSession(queue)
    return FxClient(cfg, session=s, sleep=sleeps.append), s, sleeps


def test_retries_404_then_succeeds():
    fx, s, sleeps = client([nf(), nf(), ok(2)])
    r = fx.fetch_statuses("nasa")
    assert r["status"] == "ok" and len(r["results"]) == 2
    assert r["cursor_bottom"] == "next"
    assert len(s.calls) == 3
    assert sleeps == [0.5, 0.5]


def test_404_four_times_is_not_found():
    fx, s, _ = client([nf(), nf(), nf(), nf()])
    assert fx.fetch_statuses("nobody") == {"status": "not_found"}
    assert len(s.calls) == 4


def test_200_with_body_code_404_counts_as_404():
    fx, s, _ = client([Resp(200, {"code": 404, "results": []}), ok(1)])
    assert fx.fetch_statuses("nasa")["status"] == "ok"
    assert len(s.calls) == 2


def test_500_is_error_without_retry():
    fx, s, _ = client([Resp(500, "oops")])
    assert fx.fetch_statuses("nasa") == {"status": "error", "error": "HTTP 500"}
    assert len(s.calls) == 1


def test_429_is_rate_limited():
    fx, _, _ = client([Resp(429, "")])
    assert fx.fetch_statuses("nasa") == {"status": "error", "error": "rate limited"}


def test_204_is_ok_with_no_results():
    fx, _, _ = client([Resp(204)])
    assert fx.fetch_statuses("nasa") == {"status": "ok", "results": [], "cursor_bottom": None}


def test_builds_url_with_replies_and_cursor_sends_user_agent():
    fx, s, _ = client([ok(0)])
    fx.fetch_statuses("nasa", cursor="a b")
    assert s.calls[0]["url"] == "https://api.fxtwitter.com/2/profile/nasa/statuses?with_replies=1&cursor=a%20b"
    assert s.calls[0]["headers"]["User-Agent"] == config.DEFAULTS["fxembed"]["user_agent"]
    assert s.calls[0]["headers"]["Accept"] == "application/json"
    assert s.calls[0]["timeout"] == 15


def test_with_replies_false_omits_flag():
    fx, s, _ = client([ok(0)])
    fx.fetch_statuses("nasa", with_replies=False)
    assert s.calls[0]["url"] == "https://api.fxtwitter.com/2/profile/nasa/statuses"


def test_connection_error_is_network_error():
    fx, _, _ = client([requests.ConnectionError("ECONNRESET")])
    assert fx.fetch_statuses("nasa") == {"status": "error", "error": "network: ECONNRESET"}


def test_requests_timeout_is_timeout():
    fx, _, _ = client([requests.ReadTimeout("slow")])
    assert fx.fetch_statuses("nasa") == {"status": "error", "error": "timeout"}


def test_non_json_200_is_error():
    fx, _, _ = client([Resp(200, "<html>")])
    assert fx.fetch_statuses("nasa") == {"status": "error", "error": "bad response from FxEmbed"}


def test_api_code_not_200_is_error():
    fx, _, _ = client([Resp(200, {"code": 500, "results": []})])
    assert fx.fetch_statuses("nasa") == {"status": "error", "error": "API 500"}


def test_unexpected_exception_never_raises():
    fx, _, _ = client([RuntimeError("boom")])
    assert fx.fetch_statuses("nasa") == {"status": "error", "error": "network: boom"}


@pytest.mark.allow_network
def test_a_dripping_server_is_cut_by_the_total_deadline(monkeypatch):
    """Real loopback socket, default session: a server dripping one byte per
    step never trips requests' per-read timeout, so only the total deadline
    (3 x timeout_s) ends it - as 'timeout', not a hang or a short body."""
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    stop = threading.Event()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(5)

    def serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            try:
                conn.recv(65536)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
                while not stop.wait(0.1):
                    conn.sendall(b"x")
            except OSError:
                return

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    cfg = {**config.DEFAULTS["fxembed"], "timeout_s": 0.5,
           "base_url": f"http://127.0.0.1:{srv.getsockname()[1]}", "not_found_retries": 0}
    try:
        t0 = time.monotonic()
        r = FxClient(cfg).fetch_statuses("nasa")
        elapsed = time.monotonic() - t0
    finally:
        stop.set()
        srv.close()
        t.join(5)
    assert r == {"status": "error", "error": "timeout"}
    assert elapsed < 3.0


def test_unexpected_fault_is_counted_and_still_returned_as_error():
    from services import _degrade
    _degrade.reset()
    fx, _s, _sl = client([])
    fx._once = lambda url: (_ for _ in ()).throw(ValueError("handle=secret"))
    assert fx.fetch_statuses("nasa") == {"status": "error", "error": "network: handle=secret"}
    assert _degrade.counts() == {"xfeed.fxapi": 1}
