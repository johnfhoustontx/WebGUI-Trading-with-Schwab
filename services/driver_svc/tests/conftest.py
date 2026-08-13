"""Shared fixtures for the driver_svc test suite.

``fake_bus`` is the in-memory ``Bus(fake=True)`` (fakeredis) the autonomous
handler/scheduler/e2e tests use. The legacy ``test_handlers.py`` constructs its
own ``Bus(fake=True)`` inline; this fixture is the canonical one for the newer
autonomous tests (Units 5/6/8) that reference ``fake_bus`` by name.
"""
import pytest

from repo_paths import ENV_FLAGS
from shared.bus import Bus


@pytest.fixture
def fake_bus():
    return Bus(fake=True)


@pytest.fixture(autouse=True)
def autonomous_trading_permitted(monkeypatch):
    """Re-permit autonomous trading for this suite.

    ``repo_paths._resolve_env`` forces every environment suppression flag False
    under pytest, ``autonomous_trading`` among them — so without this fixture
    ``handlers.run_autonomous_cycle`` returns immediately in every test and ~30
    tests across ``test_handlers_autonomous.py`` and ``test_autonomous_e2e.py``
    stop exercising anything. Two of them (``test_cycle_disabled_is_noop`` /
    ``test_cycle_halted_is_noop``) would go WORSE than red — they assert that
    nothing happens, so they would keep passing while testing the environment
    guard instead of the control-key gate they were written for.

    Safe to re-permit here because the flag gates one thing only: enqueuing a
    ``driver_paper_create`` onto the bus, which in tests is fakeredis. Nothing
    reaches out — ``allow_claude`` stays False (the decider builds no client) and
    the market fetch is monkeypatched in every test that runs a cycle.

    Autouse rather than opt-in because the autonomous cycle is what this suite is
    ABOUT; the environment guard is orthogonal to every one of these tests. The
    guard's own tests (``test_env_autonomous_guard.py``) set the flag explicitly,
    which overrides this (their ``monkeypatch.setitem`` is applied after).
    """
    monkeypatch.setitem(ENV_FLAGS, "autonomous_trading", True)
