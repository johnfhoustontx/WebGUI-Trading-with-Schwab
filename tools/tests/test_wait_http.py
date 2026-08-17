"""``tools/wait_http.py`` — the probe that replaced the bare TCP connect.

The property under test is the one the TCP probe got WRONG on 2026-08-16: a
bound-but-not-accepting socket must read as DOWN, and any HTTP answer — 307,
404, 500 — must read as UP.
"""
import http.server
import threading
import urllib.error

import pytest

from tools import wait_http


# --- what counts as "answered" ----------------------------------------------

class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_2xx_is_alive(monkeypatch):
    monkeypatch.setattr(wait_http.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert wait_http.probe("http://x/") is True


@pytest.mark.parametrize("code", [301, 307, 404, 500])
def test_any_http_status_is_alive(monkeypatch, code):
    """The server SPOKE HTTP, so its accept loop works — which is the whole
    question. The proxy 404s on `/` and the web GUI 307s there (it redirects to
    the Market Dashboard); demanding 200 would turn this into a route test."""
    def _boom(*a, **k):
        raise urllib.error.HTTPError("http://x/", code, "nope", {}, None)

    monkeypatch.setattr(wait_http.urllib.request, "urlopen", _boom)
    assert wait_http.probe("http://x/") is True


@pytest.mark.parametrize("exc", [ConnectionRefusedError, ConnectionResetError,
                                 TimeoutError, OSError])
def test_transport_failures_are_not_alive(monkeypatch, exc):
    def _boom(*a, **k):
        raise exc("down")

    monkeypatch.setattr(wait_http.urllib.request, "urlopen", _boom)
    assert wait_http.probe("http://x/") is False


# --- the regression this exists for -----------------------------------------

def test_a_bound_socket_that_never_accepts_reads_as_DOWN():
    """The 2026-08-16 failure, reproduced: listen() without accept().

    A TCP connect SUCCEEDS against this socket — the OS completes the handshake
    from the backlog — which is exactly why the old probe passed while the web
    GUI served nothing. The HTTP probe must time out instead.
    """
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)                       # bound + listening, never accepts
    port = srv.getsockname()[1]
    try:
        # the OLD probe's question — "can I connect?" — says yes
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
        # the NEW probe's question — "will it answer?" — says no
        assert wait_http.probe(wait_http.url_for_port(port), timeout=1.5) is False
    finally:
        srv.close()


def test_probe_succeeds_against_a_real_server():
    """Power check for the test above: a server that DOES accept reads as UP."""
    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert wait_http.probe(wait_http.url_for_port(srv.server_port)) is True
    finally:
        srv.shutdown()
        srv.server_close()


# --- polling ----------------------------------------------------------------

def test_wait_for_probes_before_sleeping(monkeypatch):
    """An already-up server must not pay the first interval."""
    monkeypatch.setattr(wait_http, "probe", lambda *a, **k: True)
    slept = []
    assert wait_http.wait_for("http://x/", sleep=slept.append) is True
    assert slept == []


def test_wait_for_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def _probe(*a, **k):
        calls["n"] += 1
        return calls["n"] >= 3

    monkeypatch.setattr(wait_http, "probe", _probe)
    assert wait_http.wait_for("http://x/", sleep=lambda s: None) is True
    assert calls["n"] == 3


def test_wait_for_gives_up_at_the_deadline(monkeypatch):
    monkeypatch.setattr(wait_http, "probe", lambda *a, **k: False)
    ticks = iter([0.0, 1.0, 2.0, 3.0, 99.0])
    assert wait_http.wait_for("http://x/", timeout=2.0, sleep=lambda s: None,
                              clock=lambda: next(ticks)) is False


# --- CLI --------------------------------------------------------------------

def test_cli_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(wait_http, "wait_for", lambda *a, **k: True)
    assert wait_http.main(["wait_http", "--port", "8500"]) == 0
    monkeypatch.setattr(wait_http, "wait_for", lambda *a, **k: False)
    assert wait_http.main(["wait_http", "--port", "8500", "--label", "web GUI"]) == 1
    assert "web GUI" in capsys.readouterr().out


def test_cli_requires_a_target():
    with pytest.raises(SystemExit):
        wait_http.main(["wait_http"])


def test_url_for_port_builds_a_loopback_url():
    assert wait_http.url_for_port(8100) == "http://127.0.0.1:8100/"
    assert wait_http.url_for_port(8100, path="/health") == "http://127.0.0.1:8100/health"
