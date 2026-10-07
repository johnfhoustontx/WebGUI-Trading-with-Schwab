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

**Three chokepoints, one per HTTP stack a production call can travel:**

* ``requests.adapters.HTTPAdapter.send`` — below ``requests.Session``, which
  matters: eight production modules hold a persistent ``requests.Session()``, so
  patching ``requests.post`` would have missed most of them;
* ``urllib.request.urlopen`` — ``daily_trade_log``, ``earnings_history``,
  ``edgar_fundamentals`` and ``tools/wait_http`` use it;
* ``httpx.HTTPTransport.handle_request`` and
  ``httpx.AsyncHTTPTransport.handle_async_request`` — no module here imports
  ``httpx``, but two dependencies drive it for real outbound calls: the
  ``anthropic`` SDK (a PAID call) and ``schwab-py`` (the stream bridge's login).
  The patch sits on the two REAL network transports and nowhere higher, because
  FastAPI's ``TestClient`` and ``httpx.ASGITransport`` are also httpx clients
  and must keep working: they bring their own in-process transport.

⚠ **Unlike the SQLite guard, this one raises each stack's OWN connection
error**, and that is deliberate. A database has no "store is down" path the code
handles, so a loud ``RuntimeError`` is right there. An HTTP server does — every
call site already catches ``requests.RequestException`` / ``URLError`` and
degrades — so raising the native type makes a test take exactly the path
production takes when the proxy is unreachable. A ``RuntimeError`` would escape
those ``except`` clauses and force a per-test stub at every site, which is the
per-site patching the SQLite post-mortem says does not scale.

⚠ **The guard is installed ONCE, when the root conftest is imported, and stays
in place for the whole process.** The first version was a per-test monkeypatch,
which is undone at every teardown — and a thread a test leaves running does not
stop at a teardown. Measured 2026-10-06 on ``services/sentiment_svc``: three
tests started the service's two daemon stream consumers, and in 3 runs out of 3
two or three of their requests reached the proxy's port in the gap between one
test's teardown and the next test's setup. Those tests are fixed, but the next
leaked thread will be just as quiet, so the gap itself is closed.
"""
import urllib.error
import urllib.request

import httpx
import pytest
import requests

from conftest import HttpxBlockedInTest, NetworkBlockedInTest, UrllibBlockedInTest


# ── captured at IMPORT: during collection, with no test and no fixture ───────
#
# This module is imported while pytest collects, before any test has started.
# A guard that lives in a per-test fixture is not installed yet at that moment
# (and not between two tests either), so these three attempts would go out.

def _attempt(call, blocked):
    try:
        call()
    except blocked:
        return "blocked"
    except Exception as exc:                      # a real attempt that failed
        return f"went out ({type(exc).__name__})"
    return "went out (answered)"


_AT_COLLECTION = {
    "requests": _attempt(
        lambda: requests.get("http://127.0.0.1:8100/health", timeout=1.5),
        NetworkBlockedInTest),
    "urllib": _attempt(
        lambda: urllib.request.urlopen("http://127.0.0.1:8100/health", timeout=1.5),
        UrllibBlockedInTest),
    "httpx": _attempt(
        lambda: httpx.get("http://127.0.0.1:8100/health", timeout=1.5),
        HttpxBlockedInTest),
}


def test_the_guard_is_already_in_place_OUTSIDE_any_test():
    """The property a per-test fixture cannot have: collection time, the gap
    between two tests, and a thread an earlier test left running."""
    assert _AT_COLLECTION == {"requests": "blocked", "urllib": "blocked",
                              "httpx": "blocked"}


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


# ── the httpx stack ───────────────────────────────────────────────────────

def test_a_SYNC_httpx_call_is_blocked():
    with pytest.raises(httpx.ConnectError):
        httpx.get("http://127.0.0.1:8100/health", timeout=1.5)


def test_a_persistent_httpx_CLIENT_is_blocked_too():
    """schwab-py holds one for the life of the stream bridge."""
    with httpx.Client() as client:
        with pytest.raises(httpx.ConnectError):
            client.post("https://api.schwabapi.com/v1/oauth/token", data={"x": "y"})


def test_an_ASYNC_httpx_call_is_blocked():
    """The async transport is a separate class with a separate method; a guard
    on the sync one alone leaves every ``AsyncClient`` open."""
    import asyncio

    async def _go():
        async with httpx.AsyncClient() as client:
            await client.get("https://api.anthropic.com/v1/messages")

    with pytest.raises(httpx.ConnectError):
        asyncio.run(_go())


_MODULE_LEVEL_CLIENT = httpx.Client()


def test_a_client_built_BEFORE_the_test_is_blocked_too():
    """A module-level client outlives any one test. The patch is on the
    transport CLASS, so an instance created earlier is covered as well."""
    with pytest.raises(httpx.ConnectError):
        _MODULE_LEVEL_CLIENT.get("http://127.0.0.1:8100/health")


def test_the_httpx_error_is_the_NATIVE_type_callers_already_catch():
    """The anthropic SDK turns ``httpx`` transport errors into its own
    ``APIConnectionError``; anything outside this hierarchy would escape as a
    bare exception no call site expects."""
    assert issubclass(HttpxBlockedInTest, httpx.ConnectError)
    assert issubclass(HttpxBlockedInTest, httpx.TransportError)
    assert issubclass(HttpxBlockedInTest, httpx.HTTPError)


def test_the_httpx_error_names_the_URL_and_the_way_out():
    with pytest.raises(HttpxBlockedInTest) as exc:
        httpx.get("http://127.0.0.1:8211/health")
    assert "127.0.0.1:8211" in str(exc.value)
    assert "allow_network" in str(exc.value)
    assert exc.value.request.url.host == "127.0.0.1"


def test_the_anthropic_SDK_takes_its_own_connection_error_path():
    """The paid call, end to end: a real client with a fake key reaches the
    transport, is refused, and raises the SDK's ordinary connection error. No
    request leaves the process and nothing is billed."""
    anthropic = pytest.importorskip("anthropic")
    client = anthropic.Anthropic(api_key="sk-ant-test-not-a-key", max_retries=0)
    with pytest.raises(anthropic.APIConnectionError) as exc:
        client.messages.create(model="claude-sonnet-5-5", max_tokens=8,
                               messages=[{"role": "user", "content": "x"}])
    assert isinstance(exc.value.__cause__, HttpxBlockedInTest)


# ── httpx clients that are NOT the network must be left alone ────────────

def _tiny_app():
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/health")
    def _health():
        return {"ok": True}

    return app


def test_the_FastAPI_TestClient_still_works_in_process():
    """Every service's ``test_app.py`` and the webgui auth tests depend on this.
    ``TestClient`` IS an ``httpx.Client``; it answers through its own in-process
    transport, which the guard must not touch."""
    from fastapi.testclient import TestClient
    with TestClient(_tiny_app()) as client:
        r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_an_ASGI_transport_still_works_in_process():
    import asyncio

    async def _go():
        transport = httpx.ASGITransport(app=_tiny_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.get("/health")

    assert asyncio.run(_go()).status_code == 200


def test_a_MOCK_transport_still_works():
    """The injected fake a test should prefer over ``allow_network``."""
    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    with httpx.Client(transport=transport) as client:
        assert client.get("https://api.anthropic.com/").status_code == 204


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


class _OwnServer:
    """A loopback HTTP server this test file starts itself; answers 200."""

    def __enter__(self):
        import http.server
        import threading

        class _H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()


def _no_env_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.allow_network
def test_the_ESCAPE_HATCH_reaches_a_server_the_test_started(monkeypatch):
    """The marker is for a test that starts its own server, like wait_http's.
    Shown through all three stacks, against a server that really answers."""
    _no_env_proxy(monkeypatch)
    with _OwnServer() as srv:
        assert requests.get(srv.url, timeout=5).status_code == 200
        assert urllib.request.urlopen(srv.url, timeout=5).status == 200
        assert httpx.get(srv.url, timeout=5).status_code == 200


def test_without_the_marker_the_SAME_live_server_is_refused(monkeypatch):
    """The converse, so the escape-hatch test cannot pass on a guard that was
    never installed: the server is up and would answer 200."""
    _no_env_proxy(monkeypatch)
    with _OwnServer() as srv:
        with pytest.raises(NetworkBlockedInTest):
            requests.get(srv.url, timeout=5)
        with pytest.raises(UrllibBlockedInTest):
            urllib.request.urlopen(srv.url, timeout=5)
        with pytest.raises(HttpxBlockedInTest):
            httpx.get(srv.url, timeout=5)


@pytest.mark.allow_network
def test_the_escape_hatch_CLOSES_again_after_the_marked_test():
    """Half of a pair with the test below: this one only opens the guard."""
    import conftest
    assert conftest.network_guard().open == 1


def test_the_guard_is_closed_in_an_unmarked_test():
    """Runs after the marked test above in file order; either way the count is
    zero, because a marked test must leave it as it found it."""
    import conftest
    assert conftest.network_guard().open == 0


def test_a_SECOND_import_of_the_root_conftest_reuses_the_one_guard():
    """A second layer would capture the first guard as its "real" function, and
    the marker would then open the outer layer onto a closed inner one."""
    import importlib.util
    import pathlib

    import conftest
    before = (urllib.request.urlopen, requests.adapters.HTTPAdapter.send,
              httpx.HTTPTransport.handle_request)
    spec = importlib.util.spec_from_file_location(
        "conftest_imported_again", pathlib.Path(conftest.__file__))
    again = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(again)
    assert again.network_guard() is conftest.network_guard()
    assert again.NetworkBlockedInTest is NetworkBlockedInTest
    assert again.UrllibBlockedInTest is UrllibBlockedInTest
    assert again.HttpxBlockedInTest is HttpxBlockedInTest
    assert before == (urllib.request.urlopen, requests.adapters.HTTPAdapter.send,
                      httpx.HTTPTransport.handle_request)


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
