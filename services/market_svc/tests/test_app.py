from fastapi.testclient import TestClient


def test_app_health():
    from services.market_svc.app import app
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["domain"] == "market"
