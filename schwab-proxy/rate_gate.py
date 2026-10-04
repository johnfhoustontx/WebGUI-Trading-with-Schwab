"""Request spacing for the Schwab market-data API, with a priority lane.

Every market-data call the proxy sends passes ONE gate, ``interval`` seconds
apart (5 a second as shipped). Until 2026-10-03 the gate was a lock held across
a sleep: whoever got the lock went next. The one-minute collection poll and a
quarter-hour scan's chain burst therefore shared it roughly half and half, the
poll ran past its minute, and the next minute's collection slot was skipped -
20 of the 22 slots lost over four measured sessions were in the minute after a
scan started (audit PF-02).

The gate now has two lanes:

* a request marked priority goes before ordinary requests that are waiting;
* after ``priority_run`` priority requests in a row have gone AHEAD of a waiting
  ordinary one, one ordinary request goes. Ordinary traffic is slowed while the
  poll fetches and is never stopped;
* each lane is first come, first served;
* ``priority_run = 0`` switches the lane off: one queue, arrival order.

The total rate is unchanged. The poll needs the same number of calls either
way, so a scan gets the same number of slots per minute; it gets them after the
poll's instead of between them.

Pure standard library, no imports from the proxy: the clock and the timed wait
are injectable so the ordering is tested without depending on thread timing.
"""
from __future__ import annotations

import collections
import threading
import time

# Priority requests that may go ahead of a waiting ordinary one before an
# ordinary one is let through. 4 of every 5 slots while both lanes are busy.
DEFAULT_PRIORITY_RUN = 4

_PRIORITY, _NORMAL = "priority", "normal"


class RateGate:
    """One slot every ``interval`` seconds, priority lane first.

    ``priority_run`` is a number or a zero-argument callable returning one; a
    callable is read on every request, so a saved setting applies without a
    restart. Anything unusable reads as :data:`DEFAULT_PRIORITY_RUN`.
    """

    def __init__(self, interval, *, priority_run=DEFAULT_PRIORITY_RUN,
                 clock=time.monotonic, pause=None):
        self._interval = interval
        self._run = priority_run
        self._clock = clock
        self._pause = pause or (lambda cond, seconds: cond.wait(seconds))
        self._cond = threading.Condition()
        self._queues = {_PRIORITY: collections.deque(), _NORMAL: collections.deque()}
        self._last = None           # when the last slot was granted
        self._streak = 0            # priority grants that overtook an ordinary waiter
        self._granted = {_PRIORITY: 0, _NORMAL: 0}

    # ------------------------------------------------------------ settings
    def interval(self) -> float:
        value = self._interval() if callable(self._interval) else self._interval
        return value if isinstance(value, (int, float)) and value > 0 else 0.0

    def run_length(self) -> int:
        """How many priority requests may overtake a waiting ordinary one."""
        try:
            value = self._run() if callable(self._run) else self._run
        except Exception:  # noqa: BLE001 - a setting never stops a request
            return DEFAULT_PRIORITY_RUN
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return DEFAULT_PRIORITY_RUN
        return value

    # ------------------------------------------------------------- reading
    def waiting(self):
        """``(priority, ordinary)`` requests waiting for a slot."""
        with self._cond:
            return len(self._queues[_PRIORITY]), len(self._queues[_NORMAL])

    def granted(self) -> dict:
        """Slots granted so far, by the lane each was served in."""
        with self._cond:
            return dict(self._granted)

    # -------------------------------------------------------------- the gate
    def _next_up(self, run):
        p, n = self._queues[_PRIORITY], self._queues[_NORMAL]
        if p and n:
            return n[0] if self._streak >= run else p[0]
        if p:
            return p[0]
        return n[0] if n else None

    def acquire(self, priority=False) -> None:
        """Block until this request may be sent."""
        run = self.run_length()
        lane = _PRIORITY if (priority and run > 0) else _NORMAL
        me = object()
        with self._cond:
            queue = self._queues[lane]
            queue.append(me)
            try:
                while True:
                    if self._next_up(run) is not me:
                        self._pause(self._cond, None)
                        continue
                    wait = (0.0 if self._last is None
                            else self._last + self.interval() - self._clock())
                    if wait > 0:
                        self._pause(self._cond, wait)
                        continue
                    queue.popleft()
                    self._last = self._clock()
                    if lane == _NORMAL:
                        self._streak = 0
                    elif self._queues[_NORMAL]:
                        self._streak += 1
                    self._granted[lane] += 1
                    return
            finally:
                # Leaving for any reason - granted or interrupted - must not
                # leave a ticket at the head of a lane with nobody behind it.
                try:
                    queue.remove(me)
                except ValueError:
                    pass
                self._cond.notify_all()
