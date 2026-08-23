"""The fake bus must lie about NOTHING that matters, and prod has ONE Redis.

Every ``Bus(fake=True)`` used to build its own ``FakeStrictRedis``, so two Bus
objects in the same test shared nothing - while in production every Bus talks to
the same Memurai. That is not a harmless difference: several production modules
construct their OWN bus rather than receiving one
(``options_svc.compute._BRIEFING_BUS``, ``trade_svc.compute._BUS``,
``webgui/bus_client._bus``, ``_scaffold``'s ``the_bus or Bus()``), so a test that
hands a handler its own fake bus and then exercises code reaching one of those
singletons was reading an EMPTY cache and passing down the degrade path.

The fix keys one ``fakeredis.FakeServer`` per running test, so:

* every Bus inside one test shares state, exactly as prod does;
* each test still starts clean, with no conftest wiring in any of the ~15 test
  files that use the fake bus.
"""
import os

from shared.bus import Bus


def test_two_buses_in_one_test_share_state():
    a, b = Bus(fake=True), Bus(fake=True)
    a.cache_set("cache:probe:share", {"v": 1})
    env = b.cache_get("cache:probe:share")
    assert env is not None, "a second Bus must see the first Bus's write, as in prod"
    assert env.payload == {"v": 1}


def test_a_bus_built_without_fake_under_pytest_also_shares():
    """The production shape: code under test does Bus() with no argument, and
    under pytest that silently becomes a fake. It must be the SAME fake."""
    written = Bus(fake=True)
    written.cache_set("cache:probe:implicit", {"v": 2})
    assert Bus().cache_get("cache:probe:implicit").payload == {"v": 2}


def test_pubsub_crosses_bus_instances():
    """Publish/subscribe is the other half of prod semantics - a service
    publishes on its bus and the GUI listens on a different one."""
    sub_bus, pub_bus = Bus(fake=True), Bus(fake=True)
    sub = sub_bus.subscribe("events:probe:x")
    try:
        pub_bus.publish("events:probe:x", {"version": 9})
        assert sub.get_message(timeout=2.0) == {"version": 9}
    finally:
        sub.close()


def test_streams_cross_bus_instances():
    """Commands are the GUI->service path: enqueued on one bus, consumed on
    another."""
    producer, consumer = Bus(fake=True), Bus(fake=True)
    producer.enqueue_command("cmd:probe", {"type": "ping", "args": {}})
    got = list(consumer.consume_commands("cmd:probe", "g1", "c1", block_ms=50))
    assert [c.type for _mid, c in got] == ["ping"]


def test_state_does_not_leak_from_the_previous_test():
    """The other half of the contract. Every test above wrote to this same fake
    server; none of those keys may still be here."""
    leaked = [k for k in ("cache:probe:share", "cache:probe:implicit")
              if Bus(fake=True).cache_get(k) is not None]
    assert not leaked, f"state leaked across tests: {leaked}"


def test_the_test_key_is_what_scopes_the_server():
    """Documents the mechanism: pytest rewrites PYTEST_CURRENT_TEST per test, so
    the server key changes with it and no conftest wiring is needed."""
    assert "PYTEST_CURRENT_TEST" in os.environ
    assert "test_the_test_key_is_what_scopes_the_server" in \
        os.environ["PYTEST_CURRENT_TEST"]
