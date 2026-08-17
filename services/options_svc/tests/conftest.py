# services/options_svc/tests/conftest.py
import datetime as _dt
import sqlite3 as _sqlite3
import sys as _sys

import pytest

from services.options_svc import compute, handlers
from shared import market_calendar as mc


@pytest.fixture(autouse=True)
def _no_live_claude(monkeypatch):
    """Neutralize real Claude-client resolution across the whole options_svc suite.

    ``compute._make_analyze_client()`` resolves a real ``anthropic.Anthropic``
    client from the environment / gitignored ``shared/anthropic_key.txt`` — on a
    machine with a real key configured (this dev box has one), any test that
    exercises the ``client = client or _make_analyze_client()`` fallback without
    injecting its own fake client would fire a REAL, BILLED API call. This
    already happened once (the ``_research_news`` no-key test silently made a
    live web-search call). Forcing the resolver to ``None`` makes every test's
    real-client path degrade to the documented no-key behavior — no network,
    regardless of which call site (``gamma_analyze``, ``_research_news``, and
    whatever Task 4 adds next) reaches it. Tests that inject their own fake
    client (the overwhelmingly common case in this file) are unaffected — the
    ``client or _make_analyze_client()`` fallback short-circuits before this
    patched function is ever called. Mirrors ``services/market_svc/tests/conftest.py``.
    """
    monkeypatch.setattr(compute, "_make_analyze_client", lambda: None)


# Wed 2026-08-12, 10:00 CT: a plain trading day inside the 08:00–15:20 CT
# collection window and BEFORE the 2026-08-17 extended-hours activation date.
_RTH_NOW = _dt.datetime(2026, 8, 12, 10, 0, tzinfo=mc.CT)


@pytest.fixture(autouse=True)
def _pin_flow_window_clock(monkeypatch):
    """Pin ``handlers._alert_now`` so the flow-window gate is deterministic.

    ``run_flow_alerts`` / ``publish_flow_skew`` are gated on
    ``handlers._flow_window_open()``, which reads the clock. Without this, every
    test exercising them would pass or fail depending on the hour the suite ran
    (green at 10:00 CT on a weekday, red at midnight or on a Saturday). Pinning
    to a fixed in-window moment makes "the gate is open" the default, matching
    what those tests were written against.

    A test that cares about the gate overrides this by monkeypatching
    ``_alert_now`` (or passing ``now=``) in its own body — a later
    ``monkeypatch.setattr`` wins and is undone LIFO. See
    ``test_flow_alert_window.py``.
    """
    monkeypatch.setattr(handlers, "_alert_now", lambda: _RTH_NOW)


@pytest.fixture(autouse=True)
def _in_memory_gex_db(monkeypatch):
    """Give ``gex_history_db.connect`` an EMPTY IN-MEMORY database.

    ``run_flow_alerts`` opens the real ``gex_history.db`` and then reads its
    series behind ``if conn is not None``. That file is gitignored DATA, so on a
    fresh checkout the connect raises, ``conn`` stays None, and the guard
    silently skips the loader the test just monkeypatched — detection sees an
    empty series and the test fails with "lost its alerts".

    The result was a suite that passed or failed on MACHINE STATE: green in a
    checkout that happened to have collected GEX history, red in a fresh worktree
    or on CI. Exactly the same class of problem as ``_pin_flow_window_clock``
    above, which pins the clock so tests do not depend on the hour they run.

    An in-memory DB with the real schema is the honest stand-in: connect
    succeeds, so patched loaders are actually reached, and a test that does NOT
    patch one reads a genuinely empty table instead of a machine's leftovers.
    Tests that fake the whole module via ``sys.modules`` are unaffected — their
    ``setitem`` replaces the module this fixture patched.
    """
    try:
        import gex_history_db as gh
    except Exception:                                   # pragma: no cover
        return                                          # nothing to patch

    def _connect(read_only: bool = False):
        conn = _sqlite3.connect(":memory:", isolation_level=None)
        try:
            gh.init_schema(conn)
        except Exception:                               # pragma: no cover
            pass
        return conn

    monkeypatch.setattr(gh, "connect", _connect)
    # handlers imports it lazily INSIDE the function, so the patch has to land on
    # the module object every later `import gex_history_db` resolves to.
    if "gex_history_db" in _sys.modules:
        monkeypatch.setattr(_sys.modules["gex_history_db"], "connect", _connect,
                            raising=False)
