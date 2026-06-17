"""Tests for the driver service FastAPI app (Task #30).

Driver is command-driven AND scheduled: ``make_app`` is wired with both the
command handler and the 09:28-ET scheduler loop. The app uses a fakeredis ``Bus``
under pytest, so startup's command-consumer loop blocks harmlessly on an empty
``cmd:driver`` stream and the scheduler's startup perf refresh runs against fake
data.
"""
from fastapi.testclient import TestClient

from services.driver_svc import handlers, scheduler


def test_health():
    from services.driver_svc.app import app

    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json() == {"domain": "driver", "up": True}


def test_app_wires_command_handler_and_scheduler():
    from services.driver_svc import app as app_module

    assert app_module.handlers.handle_command is handlers.handle_command
    assert app_module.scheduler.loop is scheduler.loop
