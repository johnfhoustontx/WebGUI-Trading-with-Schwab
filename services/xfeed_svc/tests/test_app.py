from fastapi.testclient import TestClient


def test_health_answers():
    from services.xfeed_svc.app import app
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["domain"] == "xfeed"


def test_the_service_port_is_configured():
    from repo_paths import SERVICE_PORTS
    assert SERVICE_PORTS["xfeed"] == 8219
