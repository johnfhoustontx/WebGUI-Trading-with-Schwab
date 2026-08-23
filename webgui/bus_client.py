"""GUI-side bus client for the NiceGUI webgui (Tier 3 reader / Tier 2 commander).

A thin, process-wide client over ``shared.bus.Bus`` for the single-user app. The
webgui uses this to:

* **read** the Redis (or fakeredis) cache that services publish — no direct
  engine imports;
* **enqueue commands** onto a service's command stream (e.g. "refresh");
* **react to change events** via a background subscription thread.

Dependency-light on purpose: only ``threading`` + ``shared.bus``. This module
must import cleanly under plain pytest (no NiceGUI app). The page layer is
responsible for marshaling event callbacks back onto the UI thread safely.
"""
from __future__ import annotations

import pathlib
import sys
import threading
from typing import Any, Callable, Iterable

# Repo root on sys.path -> ``shared`` package is importable (mirrors webgui/proxy.py).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.bus import Bus  # noqa: E402

_bus: Bus | None = None


def bus() -> Bus:
    """Lazy process-wide Bus singleton (fakeredis under pytest, else Memurai).

    A single shared instance is still the right shape - it avoids per-call
    connection churn, and the EventListener thread and any publisher stay on one
    object. (The fakeredis caveat this docstring used to give as the REASON is
    handled inside ``Bus`` since 2026-08-21: every fake now shares one FakeServer
    per running test, so pub/sub fans out across instances the way prod does.)
    """
    global _bus
    if _bus is None:
        _bus = Bus()
    return _bus


def reset() -> None:
    """Drop the cached Bus so the next ``bus()`` builds a fresh one (test helper)."""
    global _bus
    _bus = None


def read(view: str) -> dict | None:
    """Return the cached payload dict for a view (e.g. 'sentiment:composite'), or None.

    Key is ``f'cache:{view}'``.
    """
    env = bus().cache_get(f"cache:{view}")
    return env.payload if env else None


def read_full(view: str) -> tuple[dict | None, int | None]:
    """Return ``(payload, version)`` for a view in ONE cache read, or ``(None, None)``.

    For callers that need both the payload and its version (e.g. a nav-badge that
    checks both a status field and whether the version advanced) — avoids a second
    round-trip vs ``read`` + ``read_version``.
    """
    env = bus().cache_get(f"cache:{view}")
    return (env.payload, env.version) if env else (None, None)


def read_version(view: str) -> int | None:
    """Return the cache version int for a view, or None if absent.

    Cheap change-detection helper for a fetch-free ``ui.timer``: reads only the
    ``{key}:ver`` counter (a tiny int) — NOT the full payload envelope — so a
    version-poll that finds no change costs one ``GET`` and zero JSON parsing.
    """
    return bus().cache_version(f"cache:{view}")


_GATE_ABSENT = object()   # "we have observed this view as absent"


def read_gated(view: str, memo: dict) -> tuple[dict | None, bool]:
    """``(payload, changed)`` — deserialize only when the view's version moved.

    ``memo`` is a caller-owned dict (``{}`` to start) holding the last-seen
    version and payload. A tick where nothing changed costs ONE tiny ``{key}:ver``
    GET and no JSON parsing, and returns the SAME payload object as last time.

    For the app-wide 2 s watcher this is the difference between ~237 KB of
    transfer+parse per tick per open tab (~10 GB/day/tab) and a pair of integer
    probes; the views it reads change a few times an hour (2026-08-20).

    An absent view is memoized too — ``(None, False)`` on repeat — so a cold or
    never-published key does not re-probe its payload every tick. ``changed`` is
    True on the FIRST observation of any state, including absence, so a caller
    can seed itself on tick one.

    ⚠ A ``None`` version is NOT gated — it reads through, every time.
    ``cache_set`` always INCRs ``{key}:ver`` so anything written by current code
    has a counter, but a pre-upgrade key can carry a payload without one, and a
    memo keyed on ``None`` would have no invalidation signal: it would serve that
    first payload forever. Gate only when there is a version to gate on;
    versionless views simply keep the old always-read behaviour.
    """
    ver = read_version(view)
    if ver is not None and memo.get("ver") == ver:
        return memo["payload"], False
    payload = read(view)
    memo["ver"], memo["payload"] = ver, payload
    return payload, True


def read_versions(views: Iterable[str]) -> dict[str, int | None]:
    """Pipelined :func:`read_version` for many views → ``{view: int|None}``.

    One Redis round-trip for the whole batch — used by pages that poll several
    views on one timer (e.g. Gamma's gamma / gex_status / explain / analyze)."""
    views = list(views)
    raw = bus().cache_versions([f"cache:{v}" for v in views])
    return {v: raw.get(f"cache:{v}") for v in views}


def _envelope_meta(view: str) -> tuple[int | None, str | None]:
    """``(version, ts)`` read from the full envelope — the pre-``:ts`` fallback."""
    env = bus().cache_get(f"cache:{view}")
    return (env.version, env.ts) if env else (None, None)


def read_meta(view: str) -> tuple[int | None, str | None]:
    """Return ``(version, ts)`` for a view, or ``(None, None)`` if absent.

    ``ts`` is the ``{key}:ts`` side key: the ISO-8601 UTC time the publishing
    service last CONFIRMED this view current, which is not the same as when it
    last changed (a ``skip_unchanged`` republish refreshes the stamp without
    touching the payload — see ``Bus.cache_set``). That is the stamp a freshness
    probe wants, and reading it here rather than the envelope keeps this in step
    with :func:`read_metas`; a stale-data view would otherwise report one age on
    the System Status table and another to the nav badge.
    """
    ver, ts = bus().cache_metas([f"cache:{view}"]).get(f"cache:{view}", (None, None))
    if ver is not None and ts is None:      # pre-upgrade write — one-off fallback
        return _envelope_meta(view)
    return (ver, ts)


def read_metas(views: Iterable[str]) -> dict[str, tuple[int | None, str | None]]:
    """Pipelined :func:`read_meta` for many views → ``{view: (version, ts)}``.

    Reads only the tiny ``:ver``/``:ts`` side keys in ONE round-trip — no payload
    deserialize — so the 2s watcher's freshness probe is cheap. Back-compat: a key
    written before the ``:ts`` side key existed (version present, ts absent) falls
    back to a full envelope read for that view only, so freshness stays correct
    across the upgrade until every service has republished.
    """
    views = list(views)
    raw = bus().cache_metas([f"cache:{v}" for v in views])
    out = {}
    for v in views:
        ver, ts = raw.get(f"cache:{v}", (None, None))
        if ver is not None and ts is None:  # pre-upgrade write — one-off fallback
            out[v] = _envelope_meta(v)
        else:
            out[v] = (ver, ts)
    return out


def ping() -> bool:
    """Return True if the Redis/Memurai backbone answers a PING, else False.

    Never raises — a down/unreachable backend yields False. Used by the System
    Status page to report Tier-3 (Memurai) health.
    """
    try:
        return bool(bus()._r.ping())
    except Exception:
        return False


def request(domain: str, command: dict) -> str:
    """Enqueue a command dict (e.g. {'type':'refresh'}) onto ``f'cmd:{domain}'``.

    Returns the Redis stream message id.
    """
    return bus().enqueue_command(f"cmd:{domain}", command)


class EventListener:
    """Background daemon thread that fans an events channel out to a callback.

    Subscribes to ``channel`` on the singleton Bus and calls ``callback(version)``
    for every message carrying a ``"version"`` field. Robust by design for a
    long-lived single-user GUI: the run loop swallows exceptions so a bad
    callback or a transient backend hiccup never kills the thread. Call
    :meth:`stop` to end it.
    """

    def __init__(self, channel: str, callback: Callable[[Any], None],
                 poll_timeout: float = 0.5) -> None:
        self._channel = channel
        self._callback = callback
        self._poll_timeout = poll_timeout
        self._stopped = False
        self._sub: Any = None
        self.subscribed = False  # flips True once the channel subscription is live
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            # Use the singleton Bus so we share the (fake)redis instance with
            # publishers in this process.
            self._sub = bus().subscribe(self._channel)
            self.subscribed = True
        except Exception:
            return
        while not self._stopped:
            try:
                msg = self._sub.get_message(timeout=self._poll_timeout)
                if msg and "version" in msg:
                    self._callback(msg["version"])
            except Exception:
                # Never let a bad callback or transient error kill the thread.
                continue

    def stop(self) -> None:
        """Signal the loop to exit and best-effort close the subscription."""
        self._stopped = True
        try:
            if self._sub is not None:
                self._sub.close()
        except Exception:
            pass


def on_event(channel: str, callback: Callable[[Any], None]) -> EventListener:
    """Start an :class:`EventListener` on ``channel`` and return it.

    Example: ``on_event('events:sentiment:composite', lambda v: ...)``.
    """
    return EventListener(channel, callback)
