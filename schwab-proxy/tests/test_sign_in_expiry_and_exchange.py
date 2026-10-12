"""The 7-day sign-in: when it really ends, and signing in again.

Measured on prod 2026-10-11. The sign-in was made on 2026-10-04 at 15:56 CT and
Schwab rejected the refresh token on 2026-10-11 at 16:15 CT, seven days later.
At that moment ``/health`` reported **165 hours left**: every 25-minute token
refresh re-stamped ``RefreshTokenExpiresAt`` to "now + 7 days", so the countdown
never ran down and the daily warning (``tools/token_watch.py``) could never
fire. A refresh renews the ACCESS token. It does not extend the sign-in.

The same evening showed the second fault: after a successful sign-in the proxy
went on reporting "rejected by Schwab" until its next refresh, because only a
refresh cleared the flag. The operator restarted the proxy to clear it.

The third part is the route the web app's Authorize dialog uses, so signing in
again no longer needs the tailnet or an SSH tunnel.
"""
import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta

import pytest


def _load_proxy():
    root = pathlib.Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    path = root / "schwab-proxy" / "schwab_proxy.py"
    spec = importlib.util.spec_from_file_location("schwab_proxy_sign_in_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


proxy = _load_proxy()

SIGNED_IN_EXPIRY = "2026-10-11T20:56:15Z"          # 7 days after the sign-in


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def _manager(tmp_path, monkeypatch, **tokens):
    monkeypatch.setattr(proxy, "TOKEN_FILE", tmp_path / "proxy_tokens.json")
    tm = proxy.TokenManager.__new__(proxy.TokenManager)
    tm.app_key = "the-app-key"
    tm.app_secret = "the-app-secret"
    tm.callback_url = "https://127.0.0.1:8182"
    tm._lock = proxy.threading.Lock()
    tm.tokens = {"AccessToken": "old-access-token", "RefreshToken": "r",
                 "RefreshTokenExpiresAt": SIGNED_IN_EXPIRY, **tokens}
    tm.refresh_rejected = False
    tm.refresh_error = None
    return tm


# ── a refresh does not extend the sign-in ────────────────────────────────────
@pytest.mark.parametrize("answer", [
    # Schwab hands the same refresh token back on every refresh...
    {"access_token": "new", "expires_in": 1800, "refresh_token": "r"},
    # ...and a body that names a lifetime for it must not move the stamp either.
    {"access_token": "new", "expires_in": 1800, "refresh_token": "r",
     "refresh_token_expires_in": 604800},
    # A rotated value is stored, and still does not restart the seven days.
    {"access_token": "new", "expires_in": 1800, "refresh_token": "r2"},
    {"access_token": "new", "expires_in": 1800},
])
def test_a_refresh_leaves_the_sign_in_expiry_where_the_sign_in_put_it(
        tmp_path, monkeypatch, answer):
    tm = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(proxy.requests, "post", lambda *a, **k: _Resp(200, answer))
    tm._refresh()
    assert tm.tokens["AccessToken"] == "new"
    assert tm.tokens["RefreshTokenExpiresAt"] == SIGNED_IN_EXPIRY
    assert tm.tokens["RefreshToken"] == answer.get("refresh_token", "r")


def test_the_countdown_runs_down_across_a_week_of_refreshes(tmp_path, monkeypatch):
    """The measured failure, replayed: refresh every 25 minutes for six days
    and the hours left must be about one day, not about seven."""
    start = datetime(2026, 10, 4, 20, 56, 15)
    tm = _manager(tmp_path, monkeypatch,
                  RefreshTokenExpiresAt=(start + timedelta(days=7)).isoformat() + "Z")
    clock = {"now": start}

    class _Clock(datetime):
        @classmethod
        def utcnow(cls):
            return clock["now"]

    monkeypatch.setattr(proxy, "datetime", _Clock)
    monkeypatch.setattr(proxy.requests, "post", lambda *a, **k: _Resp(
        200, {"access_token": "a", "expires_in": 1800, "refresh_token": "r"}))
    for _ in range(6 * 24 * 60 // 25):
        clock["now"] += timedelta(minutes=25)
        tm._refresh()
    assert 23.5 <= tm.refresh_hours_left() <= 24.5
    assert tm._is_refresh_expired() is False
    clock["now"] = start + timedelta(days=7, minutes=1)
    assert tm.refresh_hours_left() == 0.0
    assert tm._is_refresh_expired() is True


# ── signing in again ─────────────────────────────────────────────────────────
def test_a_sign_in_stamps_seven_days_from_now_and_clears_the_rejection(
        tmp_path, monkeypatch):
    tm = _manager(tmp_path, monkeypatch)
    tm.refresh_rejected = True                      # as on the evening it lapsed
    tm.refresh_error = "400: invalid_grant"
    seen = {}

    def post(url, **kw):
        seen.update(kw["data"])
        return _Resp(200, {"access_token": "fresh", "expires_in": 1800,
                           "refresh_token": "r-new"})

    monkeypatch.setattr(proxy.requests, "post", post)
    tm.exchange_auth_code("the-code")
    assert seen == {"grant_type": "authorization_code", "code": "the-code",
                    "redirect_uri": "https://127.0.0.1:8182"}
    assert tm.tokens["AccessToken"] == "fresh" and tm.tokens["RefreshToken"] == "r-new"
    left = tm.refresh_hours_left()
    assert 167.9 <= left <= 168.0
    # Schwab has just accepted a sign-in: its last refusal is history.
    assert tm.refresh_rejected is False and tm.refresh_error is None


def test_a_refused_sign_in_changes_nothing(tmp_path, monkeypatch):
    tm = _manager(tmp_path, monkeypatch)
    tm.refresh_rejected = True
    monkeypatch.setattr(proxy.requests, "post",
                        lambda *a, **k: _Resp(400, {"error": "invalid_grant"}))
    with pytest.raises(RuntimeError):
        tm.exchange_auth_code("stale")
    assert tm.tokens["AccessToken"] == "old-access-token"
    assert tm.tokens["RefreshTokenExpiresAt"] == SIGNED_IN_EXPIRY
    assert tm.refresh_rejected is True


# ── pulling the code out of what the user pasted ─────────────────────────────
@pytest.mark.parametrize("pasted, code", [
    ("https://127.0.0.1:8182/?code=C0.abc%40&session=xyz", "C0.abc@"),
    ("https://127.0.0.1:8182?code=C0.abc%40", "C0.abc@"),
    ("  https://127.0.0.1:8182/?session=1&code=Q1  ", "Q1"),
    ("https://127.0.0.1:8182/?code=", None),
    ("https://127.0.0.1:8182/", None),
    ("not a url", None),
    ("", None),
    (None, None),
    (12, None),
])
def test_code_from_pasted(pasted, code):
    assert proxy.code_from_pasted(pasted) == code


# ── the two routes the web app's dialog calls ────────────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    tm = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(proxy, "token_mgr", tm, raising=False)
    return TestClient(proxy.app), tm


def test_auth_info_gives_the_schwab_login_link_and_no_secret(client):
    c, tm = client
    body = c.get("/auth/info").json()
    assert body["auth_url"] == (
        "https://api.schwabapi.com/v1/oauth/authorize"
        "?client_id=the-app-key&redirect_uri=https://127.0.0.1:8182")
    assert body["callback_url"] == "https://127.0.0.1:8182"
    assert set(body) == {"auth_url", "callback_url"}
    assert tm.app_secret not in str(body) and "old-access-token" not in str(body)


def test_auth_exchange_signs_in_from_a_pasted_address(client, monkeypatch):
    c, tm = client
    got = []
    monkeypatch.setattr(tm, "exchange_auth_code", got.append)
    r = c.post("/auth/exchange",
               json={"url": "https://127.0.0.1:8182/?code=C0.abc%40&session=s"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert got == ["C0.abc@"]


def test_auth_exchange_refuses_a_paste_with_no_code_without_calling_schwab(
        client, monkeypatch):
    c, tm = client
    monkeypatch.setattr(tm, "exchange_auth_code",
                        lambda code: pytest.fail("Schwab must not be called"))
    for body in ({"url": "https://127.0.0.1:8182/"}, {"url": ""}, {}, {"url": 5}):
        r = c.post("/auth/exchange", json=body)
        assert r.status_code == 400, body
        assert r.json()["ok"] is False and "code" in r.json()["error"]


def test_auth_exchange_reports_schwabs_refusal_as_a_message_not_a_crash(
        client, monkeypatch):
    c, tm = client

    def refuse(code):
        raise RuntimeError("Auth code exchange failed (400): invalid_grant")

    monkeypatch.setattr(tm, "exchange_auth_code", refuse)
    r = c.post("/auth/exchange", json={"url": "https://127.0.0.1:8182/?code=old"})
    assert r.status_code == 502
    assert r.json() == {"ok": False,
                        "error": "Auth code exchange failed (400): invalid_grant"}


def test_the_code_is_sent_in_the_body_so_it_is_not_in_the_access_log(client):
    """``GET /auth/callback?url=...`` puts the one-time code in the request
    line, which uvicorn logs. The dialog's route is a POST for that reason."""
    c, _ = client
    assert c.get("/auth/exchange").status_code == 405
    route = next(r for r in proxy.app.routes if getattr(r, "path", "") == "/auth/exchange")
    assert route.methods == {"POST"}
