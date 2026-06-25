"""Shared fixtures for the driver_svc test suite.

``fake_bus`` is the in-memory ``Bus(fake=True)`` (fakeredis) the autonomous
handler/scheduler/e2e tests use. The legacy ``test_handlers.py`` constructs its
own ``Bus(fake=True)`` inline; this fixture is the canonical one for the newer
autonomous tests (Units 5/6/8) that reference ``fake_bus`` by name.
"""
import pytest

from shared.bus import Bus


@pytest.fixture
def fake_bus():
    return Bus(fake=True)
