"""``tools/check_stack_up`` — the post-restart verifier promote.bat branches on.

The rules under test are the two that matter operationally: an HTTP probe (NOT a
socket connect) decides health, and an unconfirmable answer reads as NOT up.
Everything binds a real server on an ephemeral port rather than touching the live
stack, whose state would otherwise decide the results.
"""
import http.server
import socket
import threading

import pytest

from tools import check_stack_up as csu


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):  # noqa: N802
        self.send_response(self.status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_a):
        pass


@pytest.fixture
def serving():
    """Start a real HTTP server; yield (port, set_status). Torn down after."""
    started = []

    def _start(status=200):
        cls = type("H", (_Handler,), {"status": status})
        srv = http.server.HTTPServer(("127.0.0.1", 0), cls)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return srv.server_address[1]

    yield _start
    for srv in started:
        srv.shutdown()


# ---- the load-bearing distinction: HTTP, not TCP ---------------------------

def test_a_bound_socket_that_never_answers_is_NOT_healthy():
    """The whole reason this module speaks HTTP.

    A listening socket with no accept loop completes a TCP handshake, so
    ``check_stack_down``-style probing would call it up. This app has had a UI
    outage where exactly that happened: the port answered and the service did not.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)          # bound and listening, but nothing ever reads
    port = sock.getsockname()[1]
    try:
        assert csu.is_healthy("options_svc", port, timeout=1.0) is False
    finally:
        sock.close()


def test_a_serving_port_is_healthy(serving):
    port = serving(200)
    assert csu.is_healthy("options_svc", port) is True


def test_a_closed_port_is_not_healthy():
    assert csu.is_healthy("options_svc", _free_port(), timeout=1.0) is False


def test_a_4xx_still_counts_as_up_but_a_5xx_does_not(serving):
    """A 404 means the process is serving and disagrees about the path; a 500
    means it is serving and broken. Only the second is 'not up'."""
    assert csu.is_healthy("options_svc", serving(404)) is True
    assert csu.is_healthy("options_svc", serving(503)) is False


# ---- routing ---------------------------------------------------------------

def test_the_webgui_probes_root_and_everything_else_probes_health():
    """NiceGUI exposes no /health; the services do. A wrong path here would make
    a healthy web GUI look dead on every promote."""
    assert csu.health_url("webgui", 8500) == "http://127.0.0.1:8500/"
    assert csu.health_url("proxy", 8100) == "http://127.0.0.1:8100/health"
    assert csu.health_url("options_svc", 8211) == "http://127.0.0.1:8211/health"


def test_no_port_literals_live_in_this_module():
    """The port set must come from stop_all._targets(), so the starter, stopper
    and verifier cannot drift apart."""
    src = (csu.__file__ and open(csu.__file__, encoding="utf-8").read()) or ""
    body = src.split('"""', 2)[-1]          # skip the module docstring
    for literal in ("8100", "8210", "8211", "8500", "9500"):
        assert literal not in body, f"{literal} is hard-coded; use _targets()"


# ---- aggregation and the deadline -----------------------------------------

def test_unhealthy_targets_names_only_the_ones_that_are_down(serving):
    up = serving(200)
    down = _free_port()
    result = csu.unhealthy_targets([("options_svc", up), ("webgui", down)])
    assert result == [("webgui", down)]


def test_unhealthy_targets_tolerates_a_malformed_entry(serving):
    port = serving(200)
    assert csu.unhealthy_targets([("options_svc", port), None, ("bad",)]) == []


def test_wait_until_up_returns_empty_as_soon_as_everything_answers(serving):
    port = serving(200)
    slept = []
    assert csu.wait_until_up([("options_svc", port)], deadline_sec=30,
                             sleep=slept.append, monotonic=lambda: 0.0) == []
    assert slept == [], "should not sleep when the first probe already passes"


def test_wait_until_up_gives_up_at_the_deadline_and_reports_what_is_down():
    port = _free_port()
    ticks = iter([0.0, 1.0, 2.0, 99.0])
    down = csu.wait_until_up([("webgui", port)], deadline_sec=5,
                             sleep=lambda _s: None, monotonic=lambda: next(ticks))
    assert down == [("webgui", port)]


# ---- degradation: the INVERSE of check_stack_down --------------------------

def test_main_reports_failure_when_the_targets_cannot_be_read(monkeypatch, capsys):
    """check_stack_down degrades to 'proceed' because its worst case is a
    duplicate process. This module's worst case is announcing a good promote over
    a dead trading stack, so an unreadable answer must NOT exit 0."""
    monkeypatch.setattr(csu, "_targets",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert csu.main([]) == 1
    assert "UNCONFIRMED" in capsys.readouterr().out


def test_failure_text_says_the_code_is_already_promoted():
    """Re-running promote after a failed restart is the wrong move — the pull
    already happened. The message has to say so."""
    text = csu.failure_text([("webgui", 8500)], env_name="prod", root="D:/x")
    assert "PROD" in text
    assert "already" in text.lower() or "IS promoted" in text
    assert "8500" in text
