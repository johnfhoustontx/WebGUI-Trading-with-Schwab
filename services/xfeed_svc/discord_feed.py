"""The watched X accounts' live posts, to a Discord channel as FxEmbed links.

Each post goes to the channel as one plain message holding its link on an
FxEmbed host (``https://fixupx.com/<author>/status/<id>``), and Discord draws
the post from that link: text, images, video, the quoted post. Nothing of the
post's own text is sent, so there is nothing here to escape, and
``allowed_mentions`` is empty so a link can never ping anyone.

What is sent (config/xfeed.toml ``[discord]``, re-read on every poll):

- only posts the poller marks ``live`` (page one of a recent poll): never an
  account's baseline when it is added, nor a catch-up after downtime;
- only accounts the group rule takes (``groups`` / ``ungrouped``, as the public
  site's ``[public]``), and only the post ``types`` named;
- at most ``max_per_poll`` from one account's poll, the rest recorded as skipped.

Once only: every status is claimed in ``discord_sent`` (xfeed.db) BEFORE it is
queued, keyed on the status id alone, so a status is offered to Discord at most
once, across restarts and across two accounts reposting it. A post still queued
when the service stops is not sent later: a late link is worse than none.

The webhook is ``routes.x_feed.discord`` in shared/notifications.json, resolved
by ``channels.discord_route_only``: no fall back to another channel's webhook,
off with the master switch (always off outside prod) and with the X feed's
Discord switch on Settings -> General.

One daemon thread sends, ``send_gap_s`` apart. A 429 waits the ``retry_after``
Discord gives; a 5xx or no answer waits and tries again; each up to
``max_retries``. ⚠ The webhook URL holds its token, and a ``requests``
exception's text holds the URL, so only an exception's TYPE is ever logged or
stored.
"""
import logging
import math
import queue
import re
import threading
import time

from services import _degrade
from services.xfeed_svc import config, groups

log = logging.getLogger("xfeed_svc.discord")

AREA = "xfeed.discord"
CATEGORY = "x_feed"
TYPES = ("post", "reply", "repost")
LIVE = "live"

QUEUED, SENT, FAILED, SKIPPED, DROPPED = "queued", "sent", "failed", "skipped", "dropped"

# The longest single wait a 429 or a retry may impose; a larger retry_after is
# capped, never obeyed for minutes with the queue stalled behind it.
MAX_WAIT_S = 60.0

_STATUS_URL = re.compile(
    r"https://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/([0-9]{1,25})", re.ASCII)
_HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}", re.ASCII)
_STATUS_ID = re.compile(r"[0-9]{1,25}", re.ASCII)
_HOST = re.compile(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,24}", re.ASCII)

_RANGES = {   # key: (low, high, kind)
    "max_per_poll": (1, 100, int),
    "send_gap_s": (0.5, 60.0, float),
    "max_retries": (0, 10, int),
    "timeout_s": (1, 60, int),
    "queue_max": (1, 5000, int),
    "keep_days": (1, 3650, int),
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def settings(raw) -> dict:
    """``[discord]`` read defensively: each value of the wrong type or out of
    range is the shipped one, key by key."""
    d = config.DEFAULTS["discord"]
    p = raw if isinstance(raw, dict) else {}
    out = {"rule": groups.rule(p, d)}
    types = p.get("types")
    if isinstance(types, list) and all(isinstance(t, str) for t in types):
        types = [t.strip().lower() for t in types if t.strip().lower() in TYPES]
    else:
        types = list(d["types"])
    out["types"] = types
    host = p.get("link_host")
    host = host.strip().lower() if isinstance(host, str) else ""
    out["link_host"] = host if _HOST.fullmatch(host) else d["link_host"]
    for key, (lo, hi, kind) in _RANGES.items():
        v = p.get(key)
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi
        if kind is int and ok and float(v) != int(v):
            ok = False
        out[key] = kind(v) if ok else d[key]
    return out


def link_for(post, host) -> str | None:
    """The post's FxEmbed link, or None when it has no well-formed status URL
    and no well-formed author and id to build one from."""
    m = _STATUS_URL.fullmatch(post.get("url") or "")
    if m:
        handle, sid = m.groups()
    else:
        handle, sid = post.get("author_handle") or "", str(post.get("status_id") or "")
        if not (_HANDLE.fullmatch(handle) and _STATUS_ID.fullmatch(sid)):
            return None
    return f"https://{host}/{handle}/status/{sid}"


def _retry_after(resp) -> float:
    """Seconds a 429 asks to wait: the JSON ``retry_after``, else the
    ``Retry-After`` header, else one second; capped at ``MAX_WAIT_S``."""
    wait = None
    try:
        body = resp.json()
        if isinstance(body, dict):
            wait = float(body.get("retry_after"))
    except Exception:  # noqa: BLE001 - a body that is not JSON: try the header
        wait = None
    if wait is None or not math.isfinite(wait):
        try:
            wait = float(resp.headers.get("Retry-After"))
        except (TypeError, ValueError):
            wait = 1.0
    if not math.isfinite(wait) or wait < 0:
        wait = 1.0
    return min(wait, MAX_WAIT_S)


def send(webhook, link, *, post, sleep, retries, timeout) -> tuple:
    """POST one link. ``(state, detail)``: ``("sent", None)`` or ``("failed",
    why)``. A 429 waits Discord's ``retry_after``; a 5xx or no answer waits 1,
    2, 4 ... seconds; each kind up to ``retries`` times. Another 4xx (a deleted
    webhook is a 404) fails at once. Never raises."""
    body = {"content": link, "allowed_mentions": {"parse": []}}
    limited = errors = 0
    while True:
        try:
            resp = post(webhook, json=body, timeout=timeout)
            code = int(resp.status_code)
        except Exception as exc:  # noqa: BLE001 - the type only: its text holds the webhook URL
            detail = f"no answer ({type(exc).__name__})"
            errors += 1
            if errors > retries:
                return FAILED, detail
            sleep(min(2.0 ** (errors - 1), MAX_WAIT_S))
            continue
        if 200 <= code < 300:
            return SENT, None
        if code == 429:
            limited += 1
            if limited > retries:
                return FAILED, "HTTP 429"
            sleep(_retry_after(resp))
            continue
        if code >= 500:
            errors += 1
            if errors > retries:
                return FAILED, f"HTTP {code}"
            sleep(min(2.0 ** (errors - 1), MAX_WAIT_S))
            continue
        return FAILED, f"HTTP {code}"


def _http_post(url, **kw):
    import requests
    return requests.post(url, **kw)


def _webhook() -> str:
    from shared.notify import channels
    return channels.discord_route_only(channels.load_config(), CATEGORY)


class Sender:
    """Picks a poll's new posts for Discord and sends them from one thread."""

    def __init__(self, store, *, webhook=_webhook, post=_http_post, sleep=time.sleep,
                 now_ms=_now_ms, load_cfg=config.load):
        self._store = store
        self._webhook = webhook
        self._post = post
        self._sleep = sleep
        self._now_ms = now_ms
        self._load_cfg = load_cfg
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._thread_guard = threading.Lock()
        self._last_prune = None

    def _settings(self) -> dict:
        return settings(self._load_cfg().get("discord"))

    def offer(self, account, posts) -> int:
        """Queue ``account``'s newly seen ``posts`` that Discord should get;
        returns how many were queued. Never raises (the poll goes on)."""
        try:
            return self._offer(account, posts)
        except Exception as exc:  # noqa: BLE001
            _degrade.degraded(AREA, detail=f"choosing posts for Discord ({type(exc).__name__})", exc_info=False)
            return 0

    def _offer(self, account, posts) -> int:
        s = self._settings()
        if not groups.matches(account, s["rule"]):
            return 0
        picks = [p for p in posts or () if p.get("kind", "post") == "post"
                 and p.get("origin") == LIVE and p.get("type") in s["types"]]
        if not picks or not self._webhook():
            return 0
        picks.sort(key=lambda p: (p.get("created_at") or 0, str(p.get("status_id"))))  # oldest first
        jobs = [(str(p["status_id"]), link) for p in picks
                if (link := link_for(p, s["link_host"])) is not None]
        now = self._now_ms()
        self._prune(now, s)
        claimed = self._store.claim_discord([(account["handle"], sid) for sid, _ in jobs], now=now)
        queued = 0
        for sid, link in jobs:
            if sid not in claimed:
                continue
            if queued >= s["max_per_poll"]:
                self._store.mark_discord(sid, SKIPPED, now=now, detail="over the per-poll cap")
                continue
            if self._q.qsize() >= s["queue_max"]:
                self._store.mark_discord(sid, SKIPPED, now=now, detail="the send queue was full")
                continue
            self._q.put((sid, link))
            queued += 1
        if queued:
            self._ensure_thread()
        return queued

    def _prune(self, now, s) -> None:
        """Drop records past ``keep_days``, at most once an hour."""
        if self._last_prune is not None and now - self._last_prune < 3_600_000:
            return
        self._last_prune = now
        self._store.prune_discord(before=now - s["keep_days"] * 86_400_000)

    def _ensure_thread(self) -> None:
        with self._thread_guard:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name="xfeed-discord", daemon=True)
                self._thread.start()

    def stop(self) -> None:
        """Ask the sending thread to end after its current post (shutdown)."""
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            self.deliver(*job)

    def deliver(self, sid, link) -> str:
        """Send one queued post and record the outcome; returns the state.
        Never raises (the thread must outlive any one post)."""
        try:
            s = self._settings()
            hook = self._webhook() if s["rule"]["enabled"] else ""
            if not hook:
                self._store.mark_discord(sid, DROPPED, now=self._now_ms(), detail="switched off before sending")
                return DROPPED
            state, detail = send(hook, link, post=self._post, sleep=self._sleep,
                                 retries=s["max_retries"], timeout=s["timeout_s"])
            self._store.mark_discord(sid, state, now=self._now_ms(), detail=detail)
            if state != SENT:
                _degrade.degraded(AREA, detail=f"a post was not sent to Discord: {detail}", exc_info=False)
            self._sleep(s["send_gap_s"])
            return state
        except Exception as exc:  # noqa: BLE001
            _degrade.degraded(AREA, detail=f"sending a post to Discord ({type(exc).__name__})", exc_info=False)
            return FAILED
