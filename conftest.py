"""Repo-root conftest: the suite cannot open a live on-disk store, or reach a
real HTTP server (the second half of this file: "THE NETWORK TWIN").

WHY THIS EXISTS
---------------
On 2026-07-16 a pytest run wrote 24 synthetic signals (SPY @ underlying 500.00
and QQQ @ 430.00, short deltas on an exact 32nd ladder, theta exactly 0) plus 21
rejected paper orders into BOTH environments' production databases. They were
found on 2026-08-28, by which point they had been feeding backtests and ranking
samples for six weeks.

`options-scanner/tests/conftest.py` already carried a fixture written to prevent
precisely this, and it had NEVER worked. It does:

    monkeypatch.setattr(signal_db, "DEFAULT_DB_PATH", tmp)

but every function in that module is declared ``def f(..., db_path=DEFAULT_DB_PATH)``
and **Python binds a default at `def` time**. Patching the module attribute
afterwards changes nothing the functions see: measured, all 13 signal_db
functions still resolved to the live path after the patch. `paper_account_db`
had no fixture at all, which is how the orders got through.

THE LAYER MATTERS
-----------------
Redirecting defaults is per-module, per-function, and has to be remembered
forever — it failed here silently, for weeks, while looking like protection.
This guard sits at the one chokepoint every store shares, ``sqlite3.connect``,
so a module added tomorrow is covered without anyone remembering to cover it.
Verified: no module in the repo does ``from sqlite3 import connect``, so
patching the attribute reaches every caller.

ESCAPE HATCH
------------
A test that genuinely must reach a real store marks itself:

    @pytest.mark.allow_live_db

Prefer a tmp_path copy. The marker is for reading production shape, never writing.
"""
import pathlib
import sqlite3

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent

# Directories whose .db files are REAL data. Kept explicit rather than "anything
# not under tmp": a wrong answer here silently re-opens the hole.
_LIVE_DIRS = (
    _ROOT / "options-scanner" / "data",
    _ROOT / "options-scanner",              # gex_history.db sits at the app root
    _ROOT / "shared" / "data",
    _ROOT / "webgui" / "data",
    _ROOT / "services" / "trade_svc" / "data",
    _ROOT / "services" / "news_svc" / "data",
    _ROOT / "services" / "blog_svc" / "data",
)


def live_db_paths():
    """Every production .db file this checkout carries."""
    out = []
    for d in _LIVE_DIRS:
        if d.is_dir():
            out.extend(p for p in d.glob("*.db") if p.is_file())
    return sorted(set(out))


def is_protected(path):
    """True when `path` resolves inside a live data directory.

    Compares RESOLVED parents, so a relative path, a symlink or a ``..`` walk
    cannot slip past. Non-file targets (``:memory:``) are never protected.
    """
    try:
        s = str(path)
        if not s or s == ":memory:":
            return False
        if s.startswith("file:"):                       # URI form, incl. ?mode=ro
            s = s[5:].split("?", 1)[0]
        p = pathlib.Path(s).resolve()
    except (OSError, ValueError):
        return False
    # ⚠ No `if d.exists()` filter. It was there, and it made the guard SILENTLY
    # INERT on any checkout where a live data directory had not been created yet
    # — a fresh clone, or a git worktree, where `options-scanner/data` is
    # gitignored and therefore absent. That is precisely the moment the guard
    # matters most: nothing is there to protect yet, so a leaking test creates
    # the production store rather than corrupting it.
    # `Path.resolve()` is non-strict, so a directory that does not exist still
    # resolves fine and compares correctly. Measured 2026-08-29: with the filter,
    # is_protected("options-scanner/data/signals.db") returned False in a
    # worktree and True on a full checkout — the same call, two answers,
    # decided by machine state.
    return any(p.parent == d.resolve() for d in _LIVE_DIRS)


_real_connect = sqlite3.connect


@pytest.fixture(autouse=True)
def _block_live_databases(request, monkeypatch):
    """Refuse any sqlite3.connect that resolves to a production store."""
    if request.node.get_closest_marker("allow_live_db"):
        yield
        return

    def _guarded(database, *a, **kw):
        if is_protected(database):
            raise RuntimeError(
                f"refusing to open a live database from a test: {database}\n"
                "Use tmp_path, or mark the test @pytest.mark.allow_live_db if it "
                "genuinely must read production shape."
            )
        return _real_connect(database, *a, **kw)

    monkeypatch.setattr(sqlite3, "connect", _guarded)
    yield


# ─────────────────────────────────────────────────────────────────────────────
# THE NETWORK TWIN: the suite cannot reach a real HTTP server
# ─────────────────────────────────────────────────────────────────────────────
#
# WHY THIS EXISTS
# ---------------
# ``paper_trader.add_trade`` POSTs every new paper trade to the proxy's stream
# tracker (``trade_tracker_client.track`` -> ``repo_paths.PROXY_URL``/track), and
# several suites call it with nothing stubbed. Off the prod box that is a
# 1.5-second timeout per row; ON it, where the proxy is up, the suite registers
# fake trades with the LIVE tracker.
#
# Measured 2026-09-12, by recording every outbound attempt across all 18 suites:
# **47 attempts, 45 of them to the live proxy** — and it is TWO leaks, not one.
# 18 are WRITES (/track, /untrack). 27 are market-data READS (/pricehistory,
# /chains, /quote, /stream/quotes), mostly from sentiment_svc's app tests — which
# on prod would spend real Schwab API calls against a budget already at
# ~68-76k/day. The remaining 2 are tools/wait_http's own local test servers.
#
# THE LAYER
# ---------
# Three chokepoints, one per HTTP stack a production call can travel:
#   * ``requests.adapters.HTTPAdapter.send`` — BELOW ``requests.Session``. Eight
#     production modules hold a persistent ``requests.Session()``, so patching
#     ``requests.post`` would have missed most callers. The patch is on the
#     CLASS, so a subclass that does not override ``send`` is covered too
#     (``news_svc.fetch._WatchedAdapter``, the one ``from requests.adapters
#     import HTTPAdapter`` in the repo).
#   * ``urllib.request.urlopen`` — daily_trade_log reaches the proxy this way
#     (90-second timeout), and earnings_history / edgar_fundamentals reach out.
#     Nothing does ``from urllib.request import urlopen``, and nothing builds
#     its own opener.
#   * ``httpx.HTTPTransport.handle_request`` and
#     ``httpx.AsyncHTTPTransport.handle_async_request`` (added 2026-10-06). No
#     module here imports ``httpx``, which is why the first version left it out
#     — but two dependencies drive it for real outbound calls: the ``anthropic``
#     SDK (``options_svc.compute``, a PAID call) and ``schwab-py``
#     (``schwab-proxy/stream_bridge.py``, an OAuth client). ⚠ The patch sits on
#     the two REAL network transports and nowhere higher. FastAPI's
#     ``TestClient`` is itself an ``httpx.Client``, and it, ``ASGITransport``
#     and ``MockTransport`` each bring their own in-process transport, so a
#     guard on ``httpx.Client.send`` would have broken every ``test_app.py``.
# Not covered, deliberately: ``aiohttp`` (only ``edge_tts``, whose tests stub
# ``voice._synthesize``; measured 2026-10-06 across the 21 CI suites: no test
# opens an aiohttp connection) and anything that is not HTTP (``smtplib``, the
# Schwab websocket), which the tests that touch them replace by hand.
#
# ⚠ WHY IT RAISES THE NATIVE ERROR, UNLIKE THE SQLITE GUARD
# ----------------------------------------------------------
# A database has no "store is down" path the code handles, so a loud
# RuntimeError is right there. An HTTP server DOES: every call site already
# catches ``requests.RequestException`` / ``URLError`` and degrades. Raising the
# native type makes a test take exactly the path production takes with the proxy
# unreachable. A RuntimeError would escape those ``except`` clauses and force a
# per-test stub at every site — the per-site patching the SQLite post-mortem
# above says does not scale. Blocking everything broke exactly one test in the
# 2026-09-12 measurement and five on ``main`` on 2026-10-06 — every one a test
# that starts its own server, and none that was calling somebody else's.
#
# ⚠ WHY IT IS INSTALLED ONCE, AT IMPORT, AND NOT PER TEST
# -------------------------------------------------------
# The first version was a per-test ``monkeypatch``. That is undone at every
# teardown, and a thread a test leaves running does not stop at a teardown.
# Measured 2026-10-06 on ``services/sentiment_svc``: three tests drove the real
# ``scheduler.loop``, which starts two daemon stream consumers, and in 3 runs
# out of 3 two or three of their requests (``/chains``, ``/quote``) reached the
# proxy's port in the gap between one test's teardown and the next test's
# setup. On the prod box those are real Schwab calls. The tests are fixed, but
# the next leaked thread will be just as quiet — so the patch is applied when
# this file is imported and never removed. That also covers collection (a
# module that makes a request at import) and the end of the session.
#
# ESCAPE HATCH
# ------------
#     @pytest.mark.allow_network
#
# For a test that binds its OWN local server. The marker opens the guard for
# the length of that one test — ⚠ for EVERY thread, a leaked one included, so
# it is not a substitute for stubbing. ⚠ Check the converse too: a test
# asserting a probe reads DOWN would pass VACUOUSLY under this guard, because the
# guard — not the thing under test — is what said down.

try:
    import requests
    import requests.adapters
except ImportError:          # no requests -> the requests-stack leak cannot occur
    requests = None

import urllib.error
import urllib.request

try:
    import httpx
except ImportError:          # no httpx -> the httpx-stack leak cannot occur
    httpx = None

if requests is not None:
    class NetworkBlockedInTest(requests.exceptions.ConnectionError):
        """Raised in place of a real ``requests`` round trip during a test.

        A ``ConnectionError`` on purpose: see the block above."""
else:                        # pragma: no cover - requests is a hard dependency
    NetworkBlockedInTest = None


class UrllibBlockedInTest(urllib.error.URLError):
    """Raised in place of a real ``urllib.request.urlopen`` during a test."""


if httpx is not None:
    class HttpxBlockedInTest(httpx.ConnectError):
        """Raised in place of a real ``httpx`` round trip during a test.

        A ``ConnectError`` on purpose, for the same reason as the two above:
        the anthropic SDK turns it into its own ``APIConnectionError``."""
else:                        # pragma: no cover - starlette's TestClient needs it
    HttpxBlockedInTest = None

_BLOCKED_HINT = ("refusing real network I/O from a test: {what}\n"
                 "Stub the call, or mark the test @pytest.mark.allow_network if "
                 "it starts its OWN local server.")


class _NetworkGuard:
    """The one switch all three stacks read. ``open`` counts the running tests
    marked ``allow_network``; zero, the resting state, refuses everything."""

    def __init__(self):
        self.open = 0
        self.errors = (NetworkBlockedInTest, UrllibBlockedInTest, HttpxBlockedInTest)


def _install_network_guard():
    """Patch the three stacks for the life of the process; return the switch."""
    guard = _NetworkGuard()

    real_urlopen = urllib.request.urlopen

    def _guarded_urlopen(url, *a, **kw):
        if guard.open:
            return real_urlopen(url, *a, **kw)
        target = getattr(url, "full_url", url)
        raise UrllibBlockedInTest(_BLOCKED_HINT.format(what=f"urlopen {target}"))

    _guarded_urlopen.network_guard = guard     # how a second import finds it
    urllib.request.urlopen = _guarded_urlopen

    if requests is not None:
        real_send = requests.adapters.HTTPAdapter.send

        def _guarded_send(self, prepared, *a, **kw):
            if guard.open:
                return real_send(self, prepared, *a, **kw)
            raise NetworkBlockedInTest(_BLOCKED_HINT.format(
                what=f"{prepared.method} {prepared.url}"), request=prepared)

        requests.adapters.HTTPAdapter.send = _guarded_send

    if httpx is not None:
        real_httpx = httpx.HTTPTransport.handle_request
        real_httpx_async = httpx.AsyncHTTPTransport.handle_async_request

        def _refuse(http_request):
            return HttpxBlockedInTest(_BLOCKED_HINT.format(
                what=f"{http_request.method} {http_request.url}"), request=http_request)

        def _guarded_httpx(self, http_request):
            if guard.open:
                return real_httpx(self, http_request)
            raise _refuse(http_request)

        async def _guarded_httpx_async(self, http_request):
            if guard.open:
                return await real_httpx_async(self, http_request)
            raise _refuse(http_request)

        httpx.HTTPTransport.handle_request = _guarded_httpx
        httpx.AsyncHTTPTransport.handle_async_request = _guarded_httpx_async

    return guard


# Installed at import. If this file is ever imported a second time in one
# process, the first guard is found on the patched ``urlopen`` and reused, so
# there is still one switch and one set of exception classes — a second layer
# would capture the first guard as its "real" function, and the marker would
# then open the outer layer onto a closed inner one.
_GUARD = getattr(urllib.request.urlopen, "network_guard", None)
if _GUARD is None:
    _GUARD = _install_network_guard()
else:
    NetworkBlockedInTest, UrllibBlockedInTest, HttpxBlockedInTest = _GUARD.errors


def network_guard():
    """The process-wide switch (for the guard's own tests)."""
    return _GUARD


@pytest.fixture(autouse=True)
def _block_network(request):
    """Open the guard for a test marked ``allow_network``, and close it after.

    An unmarked test needs nothing done for it: every real outbound HTTP
    request, through all three stacks, is already refused."""
    if not request.node.get_closest_marker("allow_network"):
        yield
        return
    _GUARD.open += 1
    try:
        yield
    finally:
        _GUARD.open -= 1


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_live_db: test may open a real on-disk store (prefer tmp_path)")
    config.addinivalue_line(
        "markers", "allow_network: test may make real HTTP requests — only for a "
                   "test that starts its OWN local server")
