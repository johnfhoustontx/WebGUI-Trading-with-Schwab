import importlib

from fastapi.testclient import TestClient


def test_app_health():
    from services.news_svc.app import app
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["domain"] == "news"


def test_app_health_does_not_poll(monkeypatch):
    """Under pytest the scheduler is suppressed, and a /health read must never
    reach the feeds."""
    from services.news_svc import compute
    calls = []
    monkeypatch.setattr(compute, "poll_now", lambda bus: calls.append(bus))
    from services.news_svc.app import app
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert calls == []


def test_app_is_wired_to_the_news_scheduler_and_handler(monkeypatch):
    from services import _scaffold
    from services.news_svc import app as app_mod
    from services.news_svc import handlers, scheduler

    seen = {}

    def fake_make_app(domain, **kw):
        seen["domain"] = domain
        seen.update(kw)
        return "sentinel"

    monkeypatch.setattr(_scaffold, "make_app", fake_make_app)
    try:
        importlib.reload(app_mod)
        assert app_mod.app == "sentinel"
        assert seen["domain"] == "news"
        assert seen["scheduler"] is scheduler.loop
        assert seen["command_handler"] is handlers.handle_command
    finally:
        monkeypatch.undo()
        importlib.reload(app_mod)


def test_importing_the_app_polls_nothing(monkeypatch):
    from services.news_svc import compute
    calls = []
    monkeypatch.setattr(compute, "poll_now", lambda bus: calls.append(bus))
    from services.news_svc import app as app_mod
    importlib.reload(app_mod)
    assert calls == []


def test_the_service_port_is_configured():
    from repo_paths import SERVICE_PORTS
    assert SERVICE_PORTS["news"] == 8216
