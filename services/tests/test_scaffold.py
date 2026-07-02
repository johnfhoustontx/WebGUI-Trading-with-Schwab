import logging
import time

from fastapi.testclient import TestClient
from services._scaffold import make_app
from shared.bus import Bus


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


def test_scheduler_exception_swallowed():
    """A scheduler that raises must not crash app startup/shutdown."""
    bus = Bus(fake=True)

    async def sched(b):
        raise RuntimeError("scheduler boom")

    app = make_app("boomsched", scheduler=sched, bus=bus)
    # Entering/exiting the context (startup+shutdown) must not raise.
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_scheduler_runs():
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
    bus.enqueue_command("cmd:drainx", {"type": "driver_paper_create", "args": {}})
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
    assert "driver_paper_create" in json.loads(dead[0])["fields"]["data"]
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


def test_dead_scheduler_restarted_and_health_reflects_it():
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


def test_scheduler_permanently_dead_when_backoff_budget_exhausted():
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
        assert body["up"] is True  # process still up
        assert body["scheduler_alive"] is False  # but scheduler is dead
        assert body["scheduler_restarts"] == 3


def test_healthy_scheduler_not_restarted_and_alive():
    """(R2) A healthy scheduler (internal infinite loop) is NOT restarted; it
    stays alive with zero restarts, and last_tick_age stays small."""
    import asyncio

    bus = Bus(fake=True)
    started = []

    async def healthy(b):
        started.append(1)
        while True:
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
