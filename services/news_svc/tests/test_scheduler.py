import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import pytest

from services.news_svc import scheduler

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

        def fake_poll(bus):
            self.polls.append(self.clock)
            if poll is not None:
                return poll(bus, len(self.polls))
            return {"ok": True}

        async def fake_sleep(s):
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

        monkeypatch.setattr(scheduler.compute, "poll_now", fake_poll)
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


def test_cancellation_during_a_poll_propagates(monkeypatch):
    def cancel(bus, n):
        raise asyncio.CancelledError

    h = _Harness(monkeypatch, passes=99, poll=cancel)
    h.run()
    assert h.sleeps == []
