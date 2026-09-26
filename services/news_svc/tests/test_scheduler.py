import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import pytest

from services.news_svc import econ_calendar, scheduler

CT = ZoneInfo("America/Chicago")
CFG = {"collector": {"rth_poll_min": 5, "offhours_poll_min": 15, "weekend_poll_min": 60}}
RTH = dt.datetime(2026, 9, 24, 10, 0, tzinfo=CT)      # a Thursday, mid-session


def test_cadence_by_calendar():
    assert scheduler.poll_interval_s(dt.datetime(2026, 9, 24, 10, 0, tzinfo=CT), CFG) == 300
    assert scheduler.poll_interval_s(dt.datetime(2026, 9, 24, 18, 0, tzinfo=CT), CFG) == 900
    assert scheduler.poll_interval_s(dt.datetime(2026, 9, 26, 10, 0, tzinfo=CT), CFG) == 3600
    assert scheduler.poll_interval_s(dt.datetime(2026, 7, 3, 10, 0, tzinfo=CT), CFG) == 3600  # holiday


def test_loop_calls_heartbeat_every_pass():
    import ast, inspect
    src = inspect.getsource(scheduler.loop)
    assert "_heartbeat.tick()" in src


def test_heartbeat_is_inside_the_while_loop():
    """Mirrors services/tests/test_scaffold.py's per-service guard: a beat
    outside the loop reports one tick at startup and then a growing age."""
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(inspect.getsource(scheduler.loop))).body[0]
    beats = [c for w in ast.walk(fn) if isinstance(w, ast.While)
             for c in ast.walk(w)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
             and c.func.attr == "tick"
             and isinstance(c.func.value, ast.Name) and c.func.value.id == "_heartbeat"]
    assert beats


def test_an_aware_utc_now_is_read_in_central_time():
    # 15:30 UTC on a Thursday is 10:30 CT - regular hours.
    now = dt.datetime(2026, 9, 24, 15, 30, tzinfo=dt.timezone.utc)
    assert scheduler.poll_interval_s(now, CFG) == 300
    # 03:00 UTC Saturday is 22:00 CT Friday - off-hours, not the weekend.
    now = dt.datetime(2026, 9, 26, 3, 0, tzinfo=dt.timezone.utc)
    assert scheduler.poll_interval_s(now, CFG) == 900


def test_a_naive_now_is_treated_as_central():
    assert scheduler.poll_interval_s(dt.datetime(2026, 9, 24, 10, 0), CFG) == 300


# ── a bad config must never make the loop spin ─────────────────────────────────

def _default_s(key):
    from shared import news_config as nc
    return int(nc.DEFAULTS["collector"][key]) * 60


def test_the_interval_is_floored_at_sixty_seconds():
    cfg = {"collector": {"rth_poll_min": 0.25, "offhours_poll_min": 15,
                         "weekend_poll_min": 60}}
    assert scheduler.poll_interval_s(RTH, cfg) == 60


@pytest.mark.parametrize("bad", [0, -5, "five", None, True, False, float("nan"),
                                 float("inf"), float("-inf"), [5], {"m": 5}])
def test_an_unusable_value_falls_back_to_the_built_in_default(bad):
    cfg = {"collector": {"rth_poll_min": bad, "offhours_poll_min": 15,
                         "weekend_poll_min": 60}}
    assert scheduler.poll_interval_s(RTH, cfg) == _default_s("rth_poll_min")


def test_a_missing_key_falls_back_to_the_built_in_default():
    cfg = {"collector": {"offhours_poll_min": 15, "weekend_poll_min": 60}}
    assert scheduler.poll_interval_s(RTH, cfg) == _default_s("rth_poll_min")


@pytest.mark.parametrize("cfg", [{}, {"collector": None}, {"collector": "x"}, None])
def test_a_missing_or_malformed_collector_section_falls_back(cfg):
    assert scheduler.poll_interval_s(RTH, cfg) == _default_s("rth_poll_min")


def test_a_bad_value_warns_once_not_on_every_pass(monkeypatch, caplog):
    monkeypatch.setattr(scheduler, "_warned", set())
    cfg = {"collector": {"rth_poll_min": -1}}
    with caplog.at_level(logging.WARNING, logger="news_svc.scheduler"):
        for _ in range(5):
            scheduler.poll_interval_s(RTH, cfg)
    assert sum("rth_poll_min" in r.getMessage() for r in caplog.records) == 1


def test_a_numeric_string_is_not_read_as_minutes():
    """A wrong TYPE is refused, not coerced: "1" is not 1."""
    cfg = {"collector": {"rth_poll_min": "1"}}
    assert scheduler.poll_interval_s(RTH, cfg) == _default_s("rth_poll_min")


# 1e307 is finite but * 60 overflows to inf (int(inf) raises); 10**400 makes
# math.isfinite itself raise OverflowError. Neither may kill the loop.
@pytest.mark.parametrize("huge", [1e307, 10 ** 400, -(10 ** 400), 10_081, 10_080.5,
                                  float(10 ** 20)],
                         ids=["1e307", "10**400", "-10**400", "10081", "10080.5", "1e20"])
def test_a_huge_value_falls_back_to_the_built_in_default(huge):
    cfg = {"collector": {"rth_poll_min": huge, "offhours_poll_min": 15,
                         "weekend_poll_min": 60}}
    assert scheduler.poll_interval_s(RTH, cfg) == _default_s("rth_poll_min")


def test_one_week_is_the_largest_accepted_interval():
    cfg = {"collector": {"rth_poll_min": 10_080, "offhours_poll_min": 15,
                         "weekend_poll_min": 60}}
    assert scheduler.poll_interval_s(RTH, cfg) == 10_080 * 60


def test_a_huge_value_warns_once_not_on_every_pass(monkeypatch, caplog):
    monkeypatch.setattr(scheduler, "_warned", set())
    cfg = {"collector": {"rth_poll_min": 10 ** 400}}
    with caplog.at_level(logging.WARNING, logger="news_svc.scheduler"):
        for _ in range(5):
            scheduler.poll_interval_s(RTH, cfg)
    assert sum("rth_poll_min" in r.getMessage() for r in caplog.records) == 1


class _Explodes(float):
    """A float (so it passes the type check) whose every comparison raises -
    validation must not propagate it."""
    def __le__(self, other):
        raise RuntimeError("no")
    __lt__ = __gt__ = __ge__ = __le__


def test_a_value_that_raises_during_validation_falls_back():
    cfg = {"collector": {"rth_poll_min": _Explodes(5.0)}}
    assert scheduler.poll_interval_s(RTH, cfg) == _default_s("rth_poll_min")


# ── the loop ──────────────────────────────────────────────────────────────────

class _Harness:
    """A fake clock + sleep: each sleep advances the clock, and the loop is
    cancelled after ``passes`` sleeps (the CancelledError must propagate)."""

    def __init__(self, monkeypatch, passes, cfg=None, poll=None, on_sleep=None):
        self.clock = 0.0
        self.sleeps = []
        self.polls = []
        self.ticks = 0
        self.loads = 0
        self.cfg = cfg if cfg is not None else {"collector": dict(CFG["collector"])}
        self.passes = passes
        self.on_sleep = on_sleep
        self.cal = []
        self.watch = []

        def fake_poll(bus):
            self.polls.append(self.clock)
            if poll is not None:
                return poll(bus, len(self.polls))
            return {"ok": True}

        async def fake_sleep(s):
            # The branches are background tasks now: yield once so the ones
            # this pass launched run (inline, via ``_to_thread`` below) at the
            # pass's clock time, before the clock moves.
            await asyncio.sleep(0)
            self.sleeps.append(s)
            self.clock += s
            if self.on_sleep is not None:
                self.on_sleep(self)
            if len(self.sleeps) >= self.passes:
                raise asyncio.CancelledError

        def fake_load():
            self.loads += 1
            return self.cfg

        def fake_tick():
            self.ticks += 1

        async def inline(fn, *args):
            return fn(*args)

        monkeypatch.setattr(scheduler.compute, "poll_now", fake_poll)
        monkeypatch.setattr(scheduler, "_to_thread", inline)
        monkeypatch.setattr(econ_calendar, "refresh_now",
                            lambda bus: self.cal.append(self.clock) or {"ok": True})
        monkeypatch.setattr(econ_calendar, "watch_now",
                            lambda bus: self.watch.append(self.clock) or {"due": []})
        monkeypatch.setattr(scheduler, "_sleep", fake_sleep)
        monkeypatch.setattr(scheduler, "_monotonic", lambda: self.clock)
        monkeypatch.setattr(scheduler, "_utcnow", lambda: RTH)
        monkeypatch.setattr(scheduler.nc, "load", fake_load)
        monkeypatch.setattr(scheduler._heartbeat, "tick", fake_tick)

    def run(self):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(scheduler.loop(object()))


def test_the_first_poll_runs_at_once(monkeypatch):
    h = _Harness(monkeypatch, passes=1)
    h.run()
    assert h.polls == [0.0]


def test_the_loop_polls_on_the_rth_cadence(monkeypatch):
    h = _Harness(monkeypatch, passes=int(600 / scheduler.TICK_S) + 1)
    h.run()
    assert h.polls == [0.0, 300.0, 600.0]
    assert all(s <= scheduler.TICK_S for s in h.sleeps)


def test_the_heartbeat_ticks_on_every_pass(monkeypatch):
    h = _Harness(monkeypatch, passes=7)
    h.run()
    assert h.ticks == 7


def test_config_is_read_every_pass_so_an_interval_edit_applies(monkeypatch):
    def shorten(h):
        if h.clock == 60.0:
            h.cfg = {"collector": dict(CFG["collector"], rth_poll_min=1)}

    h = _Harness(monkeypatch, passes=int(180 / scheduler.TICK_S) + 1, on_sleep=shorten)
    h.run()
    assert h.loads >= h.passes            # re-read on each pass, never cached here
    assert h.polls == [0.0, 60.0, 120.0, 180.0]


def test_a_failed_poll_is_logged_and_the_loop_continues(monkeypatch, caplog):
    def boom(bus, n):
        if n == 1:
            raise RuntimeError("feed exploded")
        return {"ok": True}

    h = _Harness(monkeypatch, passes=int(300 / scheduler.TICK_S) + 1, poll=boom)
    with caplog.at_level(logging.ERROR, logger="news_svc.scheduler"):
        h.run()
    assert h.polls == [0.0, 300.0]
    assert any("feed exploded" in (r.exc_text or "") or r.exc_info
               for r in caplog.records)


def test_a_busy_poll_does_not_break_the_loop(monkeypatch):
    h = _Harness(monkeypatch, passes=int(300 / scheduler.TICK_S) + 1,
                 poll=lambda bus, n: {"skipped": "busy"})
    h.run()
    assert h.polls == [0.0, 300.0]


def test_a_slow_poll_is_followed_by_a_full_interval_of_rest(monkeypatch):
    """A 90 s poll on a 60 s cadence: the interval is measured END-to-start, so
    the next poll never follows the last one back-to-back."""
    cfg = {"collector": dict(CFG["collector"], rth_poll_min=1)}
    ends = []
    box = {}

    def slow(bus, n):
        box["h"].clock += 90.0
        ends.append(box["h"].clock)
        return {"ok": True}

    h = _Harness(monkeypatch, passes=20, cfg=cfg, poll=slow)
    box["h"] = h
    h.run()
    assert len(h.polls) >= 3
    for end, nxt in zip(ends, h.polls[1:]):
        assert nxt - end >= 60
    assert h.polls[:3] == [0.0, 150.0, 300.0]


def test_a_failed_slow_poll_still_rests_a_full_interval(monkeypatch):
    cfg = {"collector": dict(CFG["collector"], rth_poll_min=1)}
    ends = []
    box = {}

    def slow_boom(bus, n):
        box["h"].clock += 90.0
        ends.append(box["h"].clock)
        raise RuntimeError("slow and broken")

    h = _Harness(monkeypatch, passes=20, cfg=cfg, poll=slow_boom)
    box["h"] = h
    h.run()
    assert len(h.polls) >= 3
    for end, nxt in zip(ends, h.polls[1:]):
        assert nxt - end >= 60


def test_a_huge_config_value_does_not_kill_the_loop(monkeypatch):
    cfg = {"collector": dict(CFG["collector"], rth_poll_min=10 ** 400)}
    h = _Harness(monkeypatch, passes=int(_default_s("rth_poll_min") / scheduler.TICK_S) + 1,
                 cfg=cfg)
    h.run()
    assert h.polls == [0.0, float(_default_s("rth_poll_min"))]


def test_cancellation_during_a_poll_propagates(monkeypatch, caplog):
    """The poll is a background task now, so the loop no longer awaits it: a
    CancelledError inside the poll ends THAT task cancelled - it is neither
    swallowed as a failed cycle nor stamped - and cancelling the loop itself
    (the harness does, from its sleep) still propagates out of ``loop``."""
    def cancel(bus, n):
        raise asyncio.CancelledError

    h = _Harness(monkeypatch, passes=3, poll=cancel)
    with caplog.at_level(logging.ERROR, logger="news_svc.scheduler"):
        h.run()
    assert not any("cycle failed" in r.getMessage() for r in caplog.records)
    assert h.polls == [0.0]           # ended (stamped), so not retried until the cadence
    assert len(h.cal) == len(h.watch) == 3   # the other branches were untouched


# ── a value too large to repr, and a config bug that raises ────────────────────

# A TOML hex literal is an int with no digit limit, but repr() of an int over
# sys.get_int_max_str_digits() (4300) decimal digits raises ValueError.
_UNREPRABLE = int("F" * 5000, 16)


def test_the_unreprable_fixture_really_cannot_be_repred():
    with pytest.raises(ValueError):
        repr(_UNREPRABLE)


def test_a_value_too_large_to_repr_falls_back_to_the_built_in_default(monkeypatch, caplog):
    monkeypatch.setattr(scheduler, "_warned", set())
    cfg = {"collector": {"rth_poll_min": _UNREPRABLE, "offhours_poll_min": 15,
                         "weekend_poll_min": 60}}
    with caplog.at_level(logging.WARNING, logger="news_svc.scheduler"):
        for _ in range(5):
            assert scheduler.poll_interval_s(RTH, cfg) == _default_s("rth_poll_min")
    msgs = [r.getMessage() for r in caplog.records if "rth_poll_min" in r.getMessage()]
    assert len(msgs) == 1                     # warned once, and the message rendered
    assert "too large" in msgs[0]


def test_a_value_too_large_to_repr_does_not_kill_the_loop(monkeypatch):
    monkeypatch.setattr(scheduler, "_warned", set())
    cfg = {"collector": dict(CFG["collector"], rth_poll_min=_UNREPRABLE)}
    h = _Harness(monkeypatch, passes=int(_default_s("rth_poll_min") / scheduler.TICK_S) + 1,
                 cfg=cfg)
    h.run()
    assert h.polls == [0.0, float(_default_s("rth_poll_min"))]


def test_a_raising_interval_computation_falls_back_and_the_loop_stays_up(monkeypatch, caplog):
    def broken(now, cfg):
        raise RuntimeError("config bug")

    h = _Harness(monkeypatch, passes=int(2 * scheduler.FALLBACK_INTERVAL_S / scheduler.TICK_S) + 1)
    monkeypatch.setattr(scheduler, "poll_interval_s", broken)
    with caplog.at_level(logging.ERROR, logger="news_svc.scheduler"):
        h.run()
    fb = float(scheduler.FALLBACK_INTERVAL_S)
    assert h.polls == [0.0, fb, 2 * fb]
    assert h.ticks == h.passes                # the heartbeat still beat every pass
    failures = [r for r in caplog.records if "interval" in r.getMessage()]
    assert len(failures) == 1                 # logged once, not every 30 s
    assert failures[0].exc_info               # with the traceback


def test_the_fallback_interval_is_the_built_in_rth_default():
    assert scheduler.FALLBACK_INTERVAL_S == _default_s("rth_poll_min")


def test_a_raising_config_load_falls_back_and_the_loop_stays_up(monkeypatch):
    h = _Harness(monkeypatch, passes=int(scheduler.FALLBACK_INTERVAL_S / scheduler.TICK_S) + 1)

    def broken_load():
        raise OSError("disk gone")

    monkeypatch.setattr(scheduler.nc, "load", broken_load)
    h.run()
    assert h.polls == [0.0, float(scheduler.FALLBACK_INTERVAL_S)]


def test_the_failure_is_logged_again_after_a_recovery(monkeypatch, caplog):
    state = {"broken": True}
    real = scheduler.poll_interval_s

    def flaky(now, cfg):
        if state["broken"]:
            raise RuntimeError("config bug")
        return real(now, cfg)

    def toggle(h):
        # broken for the first pass, healthy for the second, broken after
        state["broken"] = len(h.sleeps) != 1

    h = _Harness(monkeypatch, passes=4, on_sleep=toggle)
    monkeypatch.setattr(scheduler, "poll_interval_s", flaky)
    with caplog.at_level(logging.ERROR, logger="news_svc.scheduler"):
        h.run()
    assert sum("interval" in r.getMessage() for r in caplog.records) == 2


# ── the branches (T17): feeds, calendar, watch as keyed background tasks ───────

class _Cmd:
    def __init__(self, type):
        self.type = type


def test_a_slow_feed_poll_does_not_delay_the_release_watch(monkeypatch):
    """The feed poll blocks (on a real executor thread) for the whole run; the
    release watch still runs on EVERY tick, and the poll is never doubled."""
    import threading

    release = threading.Event()
    polls, watches = [], []

    def slow_poll(bus):
        polls.append(1)
        release.wait(5)
        return {"ok": True}

    passes = {"n": 0}

    async def fake_sleep(s):
        passes["n"] += 1
        for _ in range(200):               # let this pass's watch thread land
            if len(watches) >= passes["n"]:
                break
            await asyncio.sleep(0.005)
        if passes["n"] >= 4:
            release.set()                  # so asyncio.run's executor shutdown returns
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.compute, "poll_now", slow_poll)
    monkeypatch.setattr(econ_calendar, "refresh_now", lambda bus: {"ok": True})
    monkeypatch.setattr(econ_calendar, "watch_now",
                        lambda bus: watches.append(1) or {"due": []})
    monkeypatch.setattr(scheduler, "_sleep", fake_sleep)
    monkeypatch.setattr(scheduler, "_utcnow", lambda: RTH)
    monkeypatch.setattr(scheduler.nc, "load", lambda: CFG)
    monkeypatch.setattr(scheduler._heartbeat, "tick", lambda: None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.loop(object()))
    assert polls == [1]                    # still running: skipped, never doubled
    assert len(watches) == 4               # one per tick, none held back by the poll


def test_a_branch_still_running_is_skipped_not_doubled():
    class _Task:
        def __init__(self, done):
            self._done = done

        def done(self):
            return self._done

    made = []

    def make():
        made.append(1)
        return "coro"

    running = {"calendar": _Task(done=False)}
    assert scheduler.launch(running, "calendar", make, lambda c: _Task(False)) is False
    assert made == []                      # no coroutine built, so none left unawaited
    running["calendar"] = _Task(done=True)
    assert scheduler.launch(running, "calendar", make, lambda c: _Task(False)) is True
    assert made == [1] and running["calendar"].done() is False
    # A key never seen launches; another key's running task does not block it.
    assert scheduler.launch(running, "watch", make, lambda c: _Task(False)) is True


def test_a_slow_calendar_branch_is_skipped_on_the_next_tick(monkeypatch):
    """End to end: a calendar refresh that outlives a tick is not relaunched
    beside itself; the feed poll and the watch are unaffected."""
    import threading

    release = threading.Event()
    cals, watches = [], []

    def slow_cal(bus):
        cals.append(1)
        release.wait(5)
        return {"ok": True}

    passes = {"n": 0}

    async def fake_sleep(s):
        passes["n"] += 1
        for _ in range(200):
            if len(watches) >= passes["n"]:
                break
            await asyncio.sleep(0.005)
        if passes["n"] >= 3:
            release.set()
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.compute, "poll_now", lambda bus: {"ok": True})
    monkeypatch.setattr(econ_calendar, "refresh_now", slow_cal)
    monkeypatch.setattr(econ_calendar, "watch_now",
                        lambda bus: watches.append(1) or {"due": []})
    monkeypatch.setattr(scheduler, "_sleep", fake_sleep)
    monkeypatch.setattr(scheduler, "_utcnow", lambda: RTH)
    monkeypatch.setattr(scheduler.nc, "load", lambda: CFG)
    monkeypatch.setattr(scheduler._heartbeat, "tick", lambda: None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.loop(object()))
    assert cals == [1]
    assert len(watches) == 3


def test_calendar_branch_runs_at_start_then_every_refresh_min(monkeypatch):
    """The scheduler launches the calendar at start and on EVERY tick; the
    per-source ``refresh_min`` is ``econ_calendar.refresh``'s to decide
    (pinned by test_econ_calendar's
    ``test_a_source_is_refetched_only_after_its_refresh_min``), so a tick with
    nothing due fetches nothing. Same for the watch."""
    h = _Harness(monkeypatch, passes=int(300 / scheduler.TICK_S) + 1)
    h.run()
    ticks = [i * float(scheduler.TICK_S) for i in range(h.passes)]
    assert h.cal == ticks
    assert h.watch == ticks
    assert h.polls == [0.0, 300.0]         # the feeds keep the v1 cadence


def test_a_failing_calendar_branch_kills_neither_the_feeds_nor_the_watch(monkeypatch, caplog):
    h = _Harness(monkeypatch, passes=int(300 / scheduler.TICK_S) + 1)

    def boom(bus):
        h.cal.append(h.clock)
        raise RuntimeError("calendar exploded")

    monkeypatch.setattr(econ_calendar, "refresh_now", boom)
    with caplog.at_level(logging.ERROR, logger="news_svc.scheduler"):
        h.run()
    assert len(h.cal) == h.passes          # retried every tick
    assert len(h.watch) == h.passes
    assert h.polls == [0.0, 300.0]
    assert any("calendar cycle failed" in r.getMessage() and r.exc_info
               for r in caplog.records)


def test_news_refresh_runs_the_calendar_then_the_feeds(monkeypatch):
    from services.news_svc import compute, handlers

    order = []
    monkeypatch.setattr(econ_calendar, "refresh_now", lambda bus: order.append("cal"))
    monkeypatch.setattr(compute, "poll_now", lambda bus: order.append("feeds"))
    handlers.handle_command(object(), _Cmd("news_refresh"))
    assert order == ["cal", "feeds"]


def test_news_refresh_still_polls_the_feeds_when_the_calendar_fails(monkeypatch):
    from services.news_svc import compute, handlers

    order = []

    def boom(bus):
        raise RuntimeError("calendar exploded")

    monkeypatch.setattr(econ_calendar, "refresh_now", boom)
    monkeypatch.setattr(compute, "poll_now", lambda bus: order.append("feeds"))
    handlers.handle_command(object(), _Cmd("news_refresh"))
    assert order == ["feeds"]


def test_news_refresh_while_the_scheduler_refreshes_is_a_quiet_skip(monkeypatch):
    """A click racing the scheduler's calendar branch gets ``busy`` from
    ``refresh_now``'s lock - no second cycle - and the feeds still run."""
    from services.news_svc import compute, handlers

    order = []
    monkeypatch.setattr(econ_calendar, "refresh_now",
                        lambda bus: order.append("cal") or {"skipped": "busy"})
    monkeypatch.setattr(compute, "poll_now", lambda bus: order.append("feeds"))
    handlers.handle_command(object(), _Cmd("news_refresh"))
    assert order == ["cal", "feeds"]


def test_heartbeat_still_beats_inside_the_while():
    """services/tests/test_scaffold.py's per-service guard covers news_svc too;
    this pins that the branch rework kept the beat INSIDE the ``while`` and
    before any branch is launched (a beat after an ``await`` on a branch would
    age with it)."""
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(inspect.getsource(scheduler.loop))).body[0]
    loops = [w for w in ast.walk(fn) if isinstance(w, ast.While)]
    assert len(loops) == 1
    first = loops[0].body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert ast.unparse(first.value) == "_heartbeat.tick()"
