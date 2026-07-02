import json

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


def test_cache_version_reads_counter_without_payload():
    b = Bus(fake=True)
    assert b.cache_version("cache:test:v") is None  # absent
    b.cache_set("cache:test:v", {"big": "payload"})
    b.cache_set("cache:test:v", {"big": "payload2"})
    assert b.cache_version("cache:test:v") == 2  # matches envelope version


def test_cache_versions_pipelined_batch():
    b = Bus(fake=True)
    b.cache_set("cache:a", {"x": 1})
    b.cache_set("cache:b", {"x": 1})
    b.cache_set("cache:b", {"x": 2})
    out = b.cache_versions(["cache:a", "cache:b", "cache:missing"])
    assert out == {"cache:a": 1, "cache:b": 2, "cache:missing": None}


def test_cache_versions_empty():
    assert Bus(fake=True).cache_versions([]) == {}


def test_consume_creates_group_once(monkeypatch):
    b = Bus(fake=True)
    calls = {"n": 0}
    orig = b._r.xgroup_create

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(b._r, "xgroup_create", counting)
    b.consume_commands("cmd:once", "g", "c", block_ms=10)
    b.consume_commands("cmd:once", "g", "c", block_ms=10)
    assert calls["n"] == 1  # group ensured once, not per poll


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


# --- A2: command-stream hygiene ------------------------------------------


def test_enqueue_bounds_stream_length():
    """XADD is capped so cmd:* streams cannot grow without bound."""
    from shared.bus import client

    b = Bus(fake=True)
    n = client._XADD_MAXLEN + 200
    for i in range(n):
        b.enqueue_command("cmd:cap", {"type": "x", "args": {"i": i}})
    length = b._r.xlen("cmd:cap")
    # approximate trimming keeps roughly maxlen (never unbounded, never > enqueued).
    assert length <= n
    assert length <= client._XADD_MAXLEN + 50  # fakeredis trims exactly to maxlen


def test_dead_letter_pushes_raw_and_records_reason():
    b = Bus(fake=True)
    b.dead_letter("cmd:dl", {"data": '{"type": "boom"}'}, "handler raised")
    items = b._r.lrange("cmd:dl:dead", 0, -1)
    assert len(items) == 1
    rec = json.loads(items[0])
    assert rec["reason"] == "handler raised"
    assert rec["fields"] == {"data": '{"type": "boom"}'}
    assert "ts" in rec


def test_undecodable_entry_dead_letters_and_batch_continues():
    """A poison stream entry is dead-lettered + ack'd; good entries still return."""
    b = Bus(fake=True)
    # Group must exist before the manual XADD so it's delivered to the group.
    b.consume_commands("cmd:poison", group="g", consumer="c", block_ms=10)
    b._r.xadd("cmd:poison", {"data": "NOT VALID JSON {{{"})  # poison
    b.enqueue_command("cmd:poison", {"type": "good", "args": {}})

    out = b.consume_commands("cmd:poison", group="g", consumer="c", block_ms=50)
    # only the decodable command comes back
    assert [c.type for _id, c in out] == ["good"]
    # poison landed in the dead-letter list
    dead = b._r.lrange("cmd:poison:dead", 0, -1)
    assert len(dead) == 1 and json.loads(dead[0])["reason"].startswith("decode")
    # poison is NOT stuck in the PEL (it was ack'd)
    b.ack("cmd:poison", "g", out[0][0])
    pending = b._r.xpending("cmd:poison", "g")
    assert pending["pending"] == 0


def test_drain_pending_moves_stranded_entries_to_dead_letter():
    """A prior consumer's un-acked PEL entry is drained to dead-letter, not re-run."""
    b = Bus(fake=True)
    # First consumer reads a command then "crashes" without acking.
    b.enqueue_command("cmd:strand", {"type": "driver_paper_create", "args": {}})
    read = b.consume_commands("cmd:strand", group="g", consumer="dead-c", block_ms=50)
    assert len(read) == 1  # now pending, un-acked

    moved = b.drain_pending("cmd:strand", group="g", consumer="new-c")
    assert moved == 1
    dead = b._r.lrange("cmd:strand:dead", 0, -1)
    assert len(dead) == 1
    rec = json.loads(dead[0])
    assert "driver_paper_create" in rec["fields"]["data"]
    assert rec["reason"].startswith("stranded")
    # PEL is now empty — nothing stuck, nothing auto-re-executed.
    assert b._r.xpending("cmd:strand", "g")["pending"] == 0


def test_drain_pending_noop_when_nothing_stranded():
    b = Bus(fake=True)
    # group exists but no pending entries
    b.consume_commands("cmd:clean", group="g", consumer="c", block_ms=10)
    assert b.drain_pending("cmd:clean", group="g", consumer="c") == 0
