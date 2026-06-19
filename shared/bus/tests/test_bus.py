from shared.bus import Bus


def test_cache_set_get_bumps_version():
    b = Bus(fake=True)
    v1 = b.cache_set("cache:test:x", {"n": 1})
    assert v1 == 1
    env = b.cache_get("cache:test:x")
    assert env.version == 1 and env.payload == {"n": 1}
    assert isinstance(env.ts, str) and env.ts  # ISO timestamp present
    v2 = b.cache_set("cache:test:x", {"n": 2})
    assert v2 == 2
    assert b.cache_get("cache:test:x").payload == {"n": 2}


def test_cache_get_missing_returns_none():
    b = Bus(fake=True)
    assert b.cache_get("cache:test:absent") is None


def test_cache_set_skip_unchanged_does_not_bump_version():
    b = Bus(fake=True)
    v1 = b.cache_set("cache:test:s", {"n": 1})
    v2 = b.cache_set("cache:test:s", {"n": 1}, skip_unchanged=True)
    assert v1 == 1 and v2 == 1  # identical payload -> no INCR
    assert b.cache_get("cache:test:s").version == 1


def test_cache_set_skip_unchanged_bumps_when_changed():
    b = Bus(fake=True)
    b.cache_set("cache:test:s2", {"n": 1})
    v2 = b.cache_set("cache:test:s2", {"n": 2}, skip_unchanged=True)
    assert v2 == 2
    assert b.cache_get("cache:test:s2").payload == {"n": 2}


def test_cache_set_skip_unchanged_writes_when_absent():
    b = Bus(fake=True)
    v = b.cache_set("cache:test:s3", {"n": 1}, skip_unchanged=True)
    assert v == 1
    assert b.cache_get("cache:test:s3").payload == {"n": 1}


def test_cache_set_publishes_event_on_change():
    b = Bus(fake=True)
    sub = b.subscribe("events:test:e")
    v = b.cache_set("cache:test:e", {"n": 1}, event="events:test:e")
    msg = sub.get_message(timeout=1.0)
    sub.close()
    assert msg == {"version": v}


def test_cache_set_skip_unchanged_does_not_publish():
    b = Bus(fake=True)
    b.cache_set("cache:test:e2", {"n": 1}, event="events:test:e2", skip_unchanged=True)
    sub = b.subscribe("events:test:e2")  # subscribe AFTER the first (changed) publish
    v = b.cache_set("cache:test:e2", {"n": 1}, event="events:test:e2", skip_unchanged=True)
    idle = sub.get_message(timeout=0.1)
    sub.close()
    assert v == 1 and idle is None  # unchanged -> neither INCR nor publish


def test_publish_subscribe_roundtrip():
    b = Bus(fake=True)
    sub = b.subscribe("events:test:x")
    b.publish("events:test:x", {"version": 5})
    msg = sub.get_message(timeout=1.0)
    assert msg == {"version": 5}


def test_command_stream_enqueue_consume_ack():
    b = Bus(fake=True)
    b.enqueue_command("cmd:test", {"type": "rescan", "args": {}})
    cmds = b.consume_commands("cmd:test", group="g", consumer="c", block_ms=50)
    assert len(cmds) == 1
    msg_id, command = cmds[0]
    assert command.type == "rescan"
    b.ack("cmd:test", "g", msg_id)
    # after ack, a fresh read for the same group returns no pending new messages
    assert b.consume_commands("cmd:test", group="g", consumer="c", block_ms=50) == []


def test_get_message_timeout_returns_none():
    b = Bus(fake=True)
    sub = b.subscribe("events:test:idle")
    assert sub.get_message(timeout=0.05) is None


def test_consume_twice_exercises_busygroup_branch():
    b = Bus(fake=True)
    b.enqueue_command("cmd:test2", {"type": "a"})
    first = b.consume_commands("cmd:test2", group="g", consumer="c", block_ms=50)
    assert len(first) == 1
    b.ack("cmd:test2", "g", first[0][0])
    # second call re-enters group creation (BUSYGROUP swallowed) and finds nothing new
    b.enqueue_command("cmd:test2", {"type": "b"})
    second = b.consume_commands("cmd:test2", group="g", consumer="c", block_ms=50)
    assert len(second) == 1 and second[0][1].type == "b"
