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
import pathlib
import sys
import threading

# Repo root on sys.path -> ``shared`` package is importable (mirrors webgui/proxy.py).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.bus import Bus  # noqa: E402

_bus = None


def bus():
    """Lazy process-wide Bus singleton (fakeredis under pytest, else Memurai).

    A single shared instance matters: fakeredis pub/sub only fans out across the
    same FakeStrictRedis object, so the EventListener thread and any publisher
    must both go through this singleton.
    """
    global _bus
    if _bus is None:
        _bus = Bus()
    return _bus


def reset():
    """Drop the cached Bus so the next ``bus()`` builds a fresh one (test helper)."""
    global _bus
    _bus = None


def read(view):
    """Return the cached payload dict for a view (e.g. 'sentiment:composite'), or None.

    Key is ``f'cache:{view}'``.
    """
    env = bus().cache_get(f"cache:{view}")
    return env.payload if env else None


def read_version(view):
    """Return the cache version int for a view, or None if absent.

    Cheap change-detection helper for a fetch-free ``ui.timer``: compare the
    version to the last painted one and only repaint/read the payload on change.
    """
    env = bus().cache_get(f"cache:{view}")
    return env.version if env else None


def request(domain, command):
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

    def __init__(self, channel, callback, poll_timeout: float = 0.5):
        self._channel = channel
        self._callback = callback
        self._poll_timeout = poll_timeout
        self._stopped = False
        self._sub = None
        self.subscribed = False  # flips True once the channel subscription is live
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
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

    def stop(self):
        """Signal the loop to exit and best-effort close the subscription."""
        self._stopped = True
        try:
            if self._sub is not None:
                self._sub.close()
        except Exception:
            pass


def on_event(channel, callback):
    """Start an :class:`EventListener` on ``channel`` and return it.

    Example: ``on_event('events:sentiment:composite', lambda v: ...)``.
    """
    return EventListener(channel, callback)
