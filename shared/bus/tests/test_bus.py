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


def test_cache_set_writes_ts_side_key_matching_envelope():
    b = Bus(fake=True)
    b.cache_set("cache:test:m", {"n": 1})
    env = b.cache_get("cache:test:m")
    assert b._r.get("cache:test:m:ts") == env.ts  # tiny side key == envelope ts


def test_cache_metas_pipelined_batch():
    b = Bus(fake=True)
    b.cache_set("cache:ma", {"x": 1})
    b.cache_set("cache:mb", {"x": 1})
    b.cache_set("cache:mb", {"x": 2})
    out = b.cache_metas(["cache:ma", "cache:mb", "cache:absent"])
    env_a = b.cache_get("cache:ma")
    env_b = b.cache_get("cache:mb")
    assert out["cache:ma"] == (1, env_a.ts)
    assert out["cache:mb"] == (2, env_b.ts)
    assert out["cache:absent"] == (None, None)


def test_cache_metas_empty():
    assert Bus(fake=True).cache_metas([]) == {}


def test_cache_set_skip_unchanged_still_refreshes_the_freshness_stamp():
    """A skipped write means "checked, nothing changed" — NOT "nothing happened".

    This test previously asserted the opposite, which is how the bug shipped:
    freshness read the frozen stamp and reported a healthy publisher as dead
    whenever its data legitimately stopped moving. Measured in prod on a weekend,
    cache:market:dashboard showed 18h stale while market_svc was polling fine, and
    every skip_unchanged view has the same exposure.
    """
    b = Bus(fake=True)
    b.cache_set("cache:test:mt", {"n": 1})
    ts1 = b._r.get("cache:test:mt:ts")
    b.cache_set("cache:test:mt", {"n": 1}, skip_unchanged=True)
    ts2 = b._r.get("cache:test:mt:ts")
    assert ts2 is not None and ts2 >= ts1, "the stamp must move forward, not freeze"
    # The ENVELOPE's own ts is the other question — "when did it last CHANGE" —
    # and must NOT be rewritten, or the two stamps stop being distinguishable.
    assert b.cache_get("cache:test:mt").ts == ts1


def test_cache_set_skip_unchanged_leaves_the_version_alone_while_stamping():
    """The stamp refresh must not defeat what skip_unchanged is FOR: the version
    is what GUI pollers watch, and bumping it would repaint every open tab."""
    b = Bus(fake=True)
    b.cache_set("cache:test:mtv", {"n": 1})
    ver_before = b._r.get("cache:test:mtv:ver")
    b.cache_set("cache:test:mtv", {"n": 1}, skip_unchanged=True)
    assert b._r.get("cache:test:mtv:ver") == ver_before


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
    b.enqueue_command("cmd:strand", {"type": "paper_create", "args": {}})
    read = b.consume_commands("cmd:strand", group="g", consumer="dead-c", block_ms=50)
    assert len(read) == 1  # now pending, un-acked

    moved = b.drain_pending("cmd:strand", group="g", consumer="new-c")
    assert moved == 1
    dead = b._r.lrange("cmd:strand:dead", 0, -1)
    assert len(dead) == 1
    rec = json.loads(dead[0])
    assert "paper_create" in rec["fields"]["data"]
    assert rec["reason"].startswith("stranded")
    # PEL is now empty — nothing stuck, nothing auto-re-executed.
    assert b._r.xpending("cmd:strand", "g")["pending"] == 0


def test_drain_pending_noop_when_nothing_stranded():
    b = Bus(fake=True)
    # group exists but no pending entries
    b.consume_commands("cmd:clean", group="g", consumer="c", block_ms=10)
    assert b.drain_pending("cmd:clean", group="g", consumer="c") == 0


def test_cache_set_ttl_expires_the_payload_and_its_side_keys():
    """Per-position rescue boards accumulate one key per rescued trade FOREVER
    (37 in prod, ~150 KB, incl. boards for long-closed trades) and the bus has no
    delete API. A board is stale within minutes, so it is written with a TTL —
    which must cover the :ver and :ts side keys too, or they outlive the payload
    as orphan counters (2026-08-20)."""
    b = Bus(fake=True)
    b.cache_set("cache:test:ttl", {"n": 1}, ttl=90)
    for suffix in ("", ":ver", ":ts"):
        assert 0 < b._r.ttl("cache:test:ttl" + suffix) <= 90, suffix


def test_cache_set_without_ttl_persists():
    b = Bus(fake=True)
    b.cache_set("cache:test:forever", {"n": 1})
    assert b._r.ttl("cache:test:forever") == -1        # no expiry


def test_cache_set_ttl_refreshes_on_rewrite():
    """A board that is still being republished must not expire under the page."""
    b = Bus(fake=True)
    b.cache_set("cache:test:ttl2", {"n": 1}, ttl=60)
    b._r.expire("cache:test:ttl2", 5)                  # simulate near-expiry
    b.cache_set("cache:test:ttl2", {"n": 2}, ttl=60)
    assert b._r.ttl("cache:test:ttl2") > 5


# --- SE-04: a URL that names its own credential never gets the admin password --

def test_a_url_with_its_own_credential_is_not_given_the_admin_password():
    from shared.bus import client
    assert client.env_password("redis://live:pw@h:6379/0", {"MEMURAI_PASSWORD": "admin"}) is None
    assert client.env_password("redis://live@h:6379/0", {"MEMURAI_PASSWORD": "admin"}) is None
    assert client.env_password("redis://:pw@h:6379/0", {"MEMURAI_PASSWORD": "admin"}) is None


def test_a_bare_url_still_authenticates_with_the_stack_password():
    from shared.bus import client
    assert client.env_password("redis://h:6379/0", {"MEMURAI_PASSWORD": "admin"}) == "admin"
    assert client.env_password("redis://h:6379/0", {}) is None
    assert client.env_password("redis://h:6379/0", {"MEMURAI_PASSWORD": ""}) is None


# --- AR-07: the dead-letter list is bounded and can be counted ----------------

def test_the_dead_letter_list_keeps_only_the_newest_entries(monkeypatch):
    from shared import service_limits
    monkeypatch.setattr(service_limits, "dead_letter_keep", lambda: 5)
    bus = Bus(fake=True)
    for i in range(12):
        bus.dead_letter("cmd:capx", {"data": str(i)}, "handler raised")
    assert bus.dead_letter_len("cmd:capx") == 5
    import json
    kept = [json.loads(r)["fields"]["data"] for r in bus._r.lrange("cmd:capx:dead", 0, -1)]
    assert kept == ["7", "8", "9", "10", "11"]


def test_an_empty_or_missing_dead_letter_list_counts_zero():
    bus = Bus(fake=True)
    assert bus.dead_letter_len("cmd:nothing") == 0


def test_draining_hands_each_stranded_command_to_the_caller():
    bus = Bus(fake=True)
    bus.enqueue_command("cmd:strx", {"type": "paper_create", "args": {"qty": 1}})
    assert len(bus.consume_commands("cmd:strx", group="strx-svc", consumer="c1",
                                    block_ms=50)) == 1          # read, never acked
    seen = []
    moved = bus.drain_pending("cmd:strx", "strx-svc", "c1", on_entry=seen.append)
    assert moved == 1
    assert len(seen) == 1 and "paper_create" in seen[0]["data"]


def test_a_callback_that_raises_does_not_stop_the_drain():
    bus = Bus(fake=True)
    for _ in range(3):
        bus.enqueue_command("cmd:strx2", {"type": "x", "args": {}})
    bus.consume_commands("cmd:strx2", group="g", consumer="c1", block_ms=50)

    def boom(fields):
        raise RuntimeError("callback failed")

    assert bus.drain_pending("cmd:strx2", "g", "c1", on_entry=boom) == 3
    assert bus._r.xpending("cmd:strx2", "g")["pending"] == 0


# --- AR-12: a consumer survives its group being flushed away -------------------

def test_a_consumer_recreates_its_group_after_a_flush():
    """After FLUSHDB the stream and its group are gone. Every read then failed
    with NOGROUP, forever, until the service was restarted."""
    bus = Bus(fake=True)
    bus.enqueue_command("cmd:flushx", {"type": "one", "args": {}})
    assert [c.type for _, c in bus.consume_commands("cmd:flushx", "g", "c1")] == ["one"]
    bus._r.flushdb()
    bus.enqueue_command("cmd:flushx", {"type": "two", "args": {}})
    got = bus.consume_commands("cmd:flushx", "g", "c1")
    assert [c.type for _, c in got] == ["two"]


def test_a_read_error_that_is_not_a_missing_group_still_raises():
    bus = Bus(fake=True)

    def boom(**kw):
        raise RuntimeError("connection lost")

    bus._ensure_group("cmd:errx", "g")
    bus._r.xreadgroup = boom
    import pytest
    with pytest.raises(RuntimeError):
        bus.consume_commands("cmd:errx", "g", "c1")


def test_persistence_names_what_redis_is_configured_to_keep():
    class R:
        def __init__(self, save, aof):
            self._save, self._aof = save, aof

        def config_get(self, key):
            return {"save": {"save": self._save}, "appendonly": {"appendonly": self._aof}}[key]

    bus = Bus(fake=True)
    bus._r = R("3600 1 300 100 60 10000", "no")
    assert bus.persistence() == "snapshots"
    bus._r = R("", "yes")
    assert bus.persistence() == "append-only file"
    bus._r = R("3600 1", "yes")
    assert bus.persistence() == "append-only file"
    bus._r = R("", "no")
    assert bus.persistence() == "none"


def test_persistence_is_unknown_when_redis_will_not_say():
    class R:
        def config_get(self, key):
            raise RuntimeError("NOPERM")

    bus = Bus(fake=True)
    bus._r = R()
    assert bus.persistence() == "unknown"


# --- the unchanged check reads a signature, not the payload (audit PF-06) -----
# ``skip_unchanged`` fetched and parsed the whole stored payload to compare it.
# For the gamma history keys that is about 1.3 MB per key per minute, read back
# only to find, during collection, that it had changed.

def _gets(bus):
    """Record every key this bus GETs, alone or in a pipeline."""
    seen = []
    real_get, real_pipeline = bus._r.get, bus._r.pipeline

    def get(key, *a, **k):
        seen.append(key)
        return real_get(key, *a, **k)

    def pipeline(*a, **k):
        pipe = real_pipeline(*a, **k)
        pipe_get = pipe.get

        def recorded(key, *aa, **kk):
            seen.append(key)
            return pipe_get(key, *aa, **kk)

        pipe.get = recorded
        return pipe

    bus._r.get, bus._r.pipeline = get, pipeline
    return seen


def test_an_unchanged_write_does_not_read_the_payload_back():
    b = Bus(fake=True)
    b.cache_set("cache:test:sig", {"rows": [1, 2, 3]}, skip_unchanged=True)
    seen = _gets(b)
    v = b.cache_set("cache:test:sig", {"rows": [1, 2, 3]}, skip_unchanged=True)
    assert v == 1
    assert "cache:test:sig" not in seen, "the stored payload was fetched to compare"


def test_a_changed_write_does_not_read_the_payload_back_either():
    b = Bus(fake=True)
    b.cache_set("cache:test:sig2", {"rows": [1]}, skip_unchanged=True)
    seen = _gets(b)
    assert b.cache_set("cache:test:sig2", {"rows": [1, 2]}, skip_unchanged=True) == 2
    assert "cache:test:sig2" not in seen
    assert b.cache_get("cache:test:sig2").payload == {"rows": [1, 2]}


def test_rows_of_tuples_are_unchanged_when_stored_as_the_same_json():
    # A tuple is stored as a JSON list, so the parsed payload never equalled the
    # one being written and a payload of tuple rows was rewritten every time.
    b = Bus(fake=True)
    payload = {"rows": [(1, 2.5, None), (2, 3.5, "x")]}
    assert b.cache_set("cache:test:tup", payload, skip_unchanged=True) == 1
    assert b.cache_set("cache:test:tup", payload, skip_unchanged=True) == 1


def test_a_plain_write_in_between_does_not_leave_a_stale_signature():
    b = Bus(fake=True)
    b.cache_set("cache:test:mix", {"n": 1}, skip_unchanged=True)
    b.cache_set("cache:test:mix", {"n": 2})                     # no signature kept
    v = b.cache_set("cache:test:mix", {"n": 1}, skip_unchanged=True)
    assert v == 3 and b.cache_get("cache:test:mix").payload == {"n": 1}


def test_a_key_written_before_signatures_existed_is_still_compared():
    b = Bus(fake=True)
    b.cache_set("cache:test:old", {"n": 1}, skip_unchanged=True)
    b._r.delete("cache:test:old:sig")                # as an older build left it
    assert b.cache_set("cache:test:old", {"n": 1}, skip_unchanged=True) == 1
    # ... and it has a signature from then on.
    seen = _gets(b)
    assert b.cache_set("cache:test:old", {"n": 1}, skip_unchanged=True) == 1
    assert "cache:test:old" not in seen


def test_a_signature_whose_payload_is_gone_does_not_skip_the_write():
    b = Bus(fake=True)
    b.cache_set("cache:test:gone", {"n": 1}, skip_unchanged=True)
    b._r.delete("cache:test:gone")                   # expired, or flushed by hand
    b.cache_set("cache:test:gone", {"n": 1}, skip_unchanged=True)
    assert b.cache_get("cache:test:gone").payload == {"n": 1}


def test_the_signature_expires_with_the_rest_of_the_key():
    b = Bus(fake=True)
    b.cache_set("cache:test:sigttl", {"n": 1}, skip_unchanged=True, ttl=90)
    assert 0 < b._r.ttl("cache:test:sigttl:sig") <= 90
    b._r.persist("cache:test:sigttl:sig")
    b.cache_set("cache:test:sigttl", {"n": 1}, skip_unchanged=True, ttl=90)   # skipped
    assert 0 < b._r.ttl("cache:test:sigttl:sig") <= 90


def test_a_payload_that_cannot_be_signed_is_still_written():
    b = Bus(fake=True)
    payload = {"n": 1}
    import shared.bus.client as client
    real = client._signature
    client._signature = lambda p: None
    try:
        assert b.cache_set("cache:test:nosig", payload, skip_unchanged=True) == 1
        assert b.cache_set("cache:test:nosig", payload, skip_unchanged=True) == 1
        assert b.cache_set("cache:test:nosig", {"n": 2}, skip_unchanged=True) == 2
    finally:
        client._signature = real
