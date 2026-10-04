"""The proxy's request spacing, with a lane for the one-minute collection poll.

Audit PF-02. Every Schwab market-data call passes one limiter at 5 a second.
Measured on prod over four sessions (2026-09-29..10-02): 22 one-minute
collection slots were lost, 20 of them in the minute after a quarter-hour scan
started - the poll shared the limiter with the scan's chain burst first come,
first served, ran past 60 seconds and the next minute was skipped.

``RateGate`` keeps the spacing and adds an order: when both lanes are waiting,
priority requests go first, and after ``priority_run`` of them in a row one
ordinary request goes, so ordinary traffic is slowed and never stopped.

The ordering tests drive a FAKE clock one interval at a time and count grants,
so they do not depend on how fast this machine schedules threads.
"""
import threading
import time

import pytest

import rate_gate


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _pause(cond, seconds):
    """Wait briefly in REAL time whatever the fake clock says, then re-check."""
    cond.wait(0.002)


class _Harness:
    """A gate on a fake clock, with waiters started one at a time."""

    def __init__(self, priority_run):
        self.clock = _Clock()
        self.gate = rate_gate.RateGate(1.0, priority_run=lambda: priority_run,
                                       clock=self.clock, pause=_pause)
        self.order = []
        self.threads = []
        self.gate.acquire()                 # takes the slot at t=0

    def waiter(self, name, priority):
        before = sum(self.gate.waiting())

        def run():
            self.gate.acquire(priority=priority)
            self.order.append(name)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.threads.append(t)
        deadline = time.monotonic() + 5
        while sum(self.gate.waiting()) == before:
            assert time.monotonic() < deadline, f"{name} never queued"
            time.sleep(0.001)

    def drain(self):
        """Advance the clock one interval per grant until everyone has gone."""
        total = len(self.threads)
        for done in range(1, total + 1):
            self.clock.now += 1.0
            deadline = time.monotonic() + 5
            while len(self.order) < done:
                assert time.monotonic() < deadline, f"grant {done} never came"
                time.sleep(0.001)
            time.sleep(0.01)                # nobody else may go on this tick
            assert len(self.order) == done, "two grants in one interval"
        return self.order


def test_priority_requests_go_before_ordinary_ones_already_waiting():
    h = _Harness(priority_run=4)
    for n in ("n1", "n2", "n3"):
        h.waiter(n, priority=False)
    for p in ("p1", "p2"):
        h.waiter(p, priority=True)
    assert h.drain() == ["p1", "p2", "n1", "n2", "n3"]


def test_an_ordinary_request_goes_after_each_run_of_priority_ones():
    """Slowed, never stopped: with a run of 2, every third slot is ordinary."""
    h = _Harness(priority_run=2)
    for n in ("n1", "n2"):
        h.waiter(n, priority=False)
    for p in ("p1", "p2", "p3", "p4", "p5"):
        h.waiter(p, priority=True)
    assert h.drain() == ["p1", "p2", "n1", "p3", "p4", "n2", "p5"]


def test_each_lane_is_first_come_first_served():
    h = _Harness(priority_run=4)
    for n in ("n1", "n2", "n3", "n4"):
        h.waiter(n, priority=False)
    assert h.drain() == ["n1", "n2", "n3", "n4"]


def test_a_run_of_zero_switches_the_lane_off():
    """Exactly the order before the lane existed: arrival order, whoever asks."""
    h = _Harness(priority_run=0)
    h.waiter("n1", priority=False)
    h.waiter("p1", priority=True)
    h.waiter("n2", priority=False)
    h.waiter("p2", priority=True)
    assert h.drain() == ["n1", "p1", "n2", "p2"]


def test_the_priority_run_is_counted_only_while_an_ordinary_request_waits():
    """Priority requests that delayed nobody do not use up the run."""
    h = _Harness(priority_run=2)
    for p in ("p1", "p2", "p3"):
        h.waiter(p, priority=True)
    assert h.drain() == ["p1", "p2", "p3"]
    h.threads.clear()
    h.order.clear()
    h.waiter("n1", priority=False)
    h.waiter("p4", priority=True)
    h.waiter("p5", priority=True)
    assert h.drain() == ["p4", "p5", "n1"]


@pytest.mark.parametrize("bad", [None, "4", -1, float("nan"), True, 2.5])
def test_an_unusable_run_setting_reads_as_the_built_in_value(bad):
    gate = rate_gate.RateGate(1.0, priority_run=lambda: bad)
    assert gate.run_length() == rate_gate.DEFAULT_PRIORITY_RUN


def test_a_setting_that_cannot_be_read_never_stops_a_request():
    def broken():
        raise RuntimeError("config unreadable")

    gate = rate_gate.RateGate(0.0, priority_run=broken)
    gate.acquire(priority=True)
    gate.acquire()
    assert gate.run_length() == rate_gate.DEFAULT_PRIORITY_RUN


def test_requests_are_spaced_one_interval_apart_in_real_time():
    gate = rate_gate.RateGate(0.02)
    start = time.monotonic()
    threads = [threading.Thread(target=gate.acquire, kwargs={"priority": i % 2 == 0})
               for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - start >= 0.02 * 4 * 0.7


def test_the_first_request_does_not_wait():
    gate = rate_gate.RateGate(5.0)
    start = time.monotonic()
    gate.acquire()
    assert time.monotonic() - start < 1.0


def test_granted_counts_say_how_each_lane_was_used():
    h = _Harness(priority_run=4)
    h.waiter("n1", priority=False)
    h.waiter("p1", priority=True)
    h.drain()
    # The harness's own first acquire is an ordinary one.
    assert h.gate.granted() == {"priority": 1, "normal": 2}
