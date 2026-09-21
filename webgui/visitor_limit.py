"""How many public scans one visitor may ask for, per hour. In memory only.

The public Strategy Finder (``pages/options/finder_live.py``) lets anyone put a
symbol on ``cmd:finder_public``. The service's daily budget caps what that can
cost; this caps what ONE visitor can take of it, so a single address cannot
fill the serial queue and push every other visitor's request past its wait
limit. Roadmap: docs/plans/2026-09-21-public-strategy-finder-roadmap.md.

⚠ No address is written anywhere - not to Redis, not to disk, not to the log.
A restart forgets every count; the daily budget stays the hard limit.

⚠ The address rule is a COPY of ``main._client_ip``, because the public process
must never ``import main`` (it registers every private route). Behind Caddy the
peer is always 127.0.0.1, so the LAST ``X-Forwarded-For`` hop is used, and only
when Caddy's ``X-Edge`` header is present: Caddy replaces that header and
appends the peer it saw, so a visitor can lengthen the list but not change its
tail. ``webgui/tests/test_visitor_limit.py`` pins the header name to
``auth_middleware.EDGE_HEADER``.
"""
from __future__ import annotations

import collections
import threading
import time

EDGE_HEADER = "x-edge"
WINDOW_SEC = 3600


def client_key(request) -> str:
    """The address one visitor's requests are counted against."""
    if request is None:
        return "unknown"
    peer = request.client.host if getattr(request, "client", None) else ""
    headers = getattr(request, "headers", None) or {}
    if headers.get(EDGE_HEADER) is not None:
        hops = [h.strip() for h in headers.get("x-forwarded-for", "").split(",")
                if h.strip()]
        if hops:
            return hops[-1]
    return peer or "unknown"


class Limiter:
    """At most ``limit()`` allowed calls per key in any ``WINDOW_SEC``.

    ``limit`` is a callable so the configured value is read on each check and a
    Settings change applies without a restart. Keys with no calls left in the
    window are forgotten, so the map holds only visitors seen this hour."""

    def __init__(self, limit, clock=time.monotonic):
        self._limit = limit
        self._clock = clock
        self._seen: dict = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def allow(self, key) -> bool:
        now = self._clock()
        with self._lock:
            self._prune(now)
            hits = self._seen[key]
            if len(hits) >= self._limit():
                return False
            hits.append(now)
            return True

    def _prune(self, now) -> None:
        for key in list(self._seen):
            hits = self._seen[key]
            while hits and now - hits[0] >= WINDOW_SEC:
                hits.popleft()
            if not hits:
                del self._seen[key]

    def __len__(self) -> int:
        return len(self._seen)
