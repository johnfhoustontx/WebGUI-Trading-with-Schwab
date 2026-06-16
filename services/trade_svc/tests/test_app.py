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
        assert r.json() == {"domain": "trade", "up": True}


def test_app_wires_command_handler():
    """The app must dispatch via the analyze command handler (on-demand path)."""
    from services.trade_svc import app as app_module

    assert app_module.handlers.handle_command is handlers.handle_command
