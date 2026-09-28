"""Google Calendar events for the hourly trade idea (config/notify.toml).

A service-account client with no Google SDK: the OAuth assertion is an RS256 JWT
signed with ``cryptography`` and the two calls are plain ``requests`` - both
already locked, so this adds no dependency to prod's venv.

⚠ **The popup is the CALENDAR's default notification, not this module's.** An
event's ``reminders`` field is "for the authenticated user", which here is the
service account - a popup override written through the API would never reach the
operator's phone. So events are created with ``useDefault: true`` on a dedicated
calendar whose default notification the operator sets to "at time of event".

Best-effort like every sender in ``channels``: no key file or no calendar id is a
silent no-op, any failure is one WARNING, and nothing here ever raises into the
trade-idea send.
"""
import base64
import datetime as _dt
import json
import logging
import threading
import urllib.parse

import requests

from repo_paths import ENV_FLAGS, GCAL_SERVICE_ACCOUNT

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/calendar.events"
_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
_TZ = "America/Chicago"
_TIMEOUT = 10
_TOKEN_LIFE_SEC = 3600
_TOKEN_MARGIN_SEC = 60          # refresh this long before the token expires

_lock = threading.Lock()
_token: dict = {}               # {"key": (path, email), "value": str, "exp": float}


def reset() -> None:
    """Drop the cached access token (tests; a rotated key)."""
    with _lock:
        _token.clear()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _assertion(key: dict, now_ts: int) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({
        "iss": key["client_email"], "scope": SCOPE,
        "aud": key.get("token_uri") or _DEFAULT_TOKEN_URI,
        "iat": now_ts, "exp": now_ts + _TOKEN_LIFE_SEC,
    }).encode())
    signer = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    sig = signer.sign(f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{claims}.{_b64(sig)}"


def _rejected(what, resp) -> bool:
    code = getattr(resp, "status_code", None)
    if code is not None and 200 <= code < 300:
        return False
    log.warning("Google Calendar %s rejected: HTTP %s %s", what, code,
                (getattr(resp, "text", "") or "")[:300])
    return True


def _access_token(key: dict, key_path, now_ts: float):
    ident = (str(key_path), key.get("client_email"))
    with _lock:
        if (_token.get("key") == ident
                and now_ts < _token.get("exp", 0) - _TOKEN_MARGIN_SEC):
            return _token["value"]
    resp = requests.post(key.get("token_uri") or _DEFAULT_TOKEN_URI, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": _assertion(key, int(now_ts)),
    }, timeout=_TIMEOUT)
    if _rejected("token", resp):
        return None
    body = resp.json()
    value = body.get("access_token")
    if not value:
        log.warning("Google Calendar token response had no access_token")
        return None
    with _lock:
        _token.update(key=ident, value=value,
                      exp=now_ts + float(body.get("expires_in") or _TOKEN_LIFE_SEC))
    return value


def create_event(summary, description, *, calendar_id, lead_min, duration_min,
                 key_path=GCAL_SERVICE_ACCOUNT, now=None) -> bool:
    """Create one event starting ``lead_min`` after ``now``. True if Google
    accepted it. Never raises."""
    calendar_id = (calendar_id or "").strip()
    if not ENV_FLAGS.get("allow_notifications", True) or not calendar_id:
        return False
    try:
        with open(key_path, encoding="utf-8") as fh:
            key = json.load(fh)
    except FileNotFoundError:
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("Google Calendar key %s unreadable: %s", key_path, exc)
        return False
    try:
        now = now or _dt.datetime.now(_dt.timezone.utc)
        token = _access_token(key, key_path, now.timestamp())
        if not token:
            return False
        start = now + _dt.timedelta(minutes=lead_min)
        end = start + _dt.timedelta(minutes=duration_min)
        resp = requests.post(
            _EVENTS_URL.format(cal=urllib.parse.quote(calendar_id, safe="")),
            headers={"Authorization": f"Bearer {token}"},
            json={"summary": summary, "description": description,
                  "start": {"dateTime": start.isoformat(), "timeZone": _TZ},
                  "end": {"dateTime": end.isoformat(), "timeZone": _TZ},
                  "reminders": {"useDefault": True}},
            timeout=_TIMEOUT)
        return not _rejected("event", resp)
    except Exception as exc:  # noqa: BLE001 - best-effort, like every sender
        log.warning("Google Calendar event failed: %s", exc)
        return False

