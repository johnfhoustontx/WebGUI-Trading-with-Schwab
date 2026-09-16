"""The domain scheduler's heartbeat — when its loop last went round.

The scaffold's supervisor only sees the scheduler coroutine start, raise or
return. A healthy loop does none of those after startup, so a loop that HANGS —
stuck on an await, never raising — is invisible to it. ``/health`` used to
publish "seconds since the scheduler last (re)started" as
``scheduler_last_tick_age_s``, which on a healthy service is just uptime: prod
read 8,178 s on a loop ticking every 30 s (2026-09-16).

Each service's ``scheduler.loop`` calls :func:`tick` once per iteration, and
``/health`` reports :func:`age_s` as the real tick age. A plain module, not part
of ``_scaffold``, so a scheduler can import it without pulling in FastAPI.

One scheduler per service process, so one module-level timestamp is the whole
state. ``None`` means "no tick this run": the supervisor calls :func:`reset` at
every (re)start, so a tick from a dead run is never reported as a live one.
"""
import time

_last_tick = None


def tick() -> None:
    """Record that the scheduler loop just went round."""
    global _last_tick
    _last_tick = time.monotonic()


def reset() -> None:
    """Forget the last tick — called when a scheduler run (re)starts."""
    global _last_tick
    _last_tick = None


def age_s():
    """Seconds since the last tick, or ``None`` if this run has not ticked."""
    if _last_tick is None:
        return None
    return round(time.monotonic() - _last_tick, 3)
