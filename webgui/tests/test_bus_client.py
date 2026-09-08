"""Tests for webgui/bus_client.py — the GUI-side bus client.

Single-user, process-wide lazy Bus singleton. Under pytest the Bus auto-selects
fakeredis (PYTEST_CURRENT_TEST is set), so these need no live Memurai. The
singleton is what lets the EventListener thread and the test's publish share one
fakeredis instance — separate FakeStrictRedis objects do NOT share pub/sub state.
"""
import time

import pytest

import bus_client


def setup_function(_fn):
    """Force a fresh fakeredis-backed Bus per test so state does not leak."""
    bus_client.reset()


def teardown_function(_fn):
    """Restore the process-wide configuration the read-only tests mutate.

    ``_read_only`` and ``_url`` are module globals that ``reset()`` deliberately
    does NOT clear, so a test that set one and then failed mid-body would leak it
    into every later test in the session — as a refused ``request`` somewhere
    that looks nothing like the cause. Teardown runs even when the test body
    raises, which is what makes this tighter than a per-test try/finally.
    """
    bus_client.set_read_only(False)
    bus_client.set_url(None)
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


# ── read-only mode + a pinned connection URL (2026-09-07) ──────────────────
# The public live process (live.neuralstrike.co) renders the SAME page modules
# the private app does, unauthenticated. `request` is the SINGLE Tier-1 write
# chokepoint, so refusing there is the backstop behind "the page draws no button
# that enqueues" — including for pages nobody has audited yet.

def test_read_only_refuses_every_command():
    """bus_client.request is the SINGLE Tier-1 write chokepoint, and on the
    published pages it reaches gamma_analyze and gamma_explain -- PAID Claude
    calls -- plus gamma_refresh and sentiment refresh, which fan out Schwab
    fetches against a budget already running 68-76k/day. Unauthenticated and
    unrefused, that is an open tap on money."""
    bus_client.set_read_only(True)
    with pytest.raises(PermissionError):
        bus_client.request("options", {"type": "gamma_analyze"})


def test_read_only_refuses_rather_than_no_ops():
    """It must RAISE, never return a plausible id. A silent no-op is worse than
    the enqueue: the caller believes its command is queued and waits for a
    result that will never arrive."""
    bus_client.set_read_only(True)
    with pytest.raises(PermissionError):
        bus_client.request("sentiment", {"type": "refresh"})
    # nothing reached the stream
    assert bus_client.bus().consume_commands(
        "cmd:sentiment", group="g", consumer="c", block_ms=50) == []


def test_the_refusal_never_raises_something_else():
    """The refusal must never be the thing that breaks. ``command.get('type')``
    assumes a dict; a caller passing anything else must still get the
    PermissionError, not an AttributeError from inside the guard."""
    bus_client.set_read_only(True)
    for bad in (None, "gamma_analyze", ["gamma_analyze"], 7):
        with pytest.raises(PermissionError):
            bus_client.request("options", bad)  # type: ignore[arg-type]


def test_reads_still_work_when_read_only():
    """Refusing writes must not refuse the reads the screens exist to do — so
    this drives EVERY read helper in this module with the flag on and checks the
    values, which is what catches a guard placed in ``bus()`` (or anywhere else
    shared) instead of in ``request``.

    What it does NOT prove: that the Redis ACL user the live process connects as
    permits these commands. Under pytest ``Bus`` is fakeredis with no ACL at all,
    so the STRUCTURAL half of read-only is unprovable here by construction — it
    is verified by connecting as that user, not by this test. Nor does it prove
    a live page renders; that is Task 7's route-render test."""
    b = bus_client.bus()
    b.cache_set("cache:options:scan", {"signals": [1, 2]})
    b.cache_set("cache:options:gamma", {"x": 1})
    env = b.cache_get("cache:options:scan")

    bus_client.set_read_only(True)

    assert bus_client.read("options:scan") == {"signals": [1, 2]}
    assert bus_client.read_full("options:scan") == ({"signals": [1, 2]}, 1)
    assert bus_client.read_version("options:scan") == 1
    assert bus_client.read_versions(["options:scan", "options:gamma"]) == {
        "options:scan": 1, "options:gamma": 1}
    assert bus_client.read_meta("options:scan") == (1, env.ts)
    assert bus_client.read_metas(["options:scan"])["options:scan"] == (1, env.ts)
    assert bus_client.read_gated("options:scan", {}) == ({"signals": [1, 2]}, True)
    assert bus_client.ping() is True


def test_the_event_subscription_still_fires_when_read_only():
    """A subscription is a READ — the live screens repaint off it. Nothing in the
    refusal may touch the subscribe path."""
    bus_client.set_read_only(True)
    got = []
    listener = bus_client.on_event("events:options:gamma", lambda v: got.append(v))
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not listener.subscribed:
            time.sleep(0.02)
        bus_client.bus().publish("events:options:gamma", {"version": 4})
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not got:
            time.sleep(0.02)
        assert got == [4]
    finally:
        listener.stop()


def test_a_connection_url_can_be_pinned():
    """The live process connects as a Redis ACL user with read commands only.
    Bus.__init__ already accepts a url, so this is a credential passed in, not a
    redesign. (Under pytest Bus short-circuits to fakeredis and ignores the url,
    so what is asserted is that the value is held for the next ``bus()`` — not
    that a connection is made with it.)"""
    bus_client.set_url("redis://live:secret@127.0.0.1:6379/0")
    assert bus_client._url == "redis://live:secret@127.0.0.1:6379/0"


def test_the_pinned_url_reaches_the_bus():
    """Holding the value is not the same as USING it — without this, ``bus()``
    could ignore ``_url`` entirely and the test above would still pass, so the
    live process would quietly connect as the read-write default user. Recording
    the constructor is the only way to see it here: under pytest ``Bus``
    short-circuits to fakeredis and drops the url on the floor."""
    seen = []

    class _Recorder:
        def __init__(self, fake: bool = False, url: str | None = None):
            seen.append(url)

    real_bus_cls = bus_client.Bus
    bus_client.Bus = _Recorder                      # type: ignore[misc]
    try:
        bus_client.set_url("redis://live:secret@127.0.0.1:6379/0")
        bus_client.reset()
        bus_client.bus()
        assert seen == ["redis://live:secret@127.0.0.1:6379/0"]

        bus_client.set_url(None)                    # unpinned -> Bus's own default
        bus_client.reset()
        bus_client.bus()
        assert seen[-1] is None
    finally:
        bus_client.Bus = real_bus_cls               # type: ignore[misc]
        bus_client.reset()                          # drop the recorder instance


def test_reset_leaves_the_process_configuration_alone():
    """``reset()`` drops the cached Bus — it is a CACHE helper, called by ~15
    other test modules. The read-only flag and the URL are process
    configuration set once at startup; clearing them here would silently
    re-arm writes on the live process the moment anything reset the bus."""
    bus_client.set_read_only(True)
    bus_client.set_url("redis://live:secret@127.0.0.1:6379/0")
    bus_client.reset()
    assert bus_client.is_read_only() is True
    assert bus_client._url == "redis://live:secret@127.0.0.1:6379/0"
