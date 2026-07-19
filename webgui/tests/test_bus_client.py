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
