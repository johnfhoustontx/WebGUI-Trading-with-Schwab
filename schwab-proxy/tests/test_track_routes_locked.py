"""``/track`` and ``/untrack`` need the secret, and ``/track`` reads the Ledger.

Audit SE-100. Both routes took any caller on the loopback or the tailnet. A
caller could replace a real tracked trade's strikes (false target and stop
events in the analytics store) or subscribe invented trades. The paper book
itself was never reachable this way.
"""
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import schwab_proxy as sp  # noqa: E402

SECRET = "s3cret-for-tests"
OPEN_ROW = {"trade_id": "t-open", "symbol": "SPY", "strategy": "PCS",
            "expiration": "2099-01-15", "quantity": 1, "entry_credit": 1.0,
            "short_strike": 500.0, "long_strike": 495.0,
            "call_short": None, "call_long": None}


@pytest.fixture
def locked(monkeypatch):
    monkeypatch.setattr(sp, "PROXY_SHARED_SECRET", SECRET)
    tracked, untracked = [], []
    monkeypatch.setattr(sp, "_track", lambda body, **k: tracked.append(dict(body))
                        or {"status": "ok", "legs": {}})
    monkeypatch.setattr(sp, "_untrack", lambda tid: untracked.append(tid)
                        or {"status": "ok"})
    monkeypatch.setattr(sp, "_read_open_trades", lambda: {"t-open": dict(OPEN_ROW)})
    return TestClient(sp.app), tracked, untracked


H = {"X-Proxy-Secret": SECRET}


@pytest.mark.parametrize("route,body", [("/track", {"trade_id": "t-open"}),
                                        ("/untrack", {"trade_id": "t-open"})])
def test_no_secret_is_refused(locked, route, body):
    client, tracked, untracked = locked
    assert client.post(route, json=body).status_code == 401
    assert client.post(route, json=body, headers={"X-Proxy-Secret": "wrong"}).status_code == 401
    assert tracked == [] and untracked == []


@pytest.mark.parametrize("route", ["/track", "/untrack"])
def test_with_no_secret_configured_the_routes_refuse_everyone(locked, monkeypatch, route):
    client, tracked, untracked = locked
    monkeypatch.setattr(sp, "PROXY_SHARED_SECRET", None)
    r = client.post(route, json={"trade_id": "t-open"}, headers=H)
    assert r.status_code == 503 and "PROXY_SHARED_SECRET" in r.json()["detail"]
    assert tracked == [] and untracked == []


def test_track_follows_the_ledgers_row_not_the_callers(locked):
    """The caller names a trade. What is tracked is what the Ledger holds."""
    client, tracked, _ = locked
    forged = {"trade_id": "t-open", "symbol": "SPY", "strategy": "PCS",
              "expiration": "2099-01-15", "entry_credit": 9.99,
              "short_strike": 1.0, "long_strike": 0.5, "target_mid": 0.01}
    r = client.post("/track", json=forged, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert tracked == [OPEN_ROW]


def test_track_refuses_an_id_the_ledger_does_not_hold_open(locked):
    client, tracked, _ = locked
    r = client.post("/track", json={**OPEN_ROW, "trade_id": "invented"}, headers=H)
    assert r.status_code == 200
    assert r.json()["status"] == "refused" and "open" in r.json()["detail"]
    assert tracked == []


def test_track_refuses_when_the_ledger_cannot_be_read(locked, monkeypatch):
    client, tracked, _ = locked
    monkeypatch.setattr(sp, "_read_open_trades", lambda: None)
    r = client.post("/track", json={"trade_id": "t-open"}, headers=H)
    assert r.json()["status"] == "refused"
    assert tracked == []


def test_track_without_an_id_is_an_error(locked):
    client, tracked, _ = locked
    assert client.post("/track", json={}, headers=H).json()["status"] == "error"
    assert tracked == []


def test_untrack_with_the_secret_still_works(locked):
    client, _, untracked = locked
    assert client.post("/untrack", json={"trade_id": "t-open"}, headers=H).json() == {"status": "ok"}
    assert untracked == ["t-open"]
