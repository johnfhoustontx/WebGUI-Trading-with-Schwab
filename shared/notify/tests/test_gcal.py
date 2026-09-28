"""shared/notify/gcal.py: the trade idea's Google Calendar event.

The HTTP layer is stubbed; the RSA key is generated here, so the JWT signature is
verified against a real public key rather than trusted."""
import base64
import datetime as dt
import json
import logging

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from shared.notify import gcal

TOKEN_URI = "https://oauth2.example/token"
CAL = "abc123@group.calendar.google.com"
NOW = dt.datetime(2026, 9, 28, 10, 35, 12, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _prod_like(monkeypatch):
    """Under pytest every suppression is ON (repo_paths), so the calendar would
    no-op before reaching the code under test. The dev-gate test flips it back."""
    monkeypatch.setitem(gcal.ENV_FLAGS, "allow_notifications", True)


@pytest.fixture
def key(tmp_path):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    path = tmp_path / "sa.json"
    path.write_text(json.dumps({"type": "service_account",
                                "client_email": "bot@proj.iam.gserviceaccount.com",
                                "private_key": pem, "token_uri": TOKEN_URI}))
    gcal.reset()
    yield path, priv.public_key()
    gcal.reset()


class _Resp:
    def __init__(self, code=200, body=None):
        self.status_code, self._body = code, body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


@pytest.fixture
def http(monkeypatch):
    calls = []
    replies = {"token": _Resp(200, {"access_token": "tok-1", "expires_in": 3600}),
               "event": _Resp(200, {"id": "evt"})}

    def post(url, **kw):
        calls.append((url, kw))
        return replies["token" if url == TOKEN_URI else "event"]
    monkeypatch.setattr(gcal.requests, "post", post)
    return calls, replies


def _b64(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _create(path, **kw):
    args = dict(calendar_id=CAL, lead_min=1, duration_min=5, key_path=path, now=NOW)
    args.update(kw)
    return gcal.create_event("Trade idea: SPY put credit spread", "the caption", **args)


def test_the_token_request_carries_a_verifiable_rs256_jwt(key, http):
    path, pub = key
    calls, _ = http
    assert _create(path) is True
    url, kw = calls[0]
    assert url == TOKEN_URI
    assert kw["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    head, claims, sig = kw["data"]["assertion"].split(".")
    assert json.loads(_b64(head)) == {"alg": "RS256", "typ": "JWT"}
    c = json.loads(_b64(claims))
    assert c["iss"] == "bot@proj.iam.gserviceaccount.com"
    assert c["scope"] == "https://www.googleapis.com/auth/calendar.events"
    assert c["aud"] == TOKEN_URI and c["exp"] - c["iat"] == 3600
    pub.verify(_b64(sig), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_the_event_uses_the_calendars_default_reminder(key, http):
    path, _ = key
    calls, _ = http
    _create(path, lead_min=2, duration_min=7)
    url, kw = calls[1]
    assert url == ("https://www.googleapis.com/calendar/v3/calendars/"
                   "abc123%40group.calendar.google.com/events")
    assert kw["headers"]["Authorization"] == "Bearer tok-1"
    body = kw["json"]
    assert body["summary"] == "Trade idea: SPY put credit spread"
    assert body["description"] == "the caption"
    assert body["reminders"] == {"useDefault": True}
    start = dt.datetime.fromisoformat(body["start"]["dateTime"])
    end = dt.datetime.fromisoformat(body["end"]["dateTime"])
    assert start == NOW + dt.timedelta(minutes=2)
    assert end - start == dt.timedelta(minutes=7)
    assert body["start"]["timeZone"] == "America/Chicago"


def test_the_token_is_reused_until_it_nears_expiry(key, http):
    path, _ = key
    calls, _ = http
    _create(path)
    _create(path, now=NOW + dt.timedelta(minutes=30))
    assert [u for u, _ in calls].count(TOKEN_URI) == 1
    _create(path, now=NOW + dt.timedelta(minutes=59, seconds=30))   # < 60 s left
    assert [u for u, _ in calls].count(TOKEN_URI) == 2


def test_no_key_file_or_no_calendar_makes_no_call(tmp_path, http):
    calls, _ = http
    assert _create(tmp_path / "missing.json") is False
    assert _create(tmp_path / "missing.json", calendar_id="") is False
    assert calls == []


def test_a_blank_calendar_id_makes_no_call_even_with_a_key(key, http):
    path, _ = key
    calls, _ = http
    assert _create(path, calendar_id="  ") is False
    assert calls == []


@pytest.mark.parametrize("which", ["token", "event"])
def test_an_http_rejection_is_logged_never_raised(key, http, which, caplog):
    path, _ = key
    _calls, replies = http
    replies[which] = _Resp(403, {"error": "forbidden"})
    with caplog.at_level(logging.WARNING, logger=gcal.log.name):
        assert _create(path) is False
    assert "403" in caplog.text


def test_a_network_error_is_logged_never_raised(key, monkeypatch, caplog):
    path, _ = key

    def boom(*_a, **_k):
        raise ConnectionError("no route")
    monkeypatch.setattr(gcal.requests, "post", boom)
    with caplog.at_level(logging.WARNING, logger=gcal.log.name):
        assert _create(path) is False
    assert "no route" in caplog.text


def test_a_garbage_key_file_is_logged_never_raised(tmp_path, http, caplog):
    bad = tmp_path / "sa.json"
    bad.write_text('{"client_email": "x", "private_key": "not a pem"}')
    with caplog.at_level(logging.WARNING, logger=gcal.log.name):
        assert _create(bad) is False
    assert http[0] == []


def test_a_non_prod_checkout_never_writes_to_a_calendar(key, http, monkeypatch):
    path, _ = key
    monkeypatch.setitem(gcal.ENV_FLAGS, "allow_notifications", False)
    assert _create(path) is False
    assert http[0] == []
