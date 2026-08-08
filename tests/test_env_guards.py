"""Environment suppression guards — see
docs/plans/2026-08-08-dev-prod-environments-design.md.

Task 5: a suppressed environment (dev) must not run service schedulers, because
the dev stack works off a snapshot of prod's data and must issue zero Schwab API
calls at rest.

``services/_scaffold`` imports only ``fastapi`` and ``shared.bus``, so it is safe
to import from a root-level test file — it does not trigger this repo's
cross-app top-level module-name collisions (``config``/``scoring``/``notifier``/
``src``) that force the per-service suites to run one folder at a time.
"""
import pathlib
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services import _scaffold  # noqa: E402


def test_schedulers_disabled_when_suppressed(monkeypatch):
    monkeypatch.setitem(_scaffold.ENV_FLAGS, "schedulers", False)
    monkeypatch.delenv("TRADING_ENABLE_SCHEDULERS", raising=False)
    assert _scaffold._schedulers_enabled() is False


def test_env_var_overrides_suppression(monkeypatch):
    """The one dev case that needs collection: testing the collectors."""
    monkeypatch.setitem(_scaffold.ENV_FLAGS, "schedulers", False)
    monkeypatch.setenv("TRADING_ENABLE_SCHEDULERS", "1")
    assert _scaffold._schedulers_enabled() is True


def test_suppressed_app_never_creates_the_scheduler_task(monkeypatch):
    """The behavior that matters: the task is genuinely not created.

    ``_schedulers_enabled()`` returning False is an implementation detail; what
    the acceptance test (a dev stack adding no calls to prod's
    ``/stats/api_calls``) depends on is that the coroutine never runs. Drive the
    real lifespan via TestClient and assert the scheduler was never invoked.
    """
    monkeypatch.setitem(_scaffold.ENV_FLAGS, "schedulers", False)
    monkeypatch.delenv("TRADING_ENABLE_SCHEDULERS", raising=False)
    ran = []

    async def sched(bus):
        ran.append(True)

    app = _scaffold.make_app("suppressedx", scheduler=sched, bus=object())
    with TestClient(app) as client:
        # Give a would-be task ample opportunity to be scheduled and run.
        for _ in range(20):
            if ran:
                break
            client.get("/health")
    assert ran == [], "scheduler ran in a suppressed environment"


def test_unsuppressed_app_does_create_the_scheduler_task(monkeypatch):
    """Non-vacuity partner for the test above.

    Without this, ``ran == []`` would also hold if ``make_app`` were broken, if
    the scheduler kwarg were ignored, or if TestClient never ran the lifespan.
    This one FAILS if the guard is removed only in the sense that it must keep
    passing — it is the control, and it is deliberately NOT a mutation-check
    partner (removing the guard leaves it green).
    """
    monkeypatch.setitem(_scaffold.ENV_FLAGS, "schedulers", True)
    monkeypatch.delenv("TRADING_ENABLE_SCHEDULERS", raising=False)
    ran = []

    async def sched(bus):
        ran.append(True)

    app = _scaffold.make_app("unsuppressedx", scheduler=sched, bus=object())
    with TestClient(app) as client:
        for _ in range(40):
            if ran:
                break
            client.get("/health")
    assert ran == [True], "scheduler did not run with schedulers enabled"


def test_suppressed_service_is_not_reported_broken(monkeypatch):
    """/health must say "no scheduler", NOT "scheduler dead".

    Otherwise the Status page would show a perfectly healthy dev stack as broken
    every time you looked at it.
    """
    monkeypatch.setitem(_scaffold.ENV_FLAGS, "schedulers", False)
    monkeypatch.delenv("TRADING_ENABLE_SCHEDULERS", raising=False)

    async def sched(bus):  # pragma: no cover — must never run
        raise AssertionError("scheduler ran in a suppressed environment")

    app = _scaffold.make_app("healthx", scheduler=sched, bus=object())
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["up"] is True
    assert body["scheduler_alive"] is True
    assert body["scheduler_restarts"] == 0
