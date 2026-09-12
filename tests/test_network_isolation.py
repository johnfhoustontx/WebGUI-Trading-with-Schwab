"""The suite must not be able to reach a real HTTP server.

History: ``paper_trader.add_trade`` POSTs every new paper trade to the proxy's
stream tracker at ``repo_paths.PROXY_URL`` (``trade_tracker_client.track``), and
several suites call ``add_trade`` with nothing stubbed. Off the prod box that is
a 1.5-second timeout per row; **on it, where the proxy is up, the suite registers
fake trades with the LIVE stream tracker.** ``options-scanner/daily_trade_log.py``
reaches the same proxy through ``urllib`` with a 90-second timeout.

It is the network twin of the 2026-07-16 incident the SQLite guard beside this
one exists for, and it takes the same lesson: stubbing the tracker per test has
to be remembered forever, while a guard at the chokepoint covers a module added
tomorrow. These tests pin that guard.

**Two chokepoints, because there are two HTTP stacks in production code:**

* ``requests.adapters.HTTPAdapter.send`` — below ``requests.Session``, which
  matters: eight production modules hold a persistent ``requests.Session()``, so
  patching ``requests.post`` would have missed most of them;
* ``urllib.request.urlopen`` — ``daily_trade_log``, ``earnings_history``,
  ``edgar_fundamentals`` and ``tools/wait_http`` use it.

⚠ **Unlike the SQLite guard, this one raises each stack's OWN connection
error**, and that is deliberate. A database has no "store is down" path the code
handles, so a loud ``RuntimeError`` is right there. An HTTP server does — every
call site already catches ``requests.RequestException`` / ``URLError`` and
degrades — so raising the native type makes a test take exactly the path
production takes when the proxy is unreachable. A ``RuntimeError`` would escape
those ``except`` clauses and force a per-test stub at every site, which is the
per-site patching the SQLite post-mortem says does not scale.
"""
import urllib.error
import urllib.request

import pytest
import requests

from conftest import NetworkBlockedInTest, UrllibBlockedInTest


# ── the requests stack ────────────────────────────────────────────────────

def test_a_requests_POST_to_the_proxy_is_blocked():
    """The leak that motivated this: trade_tracker_client.track."""
    from repo_paths import PROXY_URL
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.post(f"{PROXY_URL}/track", json={"trade_id": "x"}, timeout=1.5)


def test_a_persistent_SESSION_is_blocked_too():
    """⚠ The reason the patch sits below Session. Eight production modules keep
    a long-lived ``requests.Session()``; a ``requests.post`` patch misses all of
    them."""
    s = requests.Session()
    with pytest.raises(requests.exceptions.ConnectionError):
        s.get("http://127.0.0.1:8100/health", timeout=1.5)


def test_an_EXTERNAL_host_is_blocked_too():
    """Hermetic means hermetic: a test that passes only because it reached a
    third-party API is depending on machine state and the weather."""
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("https://api.telegram.org/", timeout=1.5)


def test_the_requests_error_is_the_NATIVE_type_callers_already_catch():
    """``trade_tracker_client`` catches ``RequestException``. If this raised
    anything outside that hierarchy, ``add_trade`` would blow up instead of
    degrading — the opposite of what production does with the proxy down."""
    assert issubclass(NetworkBlockedInTest, requests.exceptions.ConnectionError)
    assert issubclass(NetworkBlockedInTest, requests.exceptions.RequestException)


def test_the_requests_error_names_the_URL_and_the_way_out():
    """If it ever surfaces in a traceback it must explain itself."""
    with pytest.raises(NetworkBlockedInTest) as exc:
        requests.get("http://127.0.0.1:8211/health", timeout=1.5)
    msg = str(exc.value)
    assert "127.0.0.1:8211" in msg
    assert "allow_network" in msg


def test_the_tracker_now_DEGRADES_instead_of_leaking(monkeypatch):
    """The motivating call site, end to end: fire-and-forget, so it returns
    False and logs — and the POST never leaves the process."""
    from repo_paths import OPTIONS_SCANNER
    monkeypatch.syspath_prepend(str(OPTIONS_SCANNER))
    import trade_tracker_client
    trade = {"trade_id": "t1", "symbol": "SPY", "strategy": "PCS",
             "expiration": "2026-10-16", "quantity": 1, "entry_credit": 0.5,
             "short_strike": 500.0, "long_strike": 499.0}
    assert trade_tracker_client.track(trade) is False


# ── the urllib stack ──────────────────────────────────────────────────────

def test_a_urllib_call_to_the_proxy_is_blocked():
    """``daily_trade_log`` reaches the proxy this way, with a 90-second timeout."""
    from repo_paths import PROXY_URL
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"{PROXY_URL}/trades", timeout=90)


def test_a_urllib_REQUEST_object_is_blocked_too():
    """``edgar_fundamentals`` builds a ``Request`` with headers first."""
    req = urllib.request.Request("https://www.sec.gov/",
                                 headers={"User-Agent": "test"})
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(req, timeout=30)


def test_the_urllib_error_is_the_NATIVE_type_callers_already_catch():
    assert issubclass(UrllibBlockedInTest, urllib.error.URLError)


def test_the_urllib_error_names_the_URL_and_the_way_out():
    with pytest.raises(UrllibBlockedInTest) as exc:
        urllib.request.urlopen("http://127.0.0.1:8100/x", timeout=1)
    assert "127.0.0.1:8100" in str(exc.value)
    assert "allow_network" in str(exc.value)


# ── the guard must not get in the way of what it should not touch ────────

def test_a_test_can_still_MONKEYPATCH_urlopen_over_the_guard(monkeypatch):
    """``tools/tests/test_wait_http.py`` does exactly this. The guard is set up
    by an autouse fixture BEFORE the test body, so a test's own patch wins."""
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: "stubbed")
    assert urllib.request.urlopen("http://anything/") == "stubbed"


def test_a_test_can_still_stub_requests_over_the_guard(monkeypatch):
    class _R:
        status_code = 200
    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    assert requests.get("http://127.0.0.1:8100/").status_code == 200


@pytest.mark.allow_network
def test_the_ESCAPE_HATCH_restores_the_real_adapter():
    """The marker is for a test that starts its own server, like wait_http's."""
    import requests.adapters
    from conftest import _real_http_adapter_send, _real_urlopen
    assert requests.adapters.HTTPAdapter.send is _real_http_adapter_send
    assert urllib.request.urlopen is _real_urlopen


def test_without_the_marker_the_adapter_IS_patched():
    """The converse, so the escape-hatch test cannot pass on a guard that was
    never installed."""
    import requests.adapters
    from conftest import _real_http_adapter_send, _real_urlopen
    assert requests.adapters.HTTPAdapter.send is not _real_http_adapter_send
    assert urllib.request.urlopen is not _real_urlopen


def test_the_marker_is_REGISTERED():
    """An unregistered marker is a warning, and ``--strict-markers`` makes it an
    error — so a typo in a test would silently leave the guard on."""
    import conftest
    import inspect
    assert "allow_network" in inspect.getsource(conftest.pytest_configure)


# ── the vacuous-pass hazard, demonstrated rather than asserted ───────────

def test_the_guard_makes_a_LIVE_server_read_as_DOWN():
    """⚠ The reason a probe-says-DOWN test must carry ``allow_network``.

    A real HTTP server that DOES accept and answers 200 — and without the marker,
    ``wait_http.probe`` still reads it as DOWN, because the guard refuses the
    urlopen before the server is ever asked. So any test asserting "reads as
    DOWN" passes under the guard regardless of what it is actually pointed at.
    """
    import http.server
    import threading

    from tools import wait_http

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
        assert wait_http.probe(wait_http.url_for_port(srv.server_port)) is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_real_socket_tests_in_wait_http_carry_the_marker():
    """Pinned by AST, because the vacuous one would never fail on its own."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "tools" / "tests" / "test_wait_http.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    marked = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if ast.unparse(dec) == "pytest.mark.allow_network":
                marked.add(node.name)
    assert {"test_a_bound_socket_that_never_accepts_reads_as_DOWN",
            "test_probe_succeeds_against_a_real_server"} <= marked, marked
