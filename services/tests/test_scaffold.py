import logging
import time

import pytest
from fastapi.testclient import TestClient
from services import _heartbeat, _scaffold
from services._scaffold import make_app
from shared.bus import Bus


@pytest.fixture
def schedulers_enabled(monkeypatch):
    """Opt this test back in to running a scheduler.

    ``repo_paths`` forces every suppression flag OFF under pytest, so by default
    ``make_app`` wires no scheduler task at all. Tests that exist to exercise the
    supervision machinery itself must therefore say so explicitly, rather than
    the guard being weakened to accommodate them.
    """
    monkeypatch.setitem(_scaffold.ENV_FLAGS, "schedulers", True)
    monkeypatch.delenv("TRADING_ENABLE_SCHEDULERS", raising=False)


def test_health():
    app = make_app("sentiment")
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        # Backward-compatible keys unchanged...
        assert body["domain"] == "sentiment"
        assert body["up"] is True
        # ...plus the additive R2 scheduler-heartbeat keys.
        assert body["scheduler_alive"] is True  # no scheduler → trivially alive
        assert body["scheduler_restarts"] == 0


def test_command_handler_invoked_and_acked():
    bus = Bus(fake=True)
    seen = []

    def handler(b, command):
        seen.append(command.type)

    app = make_app("optionsx", command_handler=handler, bus=bus, poll_block_ms=50)
    bus.enqueue_command("cmd:optionsx", {"type": "rescan", "args": {}})
    with TestClient(app):  # triggers startup -> consumer loop runs
        # give the background loop a moment to consume; poll up to ~2s
        import time

        for _ in range(40):
            if seen:
                break
            time.sleep(0.05)
    assert seen == ["rescan"]


def test_bad_command_does_not_kill_loop():
    """A handler that raises must not stop later commands being processed."""
    bus = Bus(fake=True)
    seen = []

    def handler(b, command):
        if command.type == "boom":
            raise RuntimeError("bad command")
        seen.append(command.type)

    app = make_app("crashx", command_handler=handler, bus=bus, poll_block_ms=50)
    bus.enqueue_command("cmd:crashx", {"type": "boom", "args": {}})
    bus.enqueue_command("cmd:crashx", {"type": "ok", "args": {}})
    with TestClient(app):
        import time

        for _ in range(40):
            if "ok" in seen:
                break
            time.sleep(0.05)
    assert seen == ["ok"]


def test_scheduler_exception_swallowed(schedulers_enabled):
    """A scheduler that raises must not crash app startup/shutdown."""
    bus = Bus(fake=True)

    async def sched(b):
        raise RuntimeError("scheduler boom")

    app = make_app("boomsched", scheduler=sched, bus=bus)
    # Entering/exiting the context (startup+shutdown) must not raise.
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_scheduler_runs(schedulers_enabled):
    bus = Bus(fake=True)
    ran = []

    async def sched(b):
        ran.append(True)

    app = make_app("schedx", scheduler=sched, bus=bus)
    with TestClient(app):
        import time

        for _ in range(40):
            if ran:
                break
            time.sleep(0.05)
    assert ran == [True]


def test_slow_handler_does_not_block_health():
    """A multi-second sync handler runs on the executor, so /health stays live.

    (P3) The handler blocks for a beat; a concurrent /health must return well
    before it finishes — proving the handler no longer runs on the event loop.
    """
    import threading
    import time

    bus = Bus(fake=True)
    started = threading.Event()
    release = threading.Event()

    def slow_handler(b, command):
        started.set()
        release.wait(5.0)  # block the handler until the test lets it go

    app = make_app("slowx", command_handler=slow_handler, bus=bus, poll_block_ms=50)
    bus.enqueue_command("cmd:slowx", {"type": "grind", "args": {}})
    with TestClient(app) as client:
        assert started.wait(2.0), "handler never started on the executor"
        # Handler is mid-flight (blocked). /health must still answer promptly.
        t0 = time.monotonic()
        r = client.get("/health")
        elapsed = time.monotonic() - t0
        assert r.status_code == 200
        assert elapsed < 1.5, f"/health blocked by handler ({elapsed:.2f}s)"
        release.set()


def test_raising_handler_dead_letters_and_acks():
    """(A2) A handler that raises → command dead-lettered + ack'd (PEL empties)."""
    import json
    import time

    bus = Bus(fake=True)
    app = make_app(
        "deadx",
        command_handler=lambda b, c: (_ for _ in ()).throw(RuntimeError("boom")),
        bus=bus,
        poll_block_ms=50,
    )
    bus.enqueue_command("cmd:deadx", {"type": "explode", "args": {}})
    with TestClient(app):
        for _ in range(60):
            if bus._r.llen("cmd:deadx:dead"):
                break
            time.sleep(0.05)
    dead = bus._r.lrange("cmd:deadx:dead", 0, -1)
    assert len(dead) == 1
    rec = json.loads(dead[0])
    assert rec["reason"] == "handler raised"
    assert "explode" in rec["fields"]["data"]
    # ack'd → not stuck in the PEL
    assert bus._r.xpending("cmd:deadx", "deadx-svc")["pending"] == 0


def test_startup_drains_stranded_pel_to_dead_letter():
    """(A2) A pre-existing un-acked PEL entry is drained on startup, not re-run."""
    import json
    import time

    bus = Bus(fake=True)
    seen = []

    # Simulate a prior crashed consumer: read (into group "drainx-svc") without ack.
    bus.enqueue_command("cmd:drainx", {"type": "paper_create", "args": {}})
    read = bus.consume_commands(
        "cmd:drainx", group="drainx-svc", consumer="c1", block_ms=50
    )
    assert len(read) == 1  # pending, un-acked

    app = make_app(
        "drainx",
        command_handler=lambda b, c: seen.append(c.type),
        bus=bus,
        poll_block_ms=50,
    )
    with TestClient(app):
        for _ in range(60):
            if bus._r.llen("cmd:drainx:dead"):
                break
            time.sleep(0.05)
    # stranded entry went to dead-letter and was NOT handed to the handler
    dead = bus._r.lrange("cmd:drainx:dead", 0, -1)
    assert len(dead) == 1
    assert "paper_create" in json.loads(dead[0])["fields"]["data"]
    assert seen == []  # never auto-re-executed
    assert bus._r.xpending("cmd:drainx", "drainx-svc")["pending"] == 0


# ---------------------------------------------------------------------------
# R3a — persistent logging bootstrap
# ---------------------------------------------------------------------------

def test_logging_bootstrap_is_off_and_creates_no_file_under_pytest(tmp_path):
    """(R3a) Under pytest, make_app must NOT add a file handler / create logs."""
    from services import _scaffold

    root = logging.getLogger()
    before = list(root.handlers)
    make_app("logtest")
    after = list(root.handlers)
    # No RotatingFileHandler was attached (pytest gate active).
    from logging.handlers import RotatingFileHandler

    assert not any(isinstance(h, RotatingFileHandler) for h in after)
    # No handlers leaked onto the root logger at all.
    assert after == before
    # The gate is the reason: install returns None / does nothing under pytest.
    assert _scaffold._install_file_logging("logtest") is None


def test_logging_bootstrap_idempotent_and_writes(tmp_path):
    """(R3a) Forcing the bootstrap on: adds ONE rotating file handler, writes,
    and a second call is a no-op (no duplicate handler, no crash)."""
    from logging.handlers import RotatingFileHandler

    from services import _scaffold

    root = logging.getLogger()
    added_before = [
        h for h in root.handlers if isinstance(h, RotatingFileHandler)
    ]
    try:
        path1 = _scaffold._install_file_logging(
            "unitlog", log_root=tmp_path, force=True
        )
        assert path1 is not None
        assert path1.exists()
        handlers1 = [
            h for h in root.handlers if isinstance(h, RotatingFileHandler)
        ]
        assert len(handlers1) == len(added_before) + 1
        # Idempotent: a second call adds nothing.
        path2 = _scaffold._install_file_logging(
            "unitlog", log_root=tmp_path, force=True
        )
        assert path2 == path1
        handlers2 = [
            h for h in root.handlers if isinstance(h, RotatingFileHandler)
        ]
        assert len(handlers2) == len(handlers1)
        # It really logs to the file.
        logging.getLogger("some.module").error("hello-unitlog")
        for h in handlers2:
            h.flush()
        assert "hello-unitlog" in path1.read_text(encoding="utf-8")
    finally:
        # Clean up the handler we forced on so it can't leak into other tests.
        for h in [
            h
            for h in root.handlers
            if isinstance(h, RotatingFileHandler)
            and h not in added_before
        ]:
            root.removeHandler(h)
            h.close()
        _scaffold._LOG_INSTALLED.discard("unitlog")


# ---------------------------------------------------------------------------
# R2 — scheduler supervision + health honesty
# ---------------------------------------------------------------------------

def test_no_scheduler_service_reports_healthy():
    """(R2) A command-only service (scheduler=None) is healthy; alive=True."""
    app = make_app("tradex")
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["up"] is True
        assert body["domain"] == "tradex"
        # No scheduler → reported alive (nothing can be dead).
        assert body["scheduler_alive"] is True


def test_health_backward_compatible_keys_present():
    """(R2) Existing keys unchanged; new keys are additive."""
    app = make_app("sentiment")
    with TestClient(app) as client:
        body = client.get("/health").json()
        assert body["domain"] == "sentiment"
        assert body["up"] is True


def test_dead_scheduler_restarted_and_health_reflects_it(schedulers_enabled):
    """(R2) A scheduler that raises on the first run is RESTARTED (not
    permanently dead) after a short backoff; /health shows it alive again."""
    bus = Bus(fake=True)
    calls = []

    async def sched(b):
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise RuntimeError("first-run boom")
        # Second run: stay alive (simulate a healthy internal loop).
        import asyncio

        await asyncio.sleep(3600)

    app = make_app(
        "restartx", scheduler=sched, bus=bus, scheduler_restart_backoff_s=0.05
    )
    with TestClient(app) as client:
        # Wait for the restart to happen (2 calls).
        for _ in range(100):
            if len(calls) >= 2:
                break
            time.sleep(0.05)
        assert len(calls) >= 2, "scheduler was not restarted after it raised"
        body = client.get("/health").json()
        assert body["up"] is True
        # It self-healed and is running again.
        assert body["scheduler_alive"] is True
        assert body["scheduler_restarts"] >= 1


def test_scheduler_permanently_dead_when_backoff_budget_exhausted(schedulers_enabled):
    """(R2) A scheduler that ALWAYS raises exhausts the restart budget and is
    reported NOT alive, so an external probe can distinguish it from healthy."""
    bus = Bus(fake=True)
    calls = []

    async def always_boom(b):
        calls.append(1)
        raise RuntimeError("always")

    app = make_app(
        "deadschedx",
        scheduler=always_boom,
        bus=bus,
        scheduler_restart_backoff_s=0.02,
        scheduler_max_restarts=3,
    )
    with TestClient(app) as client:
        # Wait until the budget is exhausted (initial run + 3 restarts = 4 calls).
        for _ in range(200):
            body = client.get("/health").json()
            if body["scheduler_alive"] is False:
                break
            time.sleep(0.02)
        body = client.get("/health").json()
        # The process still answers, and until 2026-10-04 this asserted that
        # made it "up". It does not: a service whose scheduler has stopped for
        # good is doing none of its work (audit AR-06).
        assert body["up"] is False
        assert body["scheduler_alive"] is False
        assert body["scheduler_restarts"] == 3


def test_healthy_scheduler_not_restarted_and_alive(schedulers_enabled):
    """(R2) A healthy scheduler (internal infinite loop) is NOT restarted; it
    stays alive with zero restarts, and last_tick_age stays small."""
    import asyncio

    bus = Bus(fake=True)
    started = []

    async def healthy(b):
        started.append(1)
        while True:
            _heartbeat.tick()
            await asyncio.sleep(0.01)

    app = make_app("livex", scheduler=healthy, bus=bus)
    with TestClient(app) as client:
        for _ in range(40):
            if started:
                break
            time.sleep(0.05)
        time.sleep(0.3)
        body = client.get("/health").json()
        assert body["scheduler_alive"] is True
        assert body["scheduler_restarts"] == 0
        assert len(started) == 1  # ran exactly once, never restarted
        assert body["scheduler_last_tick_age_s"] is not None
        assert body["scheduler_last_tick_age_s"] < 0.25
        # uptime keeps growing while the tick age stays near zero (a margin,
        # not the full 0.3 s: Windows timers land a few ms short)
        assert (body["scheduler_uptime_s"]
                - body["scheduler_last_tick_age_s"]) >= 0.2


# --- degrade counters on /health --------------------------------------------

def test_health_reports_degrade_counts():
    """A silent degrade is invisible; /health is where it becomes a number.

    The Status page already reads /health, so surfacing the counter here means
    "sentiment: 340 degrades this session" shows up with no new probe."""
    from fastapi.testclient import TestClient

    from services import _degrade

    _degrade.reset()
    try:
        app = _scaffold.make_app("probe", bus=Bus(fake=True))
        with TestClient(app) as client:
            body = client.get("/health").json()
            assert body["degrades_total"] == 0
            assert body["degrades"] == {}

            try:
                raise ValueError("boom")
            except Exception:
                _degrade.degraded("probe.thing")

            body = client.get("/health").json()
            assert body["degrades_total"] == 1
            assert body["degrades"] == {"probe.thing": 1}
    finally:
        _degrade.reset()


# --- scheduler heartbeat: last tick vs uptime (2026-09-16) --------------------
# ``scheduler_last_tick_age_s`` used to be "seconds since the scheduler task last
# (re)started" — for a healthy loop that never restarts, just process uptime. On
# prod it read 8,178 s on a scheduler that was ticking every 30 s, and a loop
# that HUNG (never raising, never returning) would have looked identical. The
# old number is now ``scheduler_uptime_s``; the tick age is real.


def _wait_started(started):
    for _ in range(40):
        if started:
            return
        time.sleep(0.05)


def test_a_loop_that_stops_ticking_shows_a_growing_tick_age(schedulers_enabled):
    import asyncio

    started = []

    async def hangs_after_one_tick(b):
        started.append(1)
        _heartbeat.tick()
        await asyncio.sleep(3600)          # alive, not raising — but stuck

    app = make_app("hangx", scheduler=hangs_after_one_tick, bus=Bus(fake=True))
    with TestClient(app) as client:
        _wait_started(started)
        time.sleep(0.4)
        body = client.get("/health").json()
        assert body["scheduler_alive"] is True       # the supervisor cannot see it
        assert body["scheduler_last_tick_age_s"] >= 0.35
        assert body["scheduler_uptime_s"] >= body["scheduler_last_tick_age_s"]


def test_a_loop_that_never_ticks_reports_no_tick_age(schedulers_enabled):
    import asyncio

    started = []

    async def silent(b):
        started.append(1)
        while True:
            await asyncio.sleep(0.01)

    app = make_app("silentx", scheduler=silent, bus=Bus(fake=True))
    with TestClient(app) as client:
        _wait_started(started)
        body = client.get("/health").json()
        assert body["scheduler_last_tick_age_s"] is None
        assert body["scheduler_uptime_s"] is not None


def test_a_restart_clears_the_previous_runs_tick(schedulers_enabled):
    """A tick belongs to the run that made it: after a restart, a stale tick
    from the dead run must not be reported as this run's heartbeat."""
    _heartbeat.tick()
    health = _scaffold._SchedulerHealth(has_scheduler=True)
    import asyncio

    async def dies(b):
        raise RuntimeError("boom")

    asyncio.run(_scaffold._supervise_scheduler(dies, None, health, 0.0, 0))
    assert _heartbeat.age_s() is None


def test_suppressed_environment_reports_neither(monkeypatch):
    import repo_paths
    monkeypatch.setitem(repo_paths.ENV_FLAGS, "schedulers", False)
    monkeypatch.delenv("TRADING_ENABLE_SCHEDULERS", raising=False)

    async def sched(b):
        raise AssertionError("must not run")

    _heartbeat.tick()   # a stale tick from an earlier test in this process
    app = make_app("devx", scheduler=sched, bus=Bus(fake=True))
    with TestClient(app) as client:
        body = client.get("/health").json()
        assert body["scheduler_uptime_s"] is None
        assert body["scheduler_last_tick_age_s"] is None


@pytest.mark.parametrize("svc", ["options_svc", "sentiment_svc",
                                 "market_svc", "portfolio_svc", "news_svc",
                                 "trade_svc"])
def test_every_service_loop_beats_inside_its_while_loop(svc):
    """A beat outside the loop body would report one tick at startup and then a
    forever-growing age — the old number under the new name."""
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / svc / "scheduler.py").read_text(
        encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "loop")
    beats = [c for w in ast.walk(fn) if isinstance(w, ast.While)
             for c in ast.walk(w)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
             and c.func.attr == "tick"
             and isinstance(c.func.value, ast.Name) and c.func.value.id == "_heartbeat"]
    assert beats, f"{svc}.scheduler.loop has no _heartbeat.tick() inside a while loop"


# ── extra consumers: a second stream with its own loop ──────────────────────

def _wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_an_extra_stream_is_consumed_by_its_own_handler():
    bus = Bus(fake=True)
    domain_seen, extra_seen = [], []
    app = make_app("extrax", command_handler=lambda b, c: domain_seen.append(c.type),
                   extra_consumers=(("cmd:extrax_public",
                                     lambda b, c: extra_seen.append(c.type)),),
                   bus=bus, poll_block_ms=50)
    bus.enqueue_command("cmd:extrax_public", {"type": "public_scan", "args": {}})
    bus.enqueue_command("cmd:extrax", {"type": "rescan", "args": {}})
    with TestClient(app):
        assert _wait_for(lambda: extra_seen and domain_seen)
    assert extra_seen == ["public_scan"]      # never handed to the domain handler
    assert domain_seen == ["rescan"]          # and the reverse


def test_a_blocked_domain_handler_does_not_hold_up_the_extra_stream():
    """THE POINT OF A SECOND CONSUMER: a 40 s public scan must never queue
    ahead of a paper create, and a slow owner command must never stall the
    public queue. Each stream has its own loop."""
    import threading
    bus = Bus(fake=True)
    release = threading.Event()
    extra_seen = []

    def slow_domain(b, c):
        release.wait(5.0)

    app = make_app("blockx", command_handler=slow_domain,
                   extra_consumers=(("cmd:blockx_public",
                                     lambda b, c: extra_seen.append(c.type)),),
                   bus=bus, poll_block_ms=50)
    bus.enqueue_command("cmd:blockx", {"type": "grind", "args": {}})
    bus.enqueue_command("cmd:blockx_public", {"type": "public_scan", "args": {}})
    try:
        with TestClient(app):
            assert _wait_for(lambda: extra_seen, timeout=2.0), \
                "the extra stream waited behind the domain handler"
            release.set()
    finally:
        release.set()


def test_an_extra_stream_handler_that_raises_is_dead_lettered_on_its_own_stream():
    bus = Bus(fake=True)

    def boom(b, c):
        raise RuntimeError("bad public command")

    app = make_app("deadx", extra_consumers=(("cmd:deadx_public", boom),),
                   bus=bus, poll_block_ms=50)
    bus.enqueue_command("cmd:deadx_public", {"type": "public_scan", "args": {}})
    with TestClient(app):
        assert _wait_for(lambda: bus._r.llen("cmd:deadx_public:dead") == 1)
    assert bus._r.llen("cmd:deadx:dead") == 0


# --- the consumer refuses a replayed command (audit AR-05) --------------------
# A consumer group is created at id 0, so a fresh group is handed the stream's
# whole history. Only a few options commands were age-gated, each in its own
# branch; ``paper_reset``, ``paper_delete`` and the rest ran on a replay. The
# gate is now in the one loop every service's own stream goes through.

def _old_ts(seconds):
    import datetime as dt
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(seconds=seconds)).isoformat()


def _run_until(bus, app, done, tries=60):
    import time
    with TestClient(app):
        for _ in range(tries):
            if done():
                break
            time.sleep(0.05)


def test_a_replayed_command_is_dropped_not_run():
    from shared import service_limits
    bus = Bus(fake=True)
    seen = []
    app = make_app("oldx", command_handler=lambda b, c: seen.append(c.type),
                   bus=bus, poll_block_ms=50)
    bus.enqueue_command("cmd:oldx", {"type": "paper_reset", "args": {},
                                     "ts": _old_ts(service_limits.replay_max_sec() + 60)})
    bus.enqueue_command("cmd:oldx", {"type": "marker", "args": {}})
    _run_until(bus, app, lambda: seen)
    assert seen == ["marker"]
    assert bus._r.xpending("cmd:oldx", "oldx-svc")["pending"] == 0   # acked
    assert bus._r.llen("cmd:oldx:dead") == 0       # dropped, not a failure


def test_a_command_inside_the_limit_runs():
    from shared import service_limits
    bus = Bus(fake=True)
    seen = []
    app = make_app("freshx", command_handler=lambda b, c: seen.append(c.type),
                   bus=bus, poll_block_ms=50)
    bus.enqueue_command("cmd:freshx", {"type": "rescan", "args": {},
                                       "ts": _old_ts(service_limits.replay_max_sec() - 120)})
    _run_until(bus, app, lambda: seen)
    assert seen == ["rescan"]


def test_a_command_the_service_marks_safe_runs_at_any_age():
    bus = Bus(fake=True)
    seen = []
    app = make_app("latex", command_handler=lambda b, c: seen.append(c.type),
                   bus=bus, poll_block_ms=50, late_ok=frozenset({"reload"}))
    bus.enqueue_command("cmd:latex", {"type": "reload", "args": {}, "ts": _old_ts(86400)})
    _run_until(bus, app, lambda: seen)
    assert seen == ["reload"]


def test_a_command_with_no_timestamp_is_never_dropped():
    """A command serialized before ``ts`` existed has no age to judge."""
    bus = Bus(fake=True)
    seen = []
    app = make_app("legacyx", command_handler=lambda b, c: seen.append(c.type),
                   bus=bus, poll_block_ms=50)
    bus.enqueue_command("cmd:legacyx", {"type": "anything", "args": {}, "ts": None})
    _run_until(bus, app, lambda: seen)
    assert seen == ["anything"]


def test_an_extra_stream_keeps_its_own_expiry_rules():
    """The public streams answer an expired request themselves (the visitor is
    waiting on a refusal). The scaffold does not drop for them."""
    bus = Bus(fake=True)
    seen = []
    app = make_app("extrax", bus=bus, poll_block_ms=50,
                   extra_consumers=(("cmd:extrax_pub", lambda b, c: seen.append(c.type)),))
    bus.enqueue_command("cmd:extrax_pub", {"type": "scan", "args": {}, "ts": _old_ts(86400)})
    _run_until(bus, app, lambda: seen)
    assert seen == ["scan"]


# --- /health tells the truth about the scheduler (audit AR-06) ----------------

def test_a_service_whose_scheduler_stopped_is_not_up(schedulers_enabled):
    bus = Bus(fake=True)

    async def always_boom(b):
        raise RuntimeError("always")

    app = make_app("deadupx", scheduler=always_boom, bus=bus,
                   scheduler_restart_backoff_s=0.02, scheduler_max_restarts=2)
    with TestClient(app) as client:
        for _ in range(200):
            body = client.get("/health").json()
            if body["scheduler_alive"] is False:
                break
            time.sleep(0.02)
        assert body["up"] is False
        assert "stopped" in body["reason"]


def test_a_scheduler_that_has_gone_silent_is_not_up(schedulers_enabled, monkeypatch):
    from services import _heartbeat, _scaffold
    from shared import service_limits
    bus = Bus(fake=True)

    async def sched(b):
        import asyncio
        _heartbeat.tick()
        await asyncio.sleep(3600)

    app = make_app("silentx", scheduler=sched, bus=bus)
    with TestClient(app) as client:
        for _ in range(100):
            if client.get("/health").json()["scheduler_last_tick_age_s"] is not None:
                break
            time.sleep(0.02)
        assert client.get("/health").json()["up"] is True
        monkeypatch.setattr(_scaffold._heartbeat, "age_s",
                            lambda: service_limits.tick_stale_sec() + 5.0)
        body = client.get("/health").json()
        assert body["up"] is False
        assert "has not run" in body["reason"]


def test_a_scheduler_that_has_not_ticked_yet_is_up(schedulers_enabled):
    """A fresh start has no tick age. That is not staleness."""
    from services import _scaffold
    hs = _scaffold._SchedulerHealth(has_scheduler=True)
    assert _scaffold.health_verdict(hs, stale_sec=600) == (True, None)


def test_a_service_with_no_scheduler_is_up():
    from services import _scaffold
    assert _scaffold.health_verdict(_scaffold._SchedulerHealth()) == (True, None)


def test_a_long_healthy_run_restores_the_restart_budget(schedulers_enabled, monkeypatch):
    """The budget was a lifetime count: ten unrelated faults a week apart and the
    scheduler stayed stopped for good."""
    from shared import service_limits
    monkeypatch.setattr(service_limits, "restart_reset_sec", lambda: 0)
    bus = Bus(fake=True)
    calls = []

    async def boom(b):
        calls.append(1)
        raise RuntimeError("x")

    app = make_app("budgetx", scheduler=boom, bus=bus,
                   scheduler_restart_backoff_s=0.01, scheduler_max_restarts=2)
    with TestClient(app) as client:
        for _ in range(100):
            if len(calls) >= 8:
                break
            time.sleep(0.02)
        body = client.get("/health").json()
    assert len(calls) >= 8, "the scheduler stopped being restarted"
    assert body["scheduler_alive"] is True


# --- AR-07: a dropped command is counted, and the service is told -------------

def test_health_counts_the_commands_that_were_never_run():
    bus = Bus(fake=True)
    app = make_app("dlcountx", command_handler=lambda b, c: None, bus=bus,
                   poll_block_ms=50)
    bus.dead_letter("cmd:dlcountx", {"data": "{}"}, "handler raised")
    bus.dead_letter("cmd:dlcountx", {"data": "{}"}, "handler raised")
    with TestClient(app) as client:
        assert client.get("/health").json()["dead_letters"] == 2


def test_a_command_stranded_by_a_restart_is_reported_to_the_service():
    """It is NOT re-run (a replayed open could double-open a position). The
    service is told, so it can answer the page that is still waiting."""
    bus = Bus(fake=True)
    bus.enqueue_command("cmd:dropx", {"type": "paper_create", "args": {"qty": 2}})
    bus.consume_commands("cmd:dropx", group="dropx-svc", consumer="c1", block_ms=50)
    ran, dropped = [], []
    app = make_app("dropx", command_handler=lambda b, c: ran.append(c.type), bus=bus,
                   poll_block_ms=50,
                   on_dropped=lambda b, c, why: dropped.append((c.type, c.args, why)))
    _run_until(bus, app, lambda: dropped)
    assert ran == []
    assert dropped == [("paper_create", {"qty": 2}, "restart")]


def test_a_replayed_command_is_reported_as_dropped_too():
    from shared import service_limits
    bus = Bus(fake=True)
    dropped = []
    app = make_app("dropoldx", command_handler=lambda b, c: None, bus=bus,
                   poll_block_ms=50,
                   on_dropped=lambda b, c, why: dropped.append((c.type, why)))
    bus.enqueue_command("cmd:dropoldx", {"type": "paper_create", "args": {},
                                         "ts": _old_ts(service_limits.replay_max_sec() + 60)})
    _run_until(bus, app, lambda: dropped)
    assert dropped == [("paper_create", "expired")]


def test_a_failing_dropped_callback_never_stops_the_consumer():
    bus = Bus(fake=True)
    bus.enqueue_command("cmd:dropbadx", {"type": "a", "args": {}})
    bus.consume_commands("cmd:dropbadx", group="dropbadx-svc", consumer="c1", block_ms=50)
    seen = []

    def boom(b, c, why):
        raise RuntimeError("callback failed")

    app = make_app("dropbadx", command_handler=lambda b, c: seen.append(c.type),
                   bus=bus, poll_block_ms=50, on_dropped=boom)
    bus.enqueue_command("cmd:dropbadx", {"type": "marker", "args": {}})
    _run_until(bus, app, lambda: seen)
    assert seen == ["marker"]
