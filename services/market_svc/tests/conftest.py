# services/market_svc/tests/conftest.py
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402

from services.market_svc import compute  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_claude(monkeypatch):
    """Neutralize real Claude-client resolution across the whole suite.

    ``test_app.py``'s TestClient lifespan starts the scheduler, whose first cycle
    fires ``summary_due`` → ``generate_summary(client=None)`` → ``_make_summary_client``.
    On a machine with a configured ANTHROPIC key that would reach a live API call
    (only a cancellation race stops it). Forcing the resolver to None makes every
    test's real-client path return an empty narrative — no network, regardless of
    timing. Tests that inject their own fake client are unaffected.
    """
    monkeypatch.setattr(compute, "_make_summary_client", lambda: None)


@pytest.fixture(autouse=True)
def _fresh_summary_packet_memo():
    """``read_summary_packet``'s memo is module-level and keyed on small
    fakeredis version counters, which two tests can share — reset it around
    every test so one test is never served another's packet."""
    from services.market_svc import compute
    compute.reset_packet_memo()
    yield
    compute.reset_packet_memo()
