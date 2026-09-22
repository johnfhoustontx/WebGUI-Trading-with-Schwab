"""The ONE path to X. Nothing else in the repo calls the X API.

v2 media upload (``POST /2/media/upload``, one-shot, image < 5 MB) then v2 create
post (``POST /2/tweets``), OAuth 1.0a user context through requests-oauthlib.
tweepy's ``media_upload`` is the v1.1 endpoint X has been retiring, which is why
this does not use tweepy. ⚠ The v2 upload's exact form fields are unverified
against a live account until the first real post; ``dry_run`` exists for that.

Every attempt is appended to ``cache:options:x_log`` (the /x page reads it) and to
``x_posts.jsonl`` on disk. **Never raises**: a refusal or a failure comes back as
``{"ok": False, "error": ...}``. The daily cap counts real posts only, per CT day.
⚠ The cap is read-then-written, not atomic in Redis. TWO threads in options_svc
call ``post`` - the scheduler's trade idea (a thread-pool worker) and the command
consumer (``x_post`` / ``x_post_report``) - so a module-level ``_LOCK`` serialises
every post from the cap check through the count write and the log line, and every
``record_refusal``. That covers one process only: a second posting PROCESS would
need a Redis-side lock.
"""
import datetime as _dt
import json
import logging
import threading
from zoneinfo import ZoneInfo

from repo_paths import X_POSTS_LOG
from shared.notify.channels import load_config

log = logging.getLogger(__name__)

API = "https://api.x.com/2"
LOG_KEY = "cache:options:x_log"
COUNT_KEY = "cache:options:x_count"
LOG_KEEP = 100
KINDS = ("report", "trade_idea", "marketing")
DEFAULT_DAILY_CAP = 15   # mirrors channels._DEFAULTS["x"]["daily_cap"]
_CREDS = ("api_key", "api_secret", "access_token", "access_secret")
_CT = ZoneInfo("America/Chicago")
_TIMEOUT = 30
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
# Serialises every post and every log write in this process. Re-entrant because
# post() writes its log line through _record while holding it.
_LOCK = threading.RLock()


def _session(creds):
    from requests_oauthlib import OAuth1Session
    return OAuth1Session(creds["api_key"], client_secret=creds["api_secret"],
                         resource_owner_key=creds["access_token"],
                         resource_owner_secret=creds["access_secret"])


def _day(now):
    return now.astimezone(_CT).date().isoformat()


def posted_today(bus, now):
    """Real posts made on ``now``'s CT day (dry runs and refusals are not counted)."""
    env = bus.cache_get(COUNT_KEY)
    p = env.payload if env is not None else None
    if not (isinstance(p, dict) and p.get("day") == _day(now)):
        return 0
    try:
        return int(p.get("count") or 0)
    except (TypeError, ValueError):
        return 0


def _daily_cap(x):
    """The configured cap; 0 = no cap. A malformed value falls back to the default,
    never to "no cap" — a typo must not unbound the account."""
    raw = x.get("daily_cap", DEFAULT_DAILY_CAP)
    if isinstance(raw, bool):
        return DEFAULT_DAILY_CAP
    try:
        cap = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CAP
    return cap if cap >= 0 else DEFAULT_DAILY_CAP


def _kind_enabled(x, kind):
    """``KINDS`` is the allow-list: a kind outside it is refused whatever the
    config says, so a hand-edited ``kinds`` block cannot invent a post type."""
    if kind not in KINDS:
        return False
    kinds = x.get("kinds")
    if not isinstance(kinds, dict):
        return False
    entry = kinds.get(kind)
    return isinstance(entry, dict) and bool(entry.get("enabled"))


def _record(bus, entry):
    with _LOCK:   # re-entered from post(); an RLock
        _record_locked(bus, entry)


def _record_locked(bus, entry):
    try:
        env = bus.cache_get(LOG_KEY)
        p = env.payload if env is not None else None
        posts = list(p.get("posts") or []) if isinstance(p, dict) else []
        bus.cache_set(LOG_KEY, {"posts": ([entry] + posts)[:LOG_KEEP]})
    except Exception:  # noqa: BLE001 -- a lost log line must not lose the post
        log.warning("x log write failed", exc_info=True)
    try:
        X_POSTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with X_POSTS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:  # noqa: BLE001
        log.warning("x jsonl append failed", exc_info=True)


def _entry(now, kind, text, image, meta):
    return {"at": now.isoformat(), "kind": kind, "text": text,
            "image": bool(image), "meta": meta or {}, "status": "refused",
            "reason": None, "id": None, "url": None}


def record_refusal(bus, kind, text, reason, *, now=None, image=False):
    """Log a refusal decided BEFORE ``post`` (e.g. "image over 5 MB"). Never raises."""
    try:
        entry = _entry(now or _dt.datetime.now(_CT), kind, text, image, None)
        entry["reason"] = reason
        _record(bus, entry)
        log.info("x %s: refused %s", kind, reason)
    except Exception:  # noqa: BLE001 -- never raises, by contract
        log.warning("x refusal log failed", exc_info=True)


class _XHttpError(RuntimeError):
    """X answered with an HTTP error: a DEFINITE refusal. Our own message (status +
    the start of X's body) is safe to log verbatim."""


class _Unconfirmed(Exception):
    """The create call may have reached X, but no confirmation came back."""


def _sanitized(what, exc):
    """A loggable reason. Only our own ``_XHttpError`` keeps its message; any other
    exception's text can carry secrets (oauthlib echoes the offending credential in
    its ValueError), so it is reduced to the exception's type name."""
    if isinstance(exc, _XHttpError):
        return str(exc)[:300]
    return f"{what} failed ({type(exc).__name__})"


def _valid_creds(x):
    creds = {k: x.get(k) for k in _CREDS}
    if all(isinstance(v, str) and v.strip() for v in creds.values()):
        return creds
    return None


def _image_type(data):
    """``(filename, mime)`` from the image's magic bytes, or None when it is
    neither PNG nor JPEG - the only two the /x page and the cards produce."""
    if data[:8] == _PNG_MAGIC:
        return "card.png", "image/png"
    if data[:3] == _JPEG_MAGIC:
        return "card.jpg", "image/jpeg"
    return None


def _upload(s, png):
    name, mime = _image_type(png)
    resp = s.post(f"{API}/media/upload",
                  files={"media": (name, png, mime)},
                  data={"media_category": "tweet_image", "media_type": mime},
                  timeout=_TIMEOUT)
    if resp.status_code >= 300:
        raise _XHttpError(f"media upload HTTP {resp.status_code}: {str(resp.text)[:200]}")
    return str(resp.json()["data"]["id"])


def _create(s, body):
    """The id of the new post, or ``None`` when X confirmed (2xx) without one.
    Raises ``_XHttpError`` on an HTTP error, ``_Unconfirmed`` when the call may
    have landed but the answer was lost or unreadable."""
    try:
        resp = s.post(f"{API}/tweets", json=body, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 -- timeout/reset: it may have landed
        raise _Unconfirmed(type(exc).__name__) from None
    if resp.status_code >= 300:
        raise _XHttpError(f"create post HTTP {resp.status_code}: {str(resp.text)[:200]}")
    try:
        out = resp.json()
    except Exception as exc:  # noqa: BLE001 -- a 2xx we cannot read
        raise _Unconfirmed(type(exc).__name__) from None
    data = out.get("data") if isinstance(out, dict) else None
    post_id = data.get("id") if isinstance(data, dict) else None
    return str(post_id) if post_id not in (None, "") else None


def post(bus, text, png=None, *, kind, now=None, config=None, meta=None):
    """Post ``text`` (+ optional PNG or JPEG bytes) to X. Returns
    ``{"ok", "id", "url", "error", "dry_run", "unknown"}``; never raises.

    ``unknown`` is True when the create call may have reached X but no answer
    confirmed it: the entry is logged ``unknown``, it COUNTS toward the daily cap
    (fail closed - better one post short than one over), and ``ok`` is False so a
    caller does not treat it as done. A caller must not blindly retry it: the post
    may be live.

    Held under ``_LOCK`` from the gates through the count write and the log line,
    network call included: posts are rare, and serialising them is exactly what
    the read-then-write cap needs."""
    with _LOCK:
        return _post(bus, text, png, kind=kind, now=now, config=config, meta=meta)


def _post(bus, text, png, *, kind, now, config, meta):
    result = {"ok": False, "id": None, "url": None, "error": None, "dry_run": False,
              "unknown": False}
    try:
        # A naive ``now`` is read as HOST-LOCAL time by ``astimezone`` (for the
        # CT day) and stamped as given in the log; pass an aware datetime.
        now = now or _dt.datetime.now(_CT)
        entry = _entry(now, kind, text, png, meta)
    except Exception:  # noqa: BLE001 -- a ``now`` without ``isoformat``
        result["error"] = "bad timestamp"
        return result

    def _done(status, reason=None):
        entry["status"], entry["reason"] = status, reason
        result["error"] = reason
        _record(bus, entry)
        try:
            log.info("x %s: %s %s", kind, status, reason or entry["id"] or "")
        except Exception:  # noqa: BLE001
            pass
        return result

    try:
        x = (config if config is not None else load_config()).get("x") or {}
        if not isinstance(x, dict) or not x.get("enabled"):
            return _done("refused", "disabled")
        if not _kind_enabled(x, kind):
            return _done("refused", f"{kind} disabled")
        if not png and not str(text or "").strip():
            return _done("refused", "empty post")
        # Before the dry run, so a dry run validates everything but the network.
        if png and _image_type(bytes(png)) is None:
            return _done("refused", "unsupported image type")
        if x.get("dry_run"):
            result.update(ok=True, dry_run=True)
            return _done("dry_run")
        creds = _valid_creds(x)
        if creds is None:
            return _done("refused", "no credentials")
        cap = _daily_cap(x)
        count = posted_today(bus, now)
        if cap and count >= cap:
            return _done("refused", f"daily cap ({cap}) reached")
    except Exception as exc:  # noqa: BLE001 -- never raises, by contract
        # Nothing was sent yet; these are config/bus faults, never credentials.
        log.warning("x %s post refused by a fault: %s", kind, exc)
        return _done("failed", str(exc)[:300])

    def _count():
        try:
            bus.cache_set(COUNT_KEY, {"day": _day(now), "count": count + 1})
        except Exception:  # noqa: BLE001 -- the post may be live; keep its status
            log.warning("x daily count write failed", exc_info=True)

    # Everything below touches credentials or X: no raw exception text is logged.
    try:
        s = _session(creds)
        body = {"text": text}
        if png:
            body["media"] = {"media_ids": [_upload(s, png)]}
    except Exception as exc:  # noqa: BLE001 -- nothing was posted
        reason = _sanitized("media upload" if png else "session", exc)
        log.warning("x %s post failed: %s", kind, reason)
        return _done("failed", reason)
    try:
        post_id = _create(s, body)
    except _Unconfirmed as exc:
        reason = f"sent; X did not confirm ({exc})"
        log.warning("x %s post unconfirmed: %s", kind, reason)
        _count()
        result["unknown"] = True
        return _done("unknown", reason)
    except Exception as exc:  # noqa: BLE001 -- an HTTP refusal: nothing posted
        reason = _sanitized("create post", exc)
        log.warning("x %s post failed: %s", kind, reason)
        return _done("failed", reason)
    entry["id"] = post_id
    entry["url"] = f"https://x.com/i/web/status/{post_id}" if post_id else None
    result.update(ok=True, id=post_id, url=entry["url"])
    _count()
    return _done("posted")
