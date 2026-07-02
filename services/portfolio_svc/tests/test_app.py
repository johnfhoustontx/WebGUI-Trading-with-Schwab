"""Tests for the portfolio service FastAPI app (Task #21).

Portfolio is command-driven AND scheduled: ``make_app`` is wired with both the
command handler and the streaming scheduler loop. The app uses a fakeredis ``Bus``
under pytest, so startup's command-consumer loop blocks harmlessly on an empty
``cmd:portfolio`` stream and the scheduler's startup rebuild runs against a
(likely down) proxy → an empty published model.
"""
from fastapi.testclient import TestClient

from services.portfolio_svc import handlers, scheduler


def test_health(monkeypatch):
    # Keep the startup scheduler hermetic: stub the rebuild/publish so the app
    # test never reaches the live proxy (rebuild behavior is covered by
    # test_handlers/test_scheduler).
    monkeypatch.setattr(handlers, "rebuild", lambda bus, state: None)
    monkeypatch.setattr(handlers, "publish_current", lambda bus, state: 0)

    from services.portfolio_svc.app import app

    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["domain"] == "portfolio"
        assert body["up"] is True
        assert body["scheduler_alive"] is True


def test_app_wires_command_handler_and_scheduler():
    from services.portfolio_svc import app as app_module

    assert app_module.handlers.handle_command is handlers.handle_command
    assert app_module.scheduler.loop is scheduler.loop
