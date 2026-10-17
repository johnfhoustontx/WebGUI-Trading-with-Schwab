"""Approving a sign-in from the phone, through the ntfy app.

Password typed and the code box left empty: ``login_page.attempt`` answers
"pending", the route creates a :class:`Pending` here and :func:`publish` sends
an ntfy push offering three two-digit numbers. Each number is a link to the
app's ``/approve-sign-in`` page, which is GATED like every other page: only a
phone already signed in to the app can open it, and pressing Approve there for
the number the waiting page shows settles it. The waiting page then collects
the session.

Why approval happens inside the app and not on the notification: a public
ntfy topic can be read by anyone who learns its name, and by the server's
operator. A push carrying an approve button would let someone holding the
password AND the topic approve their own sign-in. The links in a push are
worthless without a signed-in session.

Two ids, never the same one: the PENDING id lives only in the waiting
browser's cookie (it collects the session), the APPROVAL id only in the push
(it names what a signed-in phone is approving). Neither alone signs anyone in.

Held in memory: a web app restart drops every waiting approval, and the owner
signs in again. Design: docs/plans/2026-10-17-login-push-approval-design.md.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import os
import secrets
import threading
import time

from repo_paths import LOGIN_PUSH_TOML
from shared.config_toml import toml_loader

log = logging.getLogger(__name__)

DEFAULTS = {
    "enabled": True,
    "server": "https://ntfy.sh",
    "ttl_sec": 120,
    "max_pending": 5,
    "publish_timeout_sec": 5,
    "priority": 4,
}

load, reset_cache = toml_loader(LOGIN_PUSH_TOML, DEFAULTS, label="login_push.toml")

TITLE = "Sign in to NeuralStrike?"
APPROVE_PATH = "/approve-sign-in"
_SYS_RANDOM = secrets.SystemRandom()


def _number(key, lo, hi, *, integer):
    raw = load().get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULTS[key]
    if integer and raw != int(raw):
        return DEFAULTS[key]
    if not (math.isfinite(raw) and lo <= raw <= hi):
        return DEFAULTS[key]
    return int(raw) if integer else float(raw)


def enabled() -> bool:
    raw = load().get("enabled")
    return raw if isinstance(raw, bool) else DEFAULTS["enabled"]


def server() -> str:
    raw = load().get("server")
    if isinstance(raw, str) and raw.strip().lower().startswith("https://"):
        return raw.strip()
    return DEFAULTS["server"]


def ttl_sec() -> int:
    return _number("ttl_sec", 10, 3600, integer=True)


def max_pending() -> int:
    return _number("max_pending", 1, 50, integer=True)


def publish_timeout_sec() -> float:
    return float(_number("publish_timeout_sec", 0.5, 30, integer=False))


def priority() -> int:
    return _number("priority", 1, 5, integer=True)


def topic() -> str | None:
    """The ntfy topic from ``NTFY_TOPIC`` (a secret: the stack .env), or None."""
    value = (os.environ.get("NTFY_TOPIC") or "").strip()
    return value or None


def configured() -> bool:
    """Whether an empty code box waits for the phone. Off = the form behaves
    exactly as it did before this existed."""
    return enabled() and topic() is not None


# ── the pending approvals ────────────────────────────────────────────────────
@dataclasses.dataclass
class Pending:
    pending_id: str
    approval_id: str
    number: int
    offered: tuple
    client: str
    keep: bool
    next_path: str
    created: float
    state: str = "waiting"        # "waiting" | "approved" | "refused"


class PendingStore:
    """The approvals waiting for the phone. Every call drops what has lapsed,
    so an expired approval reads exactly like one that never existed."""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_pending: dict[str, Pending] = {}

    def _prune(self, now):
        ttl = ttl_sec()
        for pid in [k for k, p in self._by_pending.items() if now - p.created >= ttl]:
            del self._by_pending[pid]

    @staticmethod
    def _now(now):
        return time.time() if now is None else now

    def has_room(self, now=None) -> bool:
        with self._lock:
            self._prune(self._now(now))
            return len(self._by_pending) < max_pending()

    def create(self, *, client, keep, next_path, now=None) -> Pending:
        at = self._now(now)
        offered = tuple(_SYS_RANDOM.sample(range(10, 100), 3))
        p = Pending(pending_id=secrets.token_urlsafe(32),
                    approval_id=secrets.token_urlsafe(32),
                    number=secrets.choice(offered), offered=offered,
                    client=str(client), keep=bool(keep),
                    next_path=str(next_path), created=at)
        with self._lock:
            self._prune(at)
            self._by_pending[p.pending_id] = p
        return p

    def by_pending(self, pending_id, now=None) -> Pending | None:
        """The waiting browser's approval, unless it was refused or lapsed."""
        with self._lock:
            self._prune(self._now(now))
            p = self._by_pending.get(pending_id) if isinstance(pending_id, str) else None
            return p if p is not None and p.state != "refused" else None

    def by_approval(self, approval_id, now=None) -> Pending | None:
        """A still-WAITING approval named by a push's link, or None."""
        with self._lock:
            self._prune(self._now(now))
            return self._find_waiting(approval_id)

    def _find_waiting(self, approval_id):
        if not isinstance(approval_id, str) or not approval_id:
            return None
        for p in self._by_pending.values():
            if p.state == "waiting" and secrets.compare_digest(p.approval_id, approval_id):
                return p
        return None

    def answer(self, approval_id, number, approve, now=None) -> str:
        """Settle an approval from the signed-in phone: ``"approved"`` for
        Approve on the right number, ``"refused"`` for a wrong number or Not
        me, ``"ended"`` when nothing is waiting under that id."""
        with self._lock:
            self._prune(self._now(now))
            p = self._find_waiting(approval_id)
            if p is None:
                return "ended"
            p.state = "approved" if approve and number == p.number else "refused"
            return p.state

    def collect(self, pending_id, now=None) -> Pending | None:
        """Remove and return the approval once it is APPROVED; None otherwise."""
        with self._lock:
            self._prune(self._now(now))
            p = self._by_pending.get(pending_id) if isinstance(pending_id, str) else None
            if p is None or p.state != "approved":
                return None
            del self._by_pending[pending_id]
            return p

    def drop(self, pending_id) -> None:
        with self._lock:
            self._by_pending.pop(pending_id, None)

    def refuse(self, pending_id) -> None:
        with self._lock:
            p = self._by_pending.get(pending_id)
            if p is not None and p.state == "waiting":
                p.state = "refused"


STORE = PendingStore()


def reset_store() -> None:
    """Tests: forget every approval."""
    STORE.__init__()


# ── the push ─────────────────────────────────────────────────────────────────
def build_message(p: Pending, *, topic: str, app_url: str) -> dict:
    """The ntfy JSON for ``p``: three VIEW actions, one per offered number, each
    opening the app's approve page. Carries the approval id and never the
    pending id. Pure."""
    base = app_url.rstrip("/")
    order = list(p.offered)
    _SYS_RANDOM.shuffle(order)
    return {
        "topic": topic,
        "title": TITLE,
        "message": ("A sign-in is waiting. Tap the number shown on that screen, "
                    "then Approve. If you did not just sign in, ignore this. "
                    f"From {p.client}."),
        "priority": priority(),
        "tags": ["key"],
        "actions": [{"action": "view", "label": str(n), "clear": True,
                     "url": f"{base}{APPROVE_PATH}?a={p.approval_id}&n={n}"}
                    for n in order],
    }


def publish(p: Pending, *, app_url: str, post=None) -> bool:
    """Send the push for ``p``. True when ntfy accepted it. Never raises: a
    failure is logged and the sign-in falls back to the code."""
    name = topic()
    if name is None:
        return False
    if post is None:
        import requests        # lazy: only a push sign-in needs it
        post = requests.post
    headers = {}
    token = (os.environ.get("NTFY_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = post(server(), json=build_message(p, topic=name, app_url=app_url),
                    headers=headers, timeout=publish_timeout_sec())
    except Exception as exc:  # noqa: BLE001 - the code is the fallback
        log.warning("Sign-in push not sent (%s)", type(exc).__name__)
        return False
    ok = getattr(resp, "status_code", None) == 200
    if not ok:
        log.warning("Sign-in push refused by ntfy (HTTP %s)",
                    getattr(resp, "status_code", "?"))
    return ok
