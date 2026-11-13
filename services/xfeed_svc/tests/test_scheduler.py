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
        self.notify = True         # call on_change in the cycle
        self.duration = 0          # fake seconds a cycle takes
        self.clock = None
        self.stopped = 0
        self.resumed = 0
        self.order = None          # shared list: "publish" / "cycle" events

    def stop(self):
        self.stopped += 1

    def resume(self):
        self.resumed += 1

    def interval_s(self):
        return self.interval

    def run_cycle(self, on_change):
        self.started += 1
        if self.order is not None:
            self.order.append("cycle")
        if self.clock is not None:
            self.clock["t"] += self.duration
        if self.notify:
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


def test_publishes_views_at_start(env, monkeypatch):
    poller, rt, published, clock, _ = env
    order = []
    poller.order = order
    poller.notify = False      # the cycle publishes nothing, so only the start publish can
    monkeypatch.setattr(scheduler.handlers, "publish_all",
                        lambda bus, r: (order.append("publish"), published.append((bus, r))))
    drive(clock, lambda i: stop())
    assert published == [("BUS", rt)]
    assert order == ["publish", "cycle"]      # views are out before the first cycle starts


def test_first_cycle_runs_immediately(env):
    poller, rt, published, clock, _ = env
    drive(clock, lambda i: stop())
    assert poller.started == 1


def test_next_cycle_waits_interval_from_end(env):
    poller, rt, published, clock, _ = env     # interval 60, tick 5
    poller.clock = clock
    poller.duration = 20                      # cycle 1: starts t=1000, ends t=1020
    starts = []
    orig = poller.run_cycle

    def recording(on_change):
        starts.append(clock["t"])
        return orig(on_change)

    poller.run_cycle = recording

    def script(i):
        if i == 11:                           # t=1075: past start+60, short of end+60 (1080)
            assert len(starts) == 1
        if i == 15:
            stop()

    drive(clock, script)
    assert len(starts) == 2
    assert starts[1] >= 1020 + 60             # from the END, not the start (would be ~1060)


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


@pytest.mark.parametrize("bad", [0, -3, "soon", None, True, float("nan")])
def test_a_bad_tick_s_never_busy_loops_or_escapes(env, monkeypatch, bad):
    poller, rt, published, clock, _ = env
    monkeypatch.setattr(scheduler.config, "load", lambda: {"poller": {"tick_s": bad}})
    slept = []

    def script(i):
        if i == 3:
            stop()

    async def main():
        async def fake_sleep(s):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            slept.append(s)
            clock["t"] += s
            script(len(slept))

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
    assert slept and all(s >= 1.0 for s in slept)


def test_cancelling_the_loop_cancels_a_live_cycle_and_stops_the_poller(env):
    poller, rt, published, clock, holder = env
    seen = {}

    async def main():
        gate = asyncio.Event()      # never set: the cycle stays live
        holder["gate"] = gate
        n = {"i": 0}

        async def fake_sleep(s):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            n["i"] += 1
            if n["i"] == 3:
                seen["entered"] = holder["entered"]
                raise asyncio.CancelledError

        scheduler._sleep = fake_sleep
        task = asyncio.ensure_future(scheduler.loop("BUS"))
        await asyncio.wait({task}, timeout=2)      # returns promptly, not on the gate
        seen["done"] = task.done()
        seen["cancelled"] = task.cancelled()
        seen["stopped"] = poller.stopped
        seen["leftover"] = [t for t in asyncio.all_tasks()
                            if t is not asyncio.current_task() and not t.done()]

    saved = scheduler._sleep
    try:
        asyncio.run(main())
    finally:
        scheduler._sleep = saved
    assert seen["entered"] == 1
    assert seen["done"] and seen["cancelled"]
    assert seen["stopped"] == 1
    assert seen["leftover"] == []                  # the cycle task was cancelled and awaited
