"""The xfeed loop on a fake clock and a fake sleep (asyncio.sleep is never patched).

``Harness`` drives the loop one tick at a time: its ``_sleep`` advances the clock by the
requested seconds, lets pending tasks run, then calls the script for that tick; the
script raises CancelledError to end the run. Cycles run inline (``_to_thread`` swapped).
"""
import asyncio

import pytest

from services import _degrade
from services.xfeed_svc import runtime, scheduler


class FakePoller:
    def __init__(self, interval=60):
        self.interval = interval
        self.started = 0
        self.block = None          # an asyncio.Event a cycle waits on, when set
        self.fail = False
        self.on_change_calls = 0

    def interval_s(self):
        return self.interval

    def run_cycle(self, on_change):
        self.started += 1
        on_change()
        self.on_change_calls += 1
        if self.fail:
            raise RuntimeError("secret-handle exploded")


class Rt:
    def __init__(self, poller):
        self.poller = poller


@pytest.fixture
def env(monkeypatch):
    poller = FakePoller()
    rt = Rt(poller)
    runtime.set_for_tests(rt)
    published = []
    monkeypatch.setattr(scheduler.handlers, "publish_all", lambda bus, r: published.append((bus, r)))
    monkeypatch.setattr(scheduler.config, "load", lambda: {"poller": {"tick_s": 5}})
    clock = {"t": 1000.0}
    monkeypatch.setattr(scheduler, "_monotonic", lambda: clock["t"])
    holder = {}

    async def to_thread(fn, *args):
        if fn == poller.run_cycle:
            holder["entered"] = holder.get("entered", 0) + 1
            gate = holder.get("gate")
            if gate is not None:
                await gate.wait()
        return fn(*args)

    monkeypatch.setattr(scheduler, "_to_thread", to_thread)
    yield poller, rt, published, clock, holder
    runtime.set_for_tests(None)


def drive(clock, script):
    """Run the loop; ``script(n)`` is called after tick n's sleep. Returns when it raises Cancelled."""
    n = {"i": 0}

    async def fake_sleep(s):
        await asyncio.sleep(0)      # real yield: lets cycle tasks run
        await asyncio.sleep(0)
        clock["t"] += s
        n["i"] += 1
        script(n["i"])

    async def main():
        scheduler._sleep = fake_sleep
        try:
            await scheduler.loop("BUS")
        except asyncio.CancelledError:
            pass

    saved = scheduler._sleep
    try:
        asyncio.run(main())
    finally:
        scheduler._sleep = saved


def stop():
    raise asyncio.CancelledError


def test_publishes_views_at_start(env):
    poller, rt, published, clock, _ = env
    drive(clock, lambda i: stop())
    assert published[0] == ("BUS", rt)


def test_first_cycle_runs_immediately(env):
    poller, rt, published, clock, _ = env
    drive(clock, lambda i: stop())
    assert poller.started == 1


def test_next_cycle_waits_interval_from_end(env):
    poller, rt, published, clock, _ = env     # interval 60, tick 5

    def script(i):
        if i == 11:
            assert poller.started == 1       # t=1055: not yet
        if i == 13:
            stop()

    drive(clock, script)
    assert poller.started == 2


def test_running_cycle_is_not_doubled(env):
    poller, rt, published, clock, holder = env
    seen = {}

    async def main():
        gate = asyncio.Event()
        holder["gate"] = gate
        n = {"i": 0}

        async def fake_sleep(s):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            clock["t"] += s + 100        # far past the interval every tick
            n["i"] += 1
            if n["i"] == 4:
                seen["while_running"] = holder["entered"]
                gate.set()
            if n["i"] == 6:
                raise asyncio.CancelledError

        scheduler._sleep = fake_sleep
        try:
            await scheduler.loop("BUS")
        except asyncio.CancelledError:
            pass

    saved = scheduler._sleep
    try:
        asyncio.run(main())
    finally:
        scheduler._sleep = saved
    assert seen["while_running"] == 1     # the gate held the first cycle through 3 more ticks
    assert poller.started >= 2            # once it ended, the next one started


def test_config_interval_change_applies_without_restart(env):
    poller, rt, published, clock, _ = env

    def script(i):
        if i == 3:
            poller.interval = 10           # t=1015 >= 1000+10 -> due now
        if i == 5:
            stop()

    drive(clock, script)
    assert poller.started == 2


def test_a_failing_cycle_is_counted_and_the_loop_survives(env):
    poller, rt, published, clock, _ = env
    poller.fail = True
    _degrade.reset()
    drive(clock, lambda i: stop() if i == 14 else None)
    assert poller.started == 2             # kept going after the fault
    assert _degrade.counts()[scheduler.AREA] == 2


def test_a_failing_first_publish_does_not_kill_the_loop(env, monkeypatch):
    poller, rt, published, clock, _ = env

    def boom(bus, r):
        raise RuntimeError("bus down")

    monkeypatch.setattr(scheduler.handlers, "publish_all", boom)
    _degrade.reset()
    drive(clock, lambda i: stop())
    assert poller.started == 1
    assert _degrade.counts()[scheduler.AREA] == 2    # the start publish and the cycle's on_change
