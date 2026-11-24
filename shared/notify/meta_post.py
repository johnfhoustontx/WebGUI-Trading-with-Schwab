"""The ONE path to Facebook and Instagram. Nothing else in the repo calls Meta.

One call posts a trade idea or a market report to both networks through Meta's
Graph API, with a Page access token (the operator's, in the ``meta`` block of
``shared/notifications.json``):

* Facebook: ``POST /{page-id}/feed`` with ``message`` and ``link``. Facebook
  draws the linked page's preview card from its ``og:`` tags.
* Instagram: ``POST /{ig-user-id}/media`` with ``image_url`` (a public JPEG) and
  ``caption``, then ``GET /{container}?fields=status_code`` until ``FINISHED``,
  then ``POST /{ig-user-id}/media_publish`` with ``creation_id``.

Every network's attempt is one entry in ``cache:options:meta_log`` (Social Cards
reads it) and one line of ``meta_posts.jsonl``. **Never raises.** The daily cap
is per network and counts real posts and UNKNOWN ones (a call that may have
landed: fail closed). An unknown post is never retried here or by a caller.

⚠ The token is a form field, never a logged value: no exception's text is
logged or returned (only its type), and Meta's own error message has the token
cut out before it is kept. ``_LOCK`` serialises every call in this process, from
the cap check to the count write; a second posting PROCESS would need a lock in
Redis.
"""
import datetime as _dt
import json
import logging
import threading
import time
from zoneinfo import ZoneInfo

from repo_paths import META_POSTS_LOG
from shared import meta_text
from shared.notify.channels import load_config

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"
LOG_KEY = "cache:options:meta_log"
COUNT_KEY = "cache:options:meta_count"
LOG_KEEP = 100
KINDS = ("trade_idea", "report")
NETWORKS = ("facebook", "instagram")
_CT = ZoneInfo("America/Chicago")
_TOKEN_DEAD = 190          # Graph's "access token expired or revoked"
_LOCK = threading.RLock()
_sleep = time.sleep


class _Failed(Exception):
    """A definite refusal: nothing was posted. The message is our own and safe."""


class _Unknown(Exception):
    """The call may have posted, but no answer confirmed it."""


def _day(now):
    return now.astimezone(_CT).date().isoformat()


def _counts(bus, now):
    env = bus.cache_get(COUNT_KEY)
    p = env.payload if env is not None else None
    return p if isinstance(p, dict) and p.get("day") == _day(now) else {"day": _day(now)}


def posted_today(bus, network, now):
    """Real (and unknown) posts to ``network`` on ``now``'s Central day."""
    try:
        return int(_counts(bus, now).get(network) or 0)
    except (TypeError, ValueError):
        return 0


def _int(value, default):
    if isinstance(value, bool):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def _section(cfg, name):
    v = (cfg if isinstance(cfg, dict) else {}).get(name)
    return v if isinstance(v, dict) else {}


def _scrub(text, token):
    text = str(text or "")
    return text.replace(token, "***") if token else text


def _record(bus, entry):
    with _LOCK:
        try:
            env = bus.cache_get(LOG_KEY)
            p = env.payload if env is not None else None
            posts = list(p.get("posts") or []) if isinstance(p, dict) else []
            bus.cache_set(LOG_KEY, {"posts": ([entry] + posts)[:LOG_KEEP]})
        except Exception:  # noqa: BLE001 -- a lost log line must not lose the post
            log.warning("meta log write failed")
        try:
            META_POSTS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with META_POSTS_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:  # noqa: BLE001
            log.warning("meta jsonl append failed")


def _answer(resp, what, token, *, maybe_posted):
    """The JSON of a 2xx answer. A 4xx, or any answer carrying ``error``, is
    ``_Failed``; a 5xx or an unreadable 2xx is ``_Unknown`` when the call may have
    posted (``maybe_posted``) and ``_Failed`` otherwise."""
    status = getattr(resp, "status_code", 0) or 0
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict) or status >= 400:
        err = err if isinstance(err, dict) else {}
        if err.get("code") == _TOKEN_DEAD:
            raise _Failed("token expired or revoked")
        message = _scrub(err.get("message") or f"{what} refused", token)[:200]
        if status >= 500 and maybe_posted:
            raise _Unknown(f"{status}: {message}")
        raise _Failed(f"{status}: {message}")
    if not isinstance(body, dict):
        if maybe_posted:
            raise _Unknown(f"{what}: unreadable answer")
        raise _Failed(f"{what}: unreadable answer")
    return body


def _send(s, method, url, what, token, *, maybe_posted, timeout, **kw):
    try:
        resp = (s.post if method == "POST" else s.get)(url, timeout=timeout, **kw)
    except Exception as exc:  # noqa: BLE001 -- the text may carry the URL / token
        if maybe_posted:
            raise _Unknown(f"{what}: no answer ({type(exc).__name__})") from None
        raise _Failed(f"{what} failed ({type(exc).__name__})") from None
    return _answer(resp, what, token, maybe_posted=maybe_posted)


def _facebook(s, base, meta, text, link, timeout):
    token = meta["access_token"]
    body = _send(s, "POST", f"{base}/{meta['page_id']}/feed", "facebook post", token,
                 maybe_posted=True, timeout=timeout,
                 data={"message": text, "link": link, "access_token": token})
    return str(body.get("id") or "") or None


def _instagram(s, base, meta, caption, image_url, graph, timeout):
    token, user = meta["access_token"], meta["ig_user_id"]
    made = _send(s, "POST", f"{base}/{user}/media", "instagram upload", token,
                 maybe_posted=False, timeout=timeout,
                 data={"image_url": image_url, "caption": caption, "access_token": token})
    container = str(made.get("id") or "")
    if not container:
        raise _Failed("instagram upload: no container id")
    tries = max(_int(graph.get("ig_poll_tries"), 5), 1)
    pause = _int(graph.get("ig_poll_sec"), 2)
    state = None
    for i in range(tries):
        got = _send(s, "GET", f"{base}/{container}", "instagram check", token,
                    maybe_posted=False, timeout=timeout,
                    params={"fields": "status_code", "access_token": token})
        state = str(got.get("status_code") or "")
        if state == "FINISHED":
            break
        if state in ("ERROR", "EXPIRED"):
            raise _Failed(f"instagram image {state}")
        if i + 1 < tries and pause:
            _sleep(pause)
    else:
        raise _Failed(f"instagram image not ready ({state or 'no status'})")
    done = _send(s, "POST", f"{base}/{user}/media_publish", "instagram publish", token,
                 maybe_posted=True, timeout=timeout,
                 data={"creation_id": container, "access_token": token})
    return str(done.get("id") or "") or None


def _gate(network, kind, meta, settings, image_url, bus, now):
    """None when ``network`` may post, else the refusal's reason."""
    if not meta.get("enabled"):
        return "disabled"
    if kind not in KINDS or not _section(settings, "kinds").get(kind, True):
        return f"{kind} disabled"
    net = _section(settings, network)
    if not net.get("enabled", True):
        return f"{network} disabled"
    owner = "page_id" if network == "facebook" else "ig_user_id"
    if not all(isinstance(meta.get(k), str) and meta[k].strip()
               for k in (owner, "access_token")):
        return "no credentials"
    if network == "instagram" and not image_url:
        return "no image"
    cap = _int(net.get("daily_cap"), meta_text.DEFAULTS[network]["daily_cap"])
    if cap and posted_today(bus, network, now) >= cap:
        return f"daily cap ({cap}) reached"
    return None


def post(bus, *, kind, fb_text, ig_text, link, image_url, networks=NETWORKS, now=None,
         config=None, settings=None, session=None, meta=None):
    """Post to each network in ``networks``. Returns ``{"dry_run", "facebook",
    "instagram"}``, each network ``{"status", "id", "reason"}`` with status
    ``posted`` / ``unknown`` / ``failed`` / ``refused`` / ``dry_run`` /
    ``skipped``. Never raises.

    ``config`` is the notifications config (its ``meta`` block: switches and
    credentials), ``settings`` the ``meta_post.toml`` mapping; both are read when
    not given. ``session`` is a ``requests.Session``-like object."""
    out = {"dry_run": False,
           **{n: {"status": "skipped", "id": None, "reason": None} for n in NETWORKS}}
    with _LOCK:
        try:
            now = now or _dt.datetime.now(_CT)
            cfg = config if config is not None else load_config()
            block = cfg.get("meta") if isinstance(cfg, dict) else None
            block = block if isinstance(block, dict) else {}
            settings = settings if settings is not None else meta_text.config()
            out["dry_run"] = bool(block.get("dry_run"))
            box = [session]   # a session made here on first need is closed here
            try:
                for network in NETWORKS:
                    if network in networks:
                        out[network] = _one(bus, network, kind, block, settings, now,
                                            fb_text if network == "facebook" else ig_text,
                                            link, image_url, meta, box)
            finally:
                if session is None and box[0] is not None:
                    box[0].close()
        except Exception as exc:  # noqa: BLE001 -- never raises, by contract
            log.warning("meta post: fault (%s)", type(exc).__name__)
    return out


def _one(bus, network, kind, block, settings, now, text, link, image_url, meta, box):
    result = {"status": "refused", "id": None, "reason": None}
    token = block.get("access_token") if isinstance(block.get("access_token"), str) else ""
    try:
        reason = _gate(network, kind, block, settings, image_url, bus, now)
    except Exception as exc:  # noqa: BLE001 -- a bus fault: nothing was sent
        result.update(status="failed", reason=f"check failed ({type(exc).__name__})")
    else:
        if reason is not None:
            result["reason"] = reason
        elif block.get("dry_run"):
            result["status"] = "dry_run"
        else:
            graph = _section(settings, "graph")
            base = f"{GRAPH}/{str(graph.get('version') or meta_text.DEFAULTS['graph']['version'])}"
            timeout = _int(graph.get("timeout_sec"), 30) or 30
            if box[0] is None:
                import requests
                box[0] = requests.Session()
            try:
                if network == "facebook":
                    post_id = _facebook(box[0], base, block, text, link, timeout)
                else:
                    post_id = _instagram(box[0], base, block, text, image_url, graph,
                                         timeout)
                result.update(status="posted", id=post_id)
            except _Unknown as exc:
                result.update(status="unknown", reason=_scrub(exc, token)[:300])
            except _Failed as exc:
                result.update(status="failed", reason=_scrub(exc, token)[:300])
            except Exception as exc:  # noqa: BLE001 -- ours, never the token's text
                result.update(status="failed", reason=f"{network} failed ({type(exc).__name__})")
            if result["status"] in ("posted", "unknown"):
                _count(bus, network, now)
    _record(bus, {"at": now.isoformat(), "kind": kind, "network": network,
                  "status": result["status"], "reason": result["reason"],
                  "id": result["id"], "link": link, "text": text, "meta": meta or {}})
    log.info("meta %s %s: %s %s", network, kind, result["status"],
             result["reason"] or result["id"] or "")
    return result


def _count(bus, network, now):
    try:
        counts = _counts(bus, now)
        counts[network] = posted_today(bus, network, now) + 1
        bus.cache_set(COUNT_KEY, counts)
    except Exception:  # noqa: BLE001 -- the post may be live; keep its status
        log.warning("meta daily count write failed")
