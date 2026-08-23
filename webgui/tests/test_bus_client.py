"""Tests for webgui/bus_client.py — the GUI-side bus client.

Single-user, process-wide lazy Bus singleton. Under pytest the Bus auto-selects
fakeredis (PYTEST_CURRENT_TEST is set), so these need no live Memurai. The
singleton is what lets the EventListener thread and the test's publish share one
fakeredis instance — separate FakeStrictRedis objects do NOT share pub/sub state.
"""
import time

import bus_client


def setup_function(_fn):
    """Force a fresh fakeredis-backed Bus per test so state does not leak."""
    bus_client.reset()


def test_read_returns_payload_or_none():
    assert bus_client.read("sentiment:composite") is None

    bus_client.bus().cache_set("cache:sentiment:composite", {"live": {"x": 1}})

    assert bus_client.read("sentiment:composite") == {"live": {"x": 1}}
    assert bus_client.read_version("sentiment:composite") == 1


def test_read_full_returns_payload_and_version():
    assert bus_client.read_full("driver:autonomous") == (None, None)
    bus_client.bus().cache_set("cache:driver:autonomous", {"enabled": True})
    assert bus_client.read_full("driver:autonomous") == ({"enabled": True}, 1)


def test_read_versions_batches_views():
    bus_client.bus().cache_set("cache:options:gamma", {"x": 1})
    bus_client.bus().cache_set("cache:options:gex_status", {"x": 1})
    bus_client.bus().cache_set("cache:options:gex_status", {"x": 2})
    out = bus_client.read_versions(
        ["options:gamma", "options:gex_status", "options:absent"])
    assert out == {"options:gamma": 1, "options:gex_status": 2, "options:absent": None}


def test_read_metas_batches_version_and_ts():
    bus_client.bus().cache_set("cache:options:scan", {"signals": []})
    env = bus_client.bus().cache_get("cache:options:scan")
    out = bus_client.read_metas(["options:scan", "options:absent"])
    assert out["options:scan"] == (1, env.ts)
    assert out["options:absent"] == (None, None)


def test_read_metas_falls_back_to_envelope_for_pre_upgrade_keys():
    """A key written before the :ts side key existed has a version but no :ts —
    read_metas must fall back to the envelope so freshness stays correct."""
    b = bus_client.bus()
    b.cache_set("cache:sentiment:composite", {"live": {}})
    b._r.delete("cache:sentiment:composite:ts")  # simulate a pre-upgrade write
    env = b.cache_get("cache:sentiment:composite")
    out = bus_client.read_metas(["sentiment:composite"])
    assert out["sentiment:composite"] == (1, env.ts)


def test_request_enqueues_command():
    bus_client.request("sentiment", {"type": "refresh"})

    cmds = bus_client.bus().consume_commands(
        "cmd:sentiment", group="g", consumer="c", block_ms=50
    )
    assert cmds[0][1].type == "refresh"


def test_on_event_fires_callback_on_publish():
    got = []
    listener = bus_client.on_event(
        "events:sentiment:composite", lambda v: got.append(v)
    )
    try:
        # Give the daemon thread a moment to subscribe before publishing.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not listener.subscribed:
            time.sleep(0.02)

        bus_client.bus().publish("events:sentiment:composite", {"version": 7})

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not got:
            time.sleep(0.02)

        assert got == [7]
    finally:
        listener.stop()


# ── version-gated reads (2026-08-20) ───────────────────────────────────────
# The app-wide 2 s watcher read options:scan (148 KB) + options:flow_alerts
# (90 KB) UNCONDITIONALLY on every tick, per open tab: ~43,200 ticks/day x
# 237 KB is ~10 GB/day/tab of transfer + JSON parse for data that changes a
# handful of times an hour. read_gated pays a tiny :ver probe instead.

def test_read_gated_returns_payload_and_changed_on_first_read():
    memo = {}
    bus_client.bus().cache_set("cache:options:scan", {"signals": [1, 2]})
    payload, changed = bus_client.read_gated("options:scan", memo)
    assert payload == {"signals": [1, 2]} and changed is True


def test_read_gated_serves_the_memo_while_the_version_holds():
    memo = {}
    b = bus_client.bus()
    b.cache_set("cache:options:scan", {"signals": [1]})
    first, _ = bus_client.read_gated("options:scan", memo)
    calls = {"n": 0}
    real = b.cache_get

    def _counting(key, *a, **k):
        calls["n"] += 1
        return real(key, *a, **k)

    b.cache_get = _counting
    for _ in range(5):
        payload, changed = bus_client.read_gated("options:scan", memo)
        assert payload is first          # same object, no re-deserialize
        assert changed is False
    assert calls["n"] == 0               # only :ver probes, no payload GETs


def test_read_gated_rereads_when_the_version_moves():
    memo = {}
    b = bus_client.bus()
    b.cache_set("cache:options:scan", {"signals": [1]})
    bus_client.read_gated("options:scan", memo)
    b.cache_set("cache:options:scan", {"signals": [1, 2]})
    payload, changed = bus_client.read_gated("options:scan", memo)
    assert payload == {"signals": [1, 2]} and changed is True


def test_read_gated_on_an_absent_view_reads_through():
    """An absent view has no version, so there is nothing to gate on — it reads
    through rather than memoizing an absence it could never invalidate."""
    memo = {}
    for _ in range(3):
        payload, changed = bus_client.read_gated("options:absent", memo)
        assert payload is None and changed is True


def test_read_gated_never_caches_a_key_that_has_no_version_counter():
    """`cache_set` always INCRs {key}:ver, but a pre-upgrade key can carry a
    payload with no counter. A memo keyed on None has no invalidation signal, so
    it would serve that first payload forever — such a view reads through."""
    seen = {"n": 0}

    def _fake_read(view):
        seen["n"] += 1
        return {"legacy": True}

    real_read, real_ver = bus_client.read, bus_client.read_version
    bus_client.read = _fake_read
    bus_client.read_version = lambda view: None
    try:
        memo = {}
        payload, changed = bus_client.read_gated("options:legacy", memo)
        assert payload == {"legacy": True} and changed is True
        assert seen["n"] == 1
        # ...and it keeps reading, so a later change to a versionless key is seen
        payload, changed = bus_client.read_gated("options:legacy", memo)
        assert payload == {"legacy": True} and changed is True
        assert seen["n"] == 2
    finally:
        bus_client.read, bus_client.read_version = real_read, real_ver


def test_read_gated_recovers_when_an_absent_view_appears():
    memo = {}
    bus_client.read_gated("options:scan", memo)
    bus_client.bus().cache_set("cache:options:scan", {"signals": [9]})
    payload, changed = bus_client.read_gated("options:scan", memo)
    assert payload == {"signals": [9]} and changed is True
