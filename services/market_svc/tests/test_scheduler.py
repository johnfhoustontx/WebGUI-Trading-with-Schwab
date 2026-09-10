import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.market_svc import scheduler as sch

_CT = ZoneInfo("America/Chicago")


def test_poll_interval_values():
    # A macro dashboard updates tiles in place, so a slightly slower cadence is
    # imperceptible but roughly halves the Schwab /quotes volume (~24k → ~12k/day).
    # Weekend stays hard-throttled (futures closed → nothing ticks).
    assert sch.RTH_INTERVAL_SEC == 3
    assert sch.OFFHOURS_INTERVAL_SEC == 15
    assert sch.WEEKEND_INTERVAL_SEC == 60


def test_fast_cadence_during_rth():
    now = dt.datetime(2026, 7, 7, 10, 0, tzinfo=_CT)  # Tue 10:00 CT
    assert sch.poll_interval(now) == sch.RTH_INTERVAL_SEC


def test_slow_cadence_off_hours():
    now = dt.datetime(2026, 7, 7, 22, 0, tzinfo=_CT)  # Tue 22:00 CT
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC


def test_slow_cadence_on_weekend():
    # Saturday: the futures are CLOSED (they don't reopen until Sun 17:00 CT), so
    # nothing ticks — throttle harder than the normal off-hours pace.
    now = dt.datetime(2026, 7, 11, 10, 0, tzinfo=_CT)  # Sat
    assert sch.poll_interval(now) == sch.WEEKEND_INTERVAL_SEC


def test_slow_cadence_on_holiday():
    now = dt.datetime(2026, 7, 3, 10, 0, tzinfo=_CT)  # NYSE holiday
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC


_DAY = dt.date(2026, 9, 10)
_FP = ("fp-a",)


def test_the_first_poll_after_a_restart_writes_a_sentence():
    assert sch.summary_due(sch.SummaryGate(), _FP, now_mono=0.0, today=_DAY)


def test_nothing_to_summarize_never_calls():
    assert not sch.summary_due(sch.SummaryGate(), None, now_mono=0.0, today=_DAY)


def test_an_unchanged_reading_never_calls_however_long_it_waits():
    gate = sch.record_summary(sch.SummaryGate(), _FP, now_mono=0.0, today=_DAY)
    assert not sch.summary_due(gate, _FP, now_mono=10 * 3600.0, today=_DAY)


def test_a_change_waits_out_the_minimum_gap():
    gate = sch.record_summary(sch.SummaryGate(), _FP, now_mono=0.0, today=_DAY)
    soon = sch.SUMMARY_MIN_GAP_SEC - 1
    assert not sch.summary_due(gate, ("fp-b",), now_mono=soon, today=_DAY)
    assert sch.summary_due(gate, ("fp-b",), now_mono=sch.SUMMARY_MIN_GAP_SEC,
                           today=_DAY)


def test_the_daily_ceiling_holds_and_resets_at_the_date_change():
    gate = sch.SummaryGate()
    t = 0.0
    for i in range(sch.SUMMARY_DAILY_CAP):
        fp = (f"fp-{i}",)
        assert sch.summary_due(gate, fp, now_mono=t, today=_DAY), i
        gate = sch.record_summary(gate, fp, now_mono=t, today=_DAY)
        t += sch.SUMMARY_MIN_GAP_SEC
    assert not sch.summary_due(gate, ("fp-new",), now_mono=t, today=_DAY)
    tomorrow = _DAY + dt.timedelta(days=1)
    assert sch.summary_due(gate, ("fp-new",), now_mono=t, today=tomorrow)


def test_the_gate_constants_are_the_approved_design():
    assert sch.SUMMARY_MIN_GAP_SEC == 10 * 60
    assert sch.SUMMARY_DAILY_CAP == 30


def test_the_loop_is_gated_on_change_not_on_the_ticker_toggle():
    import inspect

    src = inspect.getsource(sch.loop)
    assert "summary_enabled" not in src
    assert "compute.read_summary_packet" in src
    assert "compute.summary_fingerprint(" in src
    assert "summary_due(" in src and "record_summary(" in src


def test_a_failed_summary_attempt_publishes_nothing(monkeypatch):
    """generate_summary returns None when the Claude call failed; the last good
    sentence must stay on screen rather than be blanked."""
    import asyncio
    published = []
    monkeypatch.setattr(sch.compute, "generate_summary", lambda packet: None)
    monkeypatch.setattr(sch.handlers, "publish_summary",
                        lambda bus, s: published.append(s))

    async def _go():
        return await sch._run_summary(asyncio.get_running_loop(), object(), {"x": 1})
    result = asyncio.run(_go())
    assert published == []
    assert result is False


def test_a_successful_summary_is_published(monkeypatch):
    import asyncio
    published = []
    out = {"narrative": "Fear builds; lean defensive.", "inputs": {"x": 1},
           "as_of": "2026-09-10T15:42:00+00:00"}
    monkeypatch.setattr(sch.compute, "generate_summary", lambda packet: out)
    monkeypatch.setattr(sch.handlers, "publish_summary",
                        lambda bus, s: published.append(s))

    async def _go():
        return await sch._run_summary(asyncio.get_running_loop(), object(), {"x": 1})
    result = asyncio.run(_go())
    assert published == [out]
    assert result is True


class _Stop(Exception):
    """Ends the otherwise-infinite loop from inside its sleep."""


def _drive_loop(monkeypatch, generate, ticks=40, step=60.0):
    """Run ``loop()`` for ``ticks`` polls with every seam faked: the packet and
    its fingerprint never change, each poll advances a fake monotonic clock by
    ``step`` seconds, and ``generate`` stands in for the Claude call."""
    import asyncio
    clock = {"t": 0.0, "n": 0}
    published = []
    monkeypatch.setattr(sch, "poll_interval", lambda now=None: 0)
    monkeypatch.setattr(sch.compute, "collect", lambda bus: {})
    monkeypatch.setattr(sch.handlers, "publish", lambda bus, payload: None)
    monkeypatch.setattr(sch.compute, "read_summary_packet", lambda bus: {"p": 1})
    monkeypatch.setattr(sch.compute, "summary_fingerprint", lambda packet: ("same",))
    monkeypatch.setattr(sch.compute, "generate_summary",
                        lambda packet: generate(clock["t"]))
    monkeypatch.setattr(sch.handlers, "publish_summary",
                        lambda bus, s: published.append(s))
    monkeypatch.setattr(sch.time, "monotonic", lambda: clock["t"])
    real_sleep = asyncio.sleep

    async def _inline(self, executor, fn, *args):
        return fn(*args)

    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", _inline)

    async def _sleep(_secs):
        clock["n"] += 1
        clock["t"] += step
        if clock["n"] > ticks:
            raise _Stop
        await real_sleep(0)         # let the background summary task run

    monkeypatch.setattr(sch.asyncio, "sleep", _sleep)
    with pytest.raises(_Stop):
        asyncio.run(sch.loop(object()))
    return published


def test_a_failed_attempt_is_retried_after_the_gap_when_the_readings_hold(monkeypatch):
    calls = []
    outcomes = iter([None, {"narrative": "ok", "inputs": {}, "as_of": ""}])

    def _generate(t):
        calls.append(t)
        return next(outcomes, {"narrative": "again", "inputs": {}, "as_of": ""})

    published = _drive_loop(monkeypatch, _generate)
    assert len(calls) == 2, calls              # failed once, retried once, then held
    assert calls[1] - calls[0] >= sch.SUMMARY_MIN_GAP_SEC
    assert [s["narrative"] for s in published] == ["ok"]


def test_unchanged_readings_are_written_once_and_then_left_alone(monkeypatch):
    calls = []

    def _generate(t):
        calls.append(t)
        return {"narrative": "ok", "inputs": {}, "as_of": ""}

    published = _drive_loop(monkeypatch, _generate)
    assert len(calls) == 1 and len(published) == 1


def test_loop_runs_summary_as_background_task():
    """The Claude summary (30s timeout, up to ~60s) must NOT be awaited inline in
    the 2s poll loop — it launches as a background task so the dashboard cadence
    never stalls once per ~40-min slot."""
    import inspect

    src = inspect.getsource(sch.loop)
    assert "create_task" in src
    # The blocking generate_summary is NOT directly awaited in the loop body.
    assert "await loop_.run_in_executor(None, compute.generate_summary" not in src


def test_poll_interval_throttles_deep_weekend():
    import datetime as dt
    # Saturday: futures closed all day -> slow throttle.
    sat = dt.datetime(2026, 7, 18, 10, 0, tzinfo=sch._CT)
    assert sch.poll_interval(sat) == sch.WEEKEND_INTERVAL_SEC
    # Sunday morning: still closed (futures reopen 17:00 CT Sunday).
    sun_am = dt.datetime(2026, 7, 19, 10, 0, tzinfo=sch._CT)
    assert sch.poll_interval(sun_am) == sch.WEEKEND_INTERVAL_SEC
    # Sunday evening after the futures reopen: back to the normal off-hours pace.
    sun_pm = dt.datetime(2026, 7, 19, 18, 0, tzinfo=sch._CT)
    assert sch.poll_interval(sun_pm) == sch.OFFHOURS_INTERVAL_SEC


def test_published_reads_a_finished_task_without_raising():
    import asyncio

    async def _go():
        loop = asyncio.get_running_loop()
        ok, failed, boom, cancelled = (loop.create_future() for _ in range(4))
        ok.set_result(True)
        failed.set_result(False)
        boom.set_exception(RuntimeError("x"))
        cancelled.cancel()
        return [sch._published(f) for f in (ok, failed, boom, cancelled)]

    assert asyncio.run(_go()) == [True, False, False, False]
