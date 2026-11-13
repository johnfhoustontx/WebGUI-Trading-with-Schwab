from fastapi.testclient import TestClient


def test_health_answers():
    from services.xfeed_svc.app import app
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["domain"] == "xfeed"


def test_the_service_port_is_configured():
    from repo_paths import SERVICE_PORTS
    assert SERVICE_PORTS["xfeed"] == 8219


def _wait_for(pred, timeout=3.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_a_command_waiting_on_a_polled_accounts_lock_never_holds_up_a_query():
    """remove_account / set_group take the account's lock, which the poller holds
    for a whole poll: on the stream lane every query from every tab queued behind
    them and timed out (final review A4). They run on the slow lane now."""
    import uuid

    from services._scaffold import make_app
    from services.xfeed_svc import handlers, runtime
    from services.xfeed_svc.poller import Poller
    from services.xfeed_svc.store import Store
    from shared.bus.client import Bus, reset_fake_bus

    reset_fake_bus()
    bus = Bus(fake=True)
    store = Store(":memory:")
    store.upsert_account("acct", display_name="A", avatar_url=None, now=0)
    rt = runtime.Runtime(store, None, Poller(store, None))
    runtime.set_for_tests(rt)
    lock = rt.poller.account_lock("acct")
    lock.acquire()                                    # a poll of @acct is in progress
    ids = {k: uuid.uuid4().hex for k in ("set", "remove", "query")}

    def answer(k):
        env = bus.cache_get(handlers.answer_key(ids[k]))
        return env.payload if env is not None else None

    try:
        app = make_app("xfeedlanex", command_handler=handlers.handle_command,
                       slow_commands=handlers.SLOW, bus=bus, poll_block_ms=50)
        bus.enqueue_command("cmd:xfeedlanex", {"type": "set_group",
                                               "args": {"request_id": ids["set"], "handle": "acct", "group": "g"}})
        bus.enqueue_command("cmd:xfeedlanex", {"type": "remove_account",
                                               "args": {"request_id": ids["remove"], "handle": "acct"}})
        bus.enqueue_command("cmd:xfeedlanex", {"type": "query", "args": {"request_id": ids["query"]}})
        with TestClient(app):
            try:
                assert _wait_for(lambda: answer("query") is not None, timeout=2.0), \
                    "the query waited behind a command blocked on the account lock"
                assert answer("set") is None and answer("remove") is None
            finally:
                lock.release()
            assert _wait_for(lambda: answer("remove") is not None)
        assert answer("query")["ok"] is True
        assert answer("set")["ok"] is True
        assert answer("remove")["ok"] is True
        assert store.get_account("acct")["active"] is False
    finally:
        runtime.set_for_tests(None)
