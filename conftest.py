"""Repo-root conftest: the suite cannot open a live on-disk store.

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
# Two chokepoints, because production code uses two HTTP stacks:
#   * ``requests.adapters.HTTPAdapter.send`` — BELOW ``requests.Session``. Eight
#     production modules hold a persistent ``requests.Session()``, so patching
#     ``requests.post`` would have missed most callers. Nothing in the repo does
#     ``from requests import ...``.
#   * ``urllib.request.urlopen`` — daily_trade_log reaches the proxy this way
#     (90-second timeout), and earnings_history / edgar_fundamentals reach out.
#     Nothing does ``from urllib.request import urlopen``.
# Not covered, deliberately: ``httpx`` (no production module imports it) and
# ``aiohttp`` (only ``edge_tts``, whose tests stub ``voice._synthesize``).
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
# measurement: a test that starts its own server.
#
# ESCAPE HATCH
# ------------
#     @pytest.mark.allow_network
#
# For a test that binds its OWN local server. ⚠ Check the converse too: a test
# asserting a probe reads DOWN would pass VACUOUSLY under this guard, because the
# guard — not the thing under test — is what said down.

try:
    import requests
    import requests.adapters
except ImportError:          # no requests -> the requests-stack leak cannot occur
    requests = None

import urllib.error
import urllib.request

if requests is not None:
    class NetworkBlockedInTest(requests.exceptions.ConnectionError):
        """Raised in place of a real ``requests`` round trip during a test.

        A ``ConnectionError`` on purpose: see the block above."""

    _real_http_adapter_send = requests.adapters.HTTPAdapter.send
else:                        # pragma: no cover - requests is a hard dependency
    NetworkBlockedInTest = None
    _real_http_adapter_send = None


class UrllibBlockedInTest(urllib.error.URLError):
    """Raised in place of a real ``urllib.request.urlopen`` during a test."""


_real_urlopen = urllib.request.urlopen

_BLOCKED_HINT = ("refusing real network I/O from a test: {what}\n"
                 "Stub the call, or mark the test @pytest.mark.allow_network if "
                 "it starts its OWN local server.")


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    """Refuse every real outbound HTTP request, through both stacks."""
    if request.node.get_closest_marker("allow_network"):
        yield
        return

    if requests is not None:
        def _guarded_send(self, prepared, *a, **kw):
            raise NetworkBlockedInTest(_BLOCKED_HINT.format(
                what=f"{prepared.method} {prepared.url}"), request=prepared)

        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _guarded_send)

    def _guarded_urlopen(url, *a, **kw):
        target = getattr(url, "full_url", url)
        raise UrllibBlockedInTest(_BLOCKED_HINT.format(what=f"urlopen {target}"))

    monkeypatch.setattr(urllib.request, "urlopen", _guarded_urlopen)
    yield


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_live_db: test may open a real on-disk store (prefer tmp_path)")
    config.addinivalue_line(
        "markers", "allow_network: test may make real HTTP requests — only for a "
                   "test that starts its OWN local server")
