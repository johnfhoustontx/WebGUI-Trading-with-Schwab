"""The ONE path to X. Nothing else in the repo calls the X API.

v2 media upload (``POST /2/media/upload``, one-shot, image < 5 MB) then v2 create
post (``POST /2/tweets``), OAuth 1.0a user context through requests-oauthlib.
tweepy's ``media_upload`` is the v1.1 endpoint X has been retiring, which is why
this does not use tweepy. ⚠ The v2 upload's exact form fields are unverified
against a live account until the first real post; ``dry_run`` exists for that.

Every attempt is appended to ``cache:options:x_log`` (the /x page reads it) and to
``x_posts.jsonl`` on disk. **Never raises**: a refusal or a failure comes back as
``{"ok": False, "error": ...}``. The daily cap counts real posts only, per CT day.
⚠ The cap is read-then-written, not atomic — safe because options_svc is the only
caller and runs one command consumer; a second posting process needs a lock.
"""
import datetime as _dt
import json
import logging
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
    kinds = x.get("kinds")
    if not isinstance(kinds, dict):
        return False
    entry = kinds.get(kind)
    return isinstance(entry, dict) and bool(entry.get("enabled"))


def _record(bus, entry):
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


def _check(resp, what):
    if resp.status_code >= 300:
        raise RuntimeError(f"{what} HTTP {resp.status_code}: {str(resp.text)[:200]}")
    return resp.json()


def _send(creds, text, png):
    s = _session(creds)
    body = {"text": text}
    if png:
        up = _check(s.post(f"{API}/media/upload",
                           files={"media": ("card.png", png, "image/png")},
                           data={"media_category": "tweet_image", "media_type": "image/png"},
                           timeout=_TIMEOUT), "media upload")
        body["media"] = {"media_ids": [str(up["data"]["id"])]}
    out = _check(s.post(f"{API}/tweets", json=body, timeout=_TIMEOUT), "create post")
    return str(out["data"]["id"])


def post(bus, text, png=None, *, kind, now=None, config=None, meta=None):
    """Post ``text`` (+ optional PNG bytes) to X. Returns
    ``{"ok", "id", "url", "error", "dry_run"}``; never raises."""
    result = {"ok": False, "id": None, "url": None, "error": None, "dry_run": False}
    try:
        now = now or _dt.datetime.now(_CT)
        entry = _entry(now, kind, text, png, meta)
    except Exception as exc:  # noqa: BLE001 -- e.g. a naive ``now``-less clock fault
        result["error"] = str(exc)[:300]
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
        if x.get("dry_run"):
            result.update(ok=True, dry_run=True)
            return _done("dry_run")
        creds = {k: x.get(k) for k in _CREDS}
        if not all(creds.values()):
            return _done("refused", "no credentials")
        cap = _daily_cap(x)
        count = posted_today(bus, now)
        if cap and count >= cap:
            return _done("refused", f"daily cap ({cap}) reached")
        post_id = _send(creds, text, png)
        entry["id"] = post_id
        entry["url"] = f"https://x.com/i/web/status/{post_id}"
        result.update(ok=True, id=post_id, url=entry["url"])
        try:
            bus.cache_set(COUNT_KEY, {"day": _day(now), "count": count + 1})
        except Exception:  # noqa: BLE001 -- the post IS live; do not report it failed
            log.warning("x daily count write failed", exc_info=True)
        return _done("posted")
    except Exception as exc:  # noqa: BLE001 -- never raises, by contract
        log.warning("x %s post failed: %s", kind, exc)
        result.update(ok=False, id=None, url=None, dry_run=False)
        return _done("failed", str(exc)[:300])
