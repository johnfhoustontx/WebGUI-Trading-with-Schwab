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


def _run_once(monkeypatch, result):
    """One ``_run_summary`` over a faked ``generate_summary`` returning
    ``result``: ``(outcome, what was published)``."""
    import asyncio
    published = []
    monkeypatch.setattr(sch.compute, "generate_summary", lambda packet: result())
    monkeypatch.setattr(sch.handlers, "publish_summary",
                        lambda bus, s: published.append(s))

    async def _go():
        return await sch._run_summary(asyncio.get_running_loop(), object(), {"x": 1})
    return asyncio.run(_go()), published


def test_a_failed_summary_attempt_publishes_nothing(monkeypatch):
    """generate_summary returns None when the Claude call failed; the last good
    sentence must stay on screen rather than be blanked."""
    outcome, published = _run_once(monkeypatch, lambda: None)
    assert published == []
    assert outcome == "failed"


def test_a_withheld_reply_publishes_nothing(monkeypatch):
    """generate_summary returns WITHHELD when the reply was checked and refused;
    the last good sentence stays, exactly as after a failure."""
    outcome, published = _run_once(monkeypatch, lambda: sch.compute.WITHHELD)
    assert published == []
    assert outcome == "withheld"


def test_a_successful_summary_is_published(monkeypatch):
    out = {"narrative": "Fear builds; lean defensive.", "inputs": {"x": 1},
           "as_of": "2026-09-10T15:42:00+00:00"}
    outcome, published = _run_once(monkeypatch, lambda: out)
    assert published == [out]
    assert outcome == "published"


class _Stop(Exception):
    """Ends the otherwise-infinite loop from inside its sleep."""


def _drive_loop(monkeypatch, generate, ticks=40, step=60.0, packet_at=None):
    """Run ``loop()`` for ``ticks`` polls with every seam faked: each poll
    advances a fake monotonic clock by ``step`` seconds, and ``generate`` stands
    in for the Claude call. Without ``packet_at`` the packet and its fingerprint
    never change; with it, ``packet_at(t)`` is the packet at clock ``t`` and the
    REAL fingerprint and comparison decide what changed."""
    import asyncio
    clock = {"t": 0.0, "n": 0}
    published = []
    monkeypatch.setattr(sch, "poll_interval", lambda now=None: 0)
    monkeypatch.setattr(sch.compute, "collect", lambda bus: {})
    monkeypatch.setattr(sch.handlers, "publish", lambda bus, payload: None)
    if packet_at is None:
        monkeypatch.setattr(sch.compute, "read_summary_packet", lambda bus: {"p": 1})
        monkeypatch.setattr(sch.compute, "summary_fingerprint",
                            lambda packet: ("same",))
    else:
        monkeypatch.setattr(sch.compute, "read_summary_packet",
                            lambda bus: packet_at(clock["t"]))
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


def _counts(rl, rg, fl, fg):
    return {"rising_leading": rl, "rising_lagging": rg,
            "falling_leading": fl, "falling_lagging": fg, "unknown": 0}


def _readings(counts, trend="Circling"):
    """A packet as ``read_summary_packet`` builds it — the 2026-09-11 close."""
    return {"sentiment": {"composite": 6.33},
            "trend": {"word": trend, "score": 53.77},
            "bias": "Neutral", "signal": "Neutral", "size": "1.00x",
            "regime": {"word": "Balanced", "confidence": 0.76},
            "bullbear": {"horizon": "today", "counts": counts}}


def _ok(calls):
    def _generate(t):
        calls.append(t)
        return {"narrative": "ok", "inputs": {}, "as_of": ""}
    return _generate


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


def test_a_withheld_reply_is_not_retried_while_the_readings_hold(monkeypatch):
    """A withheld reply was answered and refused, and the same readings are
    refused the same way: the Circling fact's semicolon came back as a comma in
    3 of 3 live replies. Retrying them bought 30 paid calls and no sentence
    between 02:30 and 09:06 CT on 2026-09-11. Only a changed reading earns
    another attempt."""
    calls = []

    def _generate(t):
        calls.append(t)
        return sch.compute.WITHHELD

    published = _drive_loop(monkeypatch, _generate)
    assert len(calls) == 1, calls
    assert published == []


def test_after_a_withheld_reply_a_changed_reading_is_written(monkeypatch):
    calls = []

    def _generate(t):
        calls.append(t)
        if len(calls) == 1:
            return sch.compute.WITHHELD
        return {"narrative": "ok", "inputs": {}, "as_of": ""}

    published = _drive_loop(
        monkeypatch, _generate,
        packet_at=lambda t: _readings(_counts(3, 6, 0, 2),
                                      trend="Circling" if t < 15 * 60 else "Climbing"))
    assert len(calls) == 2, calls
    assert [s["narrative"] for s in published] == ["ok"]


def test_unchanged_readings_are_written_once_and_then_left_alone(monkeypatch):
    calls = []
    published = _drive_loop(monkeypatch, _ok(calls))
    assert len(calls) == 1 and len(published) == 1


def test_a_one_sector_wobble_does_not_buy_a_new_sentence(monkeypatch):
    """2026-09-11: with the Bull/Bear counts compared exactly, one of eleven
    sectors crossing flat or crossing the S&P 500 inside every ten-minute gap
    bought a new paid sentence each gap from 09:14 to 16:40 CT (43 of them)."""
    wobble = [_counts(3, 6, 0, 2), _counts(4, 5, 0, 2), _counts(3, 7, 0, 1),
              _counts(3, 5, 0, 3), _counts(2, 7, 1, 1)]
    calls = []
    _drive_loop(monkeypatch, _ok(calls),
                packet_at=lambda t: _readings(wobble[int(t // 60) % len(wobble)]))
    assert len(calls) == 1, calls


def test_a_two_sector_shift_is_written_after_the_gap(monkeypatch):
    calls = []
    _drive_loop(monkeypatch, _ok(calls),
                packet_at=lambda t: _readings(
                    _counts(3, 6, 0, 2) if t < 15 * 60 else _counts(3, 4, 0, 4)))
    assert len(calls) == 2, calls


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


def test_outcome_reads_a_finished_task_without_raising():
    import asyncio

    async def _go():
        loop = asyncio.get_running_loop()
        done, held, failed, boom, cancelled = (loop.create_future() for _ in range(5))
        done.set_result("published")
        held.set_result("withheld")
        failed.set_result("failed")
        boom.set_exception(RuntimeError("x"))
        cancelled.cancel()
        return [sch._outcome(f) for f in (done, held, failed, boom, cancelled)]

    assert asyncio.run(_go()) == ["published", "withheld", "failed", "failed",
                                  "failed"]
