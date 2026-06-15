from fastapi.testclient import TestClient
from services._scaffold import make_app
from shared.bus import Bus


def test_health():
    app = make_app("sentiment")
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"domain": "sentiment", "up": True}


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
