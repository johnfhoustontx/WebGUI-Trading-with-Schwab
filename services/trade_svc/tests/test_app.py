"""Tests for the trade service FastAPI app (Task #26).

Trade is on-demand only: ``make_app`` is wired with a command handler and NO
scheduler. The app uses a fakeredis ``Bus`` under pytest, so startup's
command-consumer loop blocks harmlessly on an empty ``cmd:trade`` stream.
"""
from fastapi.testclient import TestClient

from services.trade_svc import handlers


def test_health():
    from services.trade_svc.app import app

    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["domain"] == "trade"
        assert body["up"] is True
        # Additive R2 scheduler-heartbeat keys (trade is command-only → alive).
        assert body["scheduler_alive"] is True


def test_app_wires_command_handler():
    """The app must dispatch via the analyze command handler (on-demand path)."""
    from services.trade_svc import app as app_module

    assert app_module.handlers.handle_command is handlers.handle_command


def test_app_passes_the_nightly_scheduler_to_make_app():
    """``scheduler=`` must actually reach ``make_app`` — the whole feature.

    Asserted behaviourally rather than by grepping the source: the failure this
    guards against is silent (no scheduler means no nightly earnings pull, and
    the store simply goes stale over weeks with nothing on ``/health`` to say
    so), and a comment mentioning ``scheduler.loop`` must not be able to satisfy
    it. ``app.py`` does ``from services._scaffold import make_app``, so patching
    the attribute on the scaffold and reloading the module captures the real
    call."""
    import importlib

    import pytest

    import services._scaffold as scaffold
    from services.trade_svc import app as app_module
    from services.trade_svc import scheduler

    seen = {}
    real = scaffold.make_app

    def _capture(domain, **kw):
        seen.update(kw)
        return real(domain, **kw)

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(scaffold, "make_app", _capture)
        importlib.reload(app_module)
    finally:
        mp.undo()
        importlib.reload(app_module)   # leave the real app for the other tests

    assert seen.get("scheduler") is scheduler.loop
