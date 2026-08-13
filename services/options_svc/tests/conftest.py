# services/options_svc/tests/conftest.py
import datetime as _dt

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
