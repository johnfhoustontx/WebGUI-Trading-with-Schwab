"""trade_svc's scheduler: one branch, the daily dividend pull (news v2, Task 15)."""
import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.trade_svc import scheduler

CT = ZoneInfo("America/Chicago")
C = {"enabled": True, "refresh_at": "06:40", "retry_min": 15}


def ct(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=CT)


def test_due_only_on_a_trading_day_at_or_after_refresh_at():
    assert scheduler.dividends_due(ct("2026-09-26 06:39"), last_run=None, cfg=C) is False   # Saturday
    assert scheduler.dividends_due(ct("2026-09-26 12:00"), last_run=None, cfg=C) is False   # Saturday
    assert scheduler.dividends_due(ct("2026-09-28 06:39"), last_run=None, cfg=C) is False
    assert scheduler.dividends_due(ct("2026-09-28 06:40"), last_run=None, cfg=C) is True
    assert scheduler.dividends_due(ct("2026-09-28 14:00"), last_run="2026-09-28", cfg=C) is False
    assert scheduler.dividends_due(ct("2026-09-29 06:40"), last_run="2026-09-28", cfg=C) is True


def test_a_holiday_is_not_due():
    assert scheduler.dividends_due(ct("2026-11-26 09:00"), last_run=None, cfg=C) is False  # Thanksgiving


def test_an_aware_utc_now_is_read_in_central_time():
    # 11:40 UTC Monday is 06:40 CT (CDT, UTC-5).
    now = dt.datetime(2026, 9, 28, 11, 40, tzinfo=dt.timezone.utc)
    assert scheduler.dividends_due(now, last_run=None, cfg=C) is True
    assert scheduler.dividends_due(now - dt.timedelta(minutes=1), last_run=None, cfg=C) is False


def test_disabled_never_due():
    for enabled in (False, "true", 1, None):
        cfg = {"enabled": enabled, "refresh_at": "06:40"}
        assert scheduler.dividends_due(ct("2026-09-28 10:00"), last_run=None, cfg=cfg) is False


@pytest.mark.parametrize("bad", ["6:40pm", "25:00", "06:61", "", None, 640, True])
def test_a_bad_refresh_at_falls_back_to_the_default(bad):
    assert scheduler.refresh_at_time({"refresh_at": bad}) == dt.time(6, 40)


def test_refresh_at_is_read_from_the_config():
    assert scheduler.refresh_at_time({"refresh_at": "07:05"}) == dt.time(7, 5)
    cfg = {"enabled": True, "refresh_at": "07:05"}
    assert scheduler.dividends_due(ct("2026-09-28 07:04"), None, cfg) is False
    assert scheduler.dividends_due(ct("2026-09-28 07:05"), None, cfg) is True


def test_dividends_config_delegates_to_the_shared_accessor(monkeypatch):
    """calendar_config() leaves sub-tables out, so the dividends table comes
    from its own accessor."""
    from shared import news_config as nc
    monkeypatch.setattr(nc, "dividends_config",
                        lambda: {"enabled": True, "refresh_at": "07:00"}, raising=False)
    assert scheduler.dividends_config() == {"enabled": True, "refresh_at": "07:00"}


# ── the loop ───────────────────────────────────────────────────────────────

class _Stop(Exception):
    pass


class _Harness:
    """A fake clock: each sleep advances it; the loop stops after ``passes``."""

    def __init__(self, monkeypatch, *, start, passes, refresh, last_run=None):
        self.now = start
        self.passes = passes
        self.ticks = 0
        self.sleeps = []
        self.calls = []
        self.threads = []
        self.last_run = last_run
        self._refresh = refresh
        monkeypatch.setattr(scheduler, "_utcnow", lambda: self.now)
        monkeypatch.setattr(scheduler, "_sleep", self.sleep)
        monkeypatch.setattr(scheduler, "_monotonic",
                            lambda: (self.now - start).total_seconds())
        monkeypatch.setattr(scheduler, "dividends_config", lambda: dict(C))
        monkeypatch.setattr(scheduler, "_read_last_run", lambda: self.last_run)
        monkeypatch.setattr(scheduler, "_refresh", self.refresh)
        monkeypatch.setattr(scheduler._heartbeat, "tick", self.tick)

    def tick(self):
        self.ticks += 1

    def refresh(self):
        import threading
        self.threads.append(threading.current_thread() is threading.main_thread())
        self.calls.append(self.now)
        return self._refresh(self)

    async def sleep(self, s):
        self.sleeps.append(s)
        if len(self.sleeps) >= self.passes:
            raise _Stop
        self.now = self.now + dt.timedelta(seconds=s)

    def run(self):
        with pytest.raises(_Stop):
            asyncio.run(scheduler.loop(object()))


def _ok(h):
    h.last_run = h.now.astimezone(CT).date().isoformat()
    return 3


def test_loop_runs_refresh_in_an_executor_once_a_day(monkeypatch):
    h = _Harness(monkeypatch, start=ct("2026-09-28 06:38"), passes=10, refresh=_ok)
    h.run()
    assert len(h.calls) == 1 and h.calls[0] >= ct("2026-09-28 06:40")
    assert h.threads == [False]            # not on the event-loop thread
    assert h.ticks == 10                   # one beat per pass
    assert all(s <= scheduler.TICK_S for s in h.sleeps)


def test_loop_does_not_refetch_after_a_restart(monkeypatch):
    h = _Harness(monkeypatch, start=ct("2026-09-28 09:00"), passes=5, refresh=_ok,
                 last_run="2026-09-28")
    h.run()
    assert h.calls == []


def test_loop_survives_a_failure_and_retries_after_the_backoff(monkeypatch):
    def boom(h):
        raise RuntimeError("proxy down")

    retry_s = C["retry_min"] * 60
    passes = int(retry_s / scheduler.TICK_S) + 3
    h = _Harness(monkeypatch, start=ct("2026-09-28 06:40"), passes=passes, refresh=boom)
    h.run()
    assert len(h.calls) == 2                               # it kept going
    assert (h.calls[1] - h.calls[0]).total_seconds() >= retry_s


def test_the_retry_backoff_is_read_from_the_config(monkeypatch):
    def boom(h):
        raise RuntimeError("proxy down")

    passes = int(5 * 60 / scheduler.TICK_S) + 3            # 8 minutes of ticks
    h = _Harness(monkeypatch, start=ct("2026-09-28 06:40"), passes=passes, refresh=boom)
    monkeypatch.setattr(scheduler, "dividends_config", lambda: dict(C, retry_min=5))
    h.run()
    assert len(h.calls) == 2                   # 15 minutes would still be waiting
    assert 300 <= (h.calls[1] - h.calls[0]).total_seconds() < 900


@pytest.mark.parametrize("bad", [0, -3, 1441, True, 15.0, "15", None])
def test_a_bad_retry_min_is_the_shared_default(bad):
    from shared import news_config as nc
    default = nc.DEFAULTS["calendar"]["dividends"]["retry_min"]
    assert scheduler.retry_after_fail_s({"retry_min": bad}) == default * 60
    assert scheduler.retry_after_fail_s(None) == default * 60


def test_the_retry_is_not_a_module_literal():
    assert not hasattr(scheduler, "RETRY_AFTER_FAIL_S")
    assert scheduler.retry_after_fail_s({"retry_min": 7}) == 420


def test_loop_survives_a_config_failure(monkeypatch):
    h = _Harness(monkeypatch, start=ct("2026-09-28 06:40"), passes=3, refresh=_ok)

    def broken():
        raise ValueError("bad toml")
    monkeypatch.setattr(scheduler, "dividends_config", broken)
    h.run()
    assert h.calls == [] and h.ticks == 3


def test_read_last_run_reads_the_store(tmp_path, monkeypatch):
    from shared import dividends as store
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", tmp_path / "d.db")
    assert scheduler._read_last_run() is None
    conn = store.init_db()
    store.set_last_run_day(conn, "2026-09-28")
    store.close_db(conn)
    assert scheduler._read_last_run() == "2026-09-28"


# ── app wiring and the on-demand command ────────────────────────────────────

def test_app_wires_the_scheduler_loop(monkeypatch):
    """Under pytest the ``schedulers`` flag is off, so wire-check by capture."""
    import importlib
    from services import _scaffold
    from services.trade_svc import app as app_module

    seen = {}
    real = _scaffold.make_app

    def spy(domain, **kw):
        seen.update(kw)
        return real(domain, **kw)
    monkeypatch.setattr(_scaffold, "make_app", spy)
    importlib.reload(app_module)
    assert seen["scheduler"] is scheduler.loop


def _cmd(type_, age_s=0):
    from shared.contracts.envelope import Command
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_s)).isoformat()
    return Command(type=type_, args={}, ts=ts)


def _spy(monkeypatch):
    from services.trade_svc import dividends
    calls = []
    monkeypatch.setattr(dividends, "refresh", lambda **kw: calls.append(kw) or 0)
    return calls


def test_dividends_refresh_command_forces_the_pull(monkeypatch):
    from services.trade_svc import handlers
    calls = _spy(monkeypatch)
    monkeypatch.setattr(scheduler, "dividends_config", lambda: dict(C))
    handlers.handle_command(object(), _cmd("dividends_refresh"))
    assert calls == [{"force": True}]


def test_dividends_refresh_command_honours_the_off_switch(monkeypatch):
    from services.trade_svc import handlers
    calls = _spy(monkeypatch)
    monkeypatch.setattr(scheduler, "dividends_config",
                        lambda: {"enabled": False, "refresh_at": "06:40"})
    handlers.handle_command(object(), _cmd("dividends_refresh"))
    assert calls == []


def test_a_replayed_refresh_command_is_dropped_by_age(monkeypatch):
    """Forced, the pull is not idempotent - so an old command (a fresh consumer
    group re-reading the stream) must not run it."""
    from services.trade_svc import handlers
    calls = _spy(monkeypatch)
    monkeypatch.setattr(scheduler, "dividends_config", lambda: dict(C))
    handlers.handle_command(
        object(), _cmd("dividends_refresh", age_s=handlers.DIVIDENDS_REFRESH_MAX_AGE_SEC + 5))
    assert calls == []
    handlers.handle_command(
        object(), _cmd("dividends_refresh", age_s=handlers.DIVIDENDS_REFRESH_MAX_AGE_SEC - 30))
    assert calls == [{"force": True}]


def test_a_command_with_no_ts_is_treated_as_fresh(monkeypatch):
    from shared.contracts.envelope import Command
    from services.trade_svc import handlers
    calls = _spy(monkeypatch)
    monkeypatch.setattr(scheduler, "dividends_config", lambda: dict(C))
    handlers.handle_command(object(), Command(type="dividends_refresh", ts=None))
    assert calls == [{"force": True}]


# ── the in-memory run day, and the parse that trusts the validated config ──

class _CountingHarness(_Harness):
    def __init__(self, monkeypatch, **kw):
        super().__init__(monkeypatch, **kw)
        self.reads = []
        monkeypatch.setattr(scheduler, "_read_last_run", self.read)

    def read(self):
        import threading
        self.reads.append((self.now, threading.current_thread() is threading.main_thread()))
        return self.last_run


def test_after_a_run_the_rest_of_the_day_reads_no_store(monkeypatch):
    """The run day is kept in memory once a pull succeeds, so the ticks after
    it until midnight never open the store."""
    h = _CountingHarness(monkeypatch, start=ct("2026-09-28 06:38"), passes=30, refresh=_ok)
    h.run()
    assert len(h.calls) == 1
    ran = h.calls[0]
    assert [t for t, _ in h.reads if t > ran] == []      # 0 reads after the run
    assert len([t for t, _ in h.reads if t <= ran]) == 1  # the one that decided it


def test_the_store_read_runs_in_the_executor(monkeypatch):
    h = _CountingHarness(monkeypatch, start=ct("2026-09-28 09:00"), passes=3, refresh=_ok,
                         last_run="2026-09-28")
    h.run()
    assert h.reads and all(on_main is False for _, on_main in h.reads)


def test_a_restart_reads_the_store_once_then_remembers_the_day(monkeypatch):
    """Nothing in memory after a restart: the store says today already ran, so
    no pull, and the day is remembered - one read, not one per tick."""
    h = _CountingHarness(monkeypatch, start=ct("2026-09-28 09:00"), passes=10, refresh=_ok,
                         last_run="2026-09-28")
    h.run()
    assert h.calls == [] and len(h.reads) == 1


def test_the_next_trading_day_runs_again(monkeypatch):
    h = _CountingHarness(monkeypatch, start=ct("2026-09-28 23:58"), passes=3, refresh=_ok,
                         last_run="2026-09-28")
    monkeypatch.setattr(scheduler, "TICK_S", 60)
    h.run()
    assert h.calls == []            # before 06:40 on the 29th: nothing due
    h2 = _CountingHarness(monkeypatch, start=ct("2026-09-29 06:39"), passes=4, refresh=_ok,
                          last_run="2026-09-28")
    h2.run()
    assert len(h2.calls) == 1


def test_the_fallback_is_the_shared_default_not_a_second_literal(monkeypatch):
    from shared import news_config as nc
    div = dict(nc.DEFAULTS["calendar"]["dividends"], refresh_at="05:15")
    cal = dict(nc.DEFAULTS["calendar"], dividends=div)
    monkeypatch.setattr(nc, "DEFAULTS", dict(nc.DEFAULTS, calendar=cal))
    assert scheduler.refresh_at_time({"refresh_at": "junk"}) == dt.time(5, 15)
    assert not hasattr(scheduler, "_DEFAULT_REFRESH_AT")
