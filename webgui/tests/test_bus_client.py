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
    assert bus_client.read_full("trade:analysis") == (None, None)
    bus_client.bus().cache_set("cache:trade:analysis", {"enabled": True})
    assert bus_client.read_full("trade:analysis") == ({"enabled": True}, 1)


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

    def _fake_read_full(view):
        # A pre-upgrade envelope still embeds a version; only the :ver counter
        # is missing, so the probe below is what reads None.
        seen["n"] += 1
        return {"legacy": True}, 1

    real_read, real_ver = bus_client.read_full, bus_client.read_version
    bus_client.read_full = _fake_read_full
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
        bus_client.read_full, bus_client.read_version = real_read, real_ver


def test_read_gated_recovers_when_an_absent_view_appears():
    memo = {}
    bus_client.read_gated("options:scan", memo)
    bus_client.bus().cache_set("cache:options:scan", {"signals": [9]})
    payload, changed = bus_client.read_gated("options:scan", memo)
    assert payload == {"signals": [9]} and changed is True


# `Bus.cache_set` INCRs {key}:ver in one round-trip and SETs the envelope in a
# LATER pipeline. A reader landing in that gap sees the new version beside the
# old payload. Memoizing the PROBED version with that payload made the pair
# sticky: every later probe matched, so the stale payload was served until the
# next publish — a whole day for the nightly options:calibration view.

def _half_written_publish(b, key, payload):
    """Replay cache_set's two halves with a reader call in between: INCR first,
    return a callable that completes the envelope SET with that same version."""
    from shared.contracts.envelope import CacheEnvelope
    version = b._r.incr(f"{key}:ver")

    def _finish():
        env = CacheEnvelope(version=version, ts="2026-09-16T00:00:00+00:00",
                            payload=payload)
        b._r.set(key, env.to_json())
        b._r.set(f"{key}:ts", env.ts)
    return _finish


def test_read_gated_does_not_pin_an_old_payload_read_mid_publish():
    memo = {}
    b = bus_client.bus()
    b.cache_set("cache:options:calibration", {"gen": 1})
    bus_client.read_gated("options:calibration", memo)

    finish = _half_written_publish(b, "cache:options:calibration", {"gen": 2})
    mid, _ = bus_client.read_gated("options:calibration", memo)   # lands in the gap
    assert mid == {"gen": 1}                                        # old envelope, fine
    finish()

    payload, changed = bus_client.read_gated("options:calibration", memo)
    assert payload == {"gen": 2} and changed is True


def test_read_gated_does_not_pin_an_absence_read_mid_first_publish():
    memo = {}
    b = bus_client.bus()
    finish = _half_written_publish(b, "cache:options:calibration", {"gen": 1})
    mid, _ = bus_client.read_gated("options:calibration", memo)
    assert mid is None
    finish()

    payload, changed = bus_client.read_gated("options:calibration", memo)
    assert payload == {"gen": 1} and changed is True


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


# ── the public Strategy Finder's one write ──────────────────────────────────

def _public_stream_commands():
    from shared import public_scan
    return bus_client.bus().consume_commands(
        public_scan.STREAM, group="g", consumer="c", block_ms=50)


def test_a_public_scan_request_is_allowed_on_a_read_only_process():
    """The ONE enqueue the public origin may make. It must work with the
    read-only flag on, which is the only state that process is ever in."""
    from shared import public_scan
    bus_client.set_read_only(True)
    msg_id = bus_client.request_public_scan(" spy ")
    assert msg_id
    cmds = _public_stream_commands()
    assert len(cmds) == 1
    assert cmds[0][1].type == public_scan.COMMAND_TYPE
    assert cmds[0][1].args == {"symbol": "SPY"}


def test_a_public_scan_request_never_reaches_the_options_stream():
    bus_client.request_public_scan("SPY")
    assert bus_client.bus().consume_commands(
        "cmd:options", group="g", consumer="c", block_ms=50) == []


@pytest.mark.parametrize("bad", ["", None, "spy; flushall", "../x", "TOOLONGSYM"])
def test_an_invalid_symbol_is_refused_before_anything_is_written(bad):
    bus_client.set_read_only(True)
    with pytest.raises(ValueError):
        bus_client.request_public_scan(bad)
    assert _public_stream_commands() == []


def test_the_generic_request_path_stays_refused_for_the_public_stream():
    """The new function must not turn ``request`` into a loophole: the domain
    spelling of the same stream is still refused on a read-only process."""
    bus_client.set_read_only(True)
    with pytest.raises(PermissionError):
        bus_client.request("finder_public", {"type": "public_scan",
                                             "args": {"symbol": "SPY"}})
    assert _public_stream_commands() == []


def test_the_public_request_takes_one_argument_and_writes_one_place():
    """A visitor controls one string. The function must take nothing else from
    its caller (no stream, no type, no extra args), and its only write must be
    the command ``request_command`` built, onto ``public_scan.STREAM``."""
    import ast
    import inspect
    import textwrap
    fn = ast.parse(textwrap.dedent(inspect.getsource(
        bus_client.request_public_scan))).body[0]
    params = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    assert params == ["raw_symbol"] and not fn.args.vararg and not fn.args.kwarg
    writes = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "enqueue_command"]
    assert len(writes) == 1
    stream, command = writes[0].args
    assert ast.unparse(stream) == "public_scan.STREAM"
    assert ast.unparse(command) == "command"
    builds = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
              and ast.unparse(n.targets[0]) == "command"]
    assert [ast.unparse(b.value) for b in builds] == [
        "public_scan.request_command(raw_symbol)"]


# ── the public Rescue form's two writes ─────────────────────────────────────

import datetime as _dt

_EXP = (_dt.date.today() + _dt.timedelta(days=30)).isoformat()


def _rescue_stream_commands():
    from shared import public_rescue
    return bus_client.bus().consume_commands(
        public_rescue.STREAM, group="g", consumer="c", block_ms=50)


def _pcs(**over):
    spec = {"symbol": "spy", "strategy": "PCS", "short_strike": 500.0,
            "long_strike": 495.0, "expiration": _EXP, "quantity": 1,
            "entry_credit": 1.2}
    spec.update(over)
    return spec


def test_public_rescue_requests_are_allowed_on_a_read_only_process():
    from shared import public_rescue
    bus_client.set_read_only(True)
    assert bus_client.request_public_ladder(" spy ")
    assert bus_client.request_public_ladder("SPY", _EXP)
    assert bus_client.request_public_rescue(_pcs(position_id=9))
    cmds = [c for _id, c in _rescue_stream_commands()]
    assert [c.type for c in cmds] == [public_rescue.LADDER_TYPE,
                                      public_rescue.LADDER_TYPE,
                                      public_rescue.COMPUTE_TYPE]
    assert cmds[0].args == {"symbol": "SPY"}
    assert cmds[1].args == {"symbol": "SPY", "expiry": _EXP}
    assert "position_id" not in cmds[2].args["spec"]      # normalized, not the raw dict
    assert cmds[2].args["spec"]["symbol"] == "SPY"


def test_public_rescue_requests_never_reach_the_options_stream():
    bus_client.request_public_ladder("SPY")
    bus_client.request_public_rescue(_pcs())
    assert bus_client.bus().consume_commands(
        "cmd:options", group="g", consumer="c", block_ms=50) == []


@pytest.mark.parametrize("call", [
    lambda: bus_client.request_public_ladder("../x"),
    lambda: bus_client.request_public_ladder("SPY", "tomorrow"),
    lambda: bus_client.request_public_rescue(_pcs(short_strike=float("nan"))),
    lambda: bus_client.request_public_rescue(_pcs(strategy="COVERED_CALL")),
    lambda: bus_client.request_public_rescue("not a dict"),
])
def test_an_invalid_public_rescue_request_writes_nothing(call):
    bus_client.set_read_only(True)
    with pytest.raises(ValueError):
        call()
    assert _rescue_stream_commands() == []


def test_the_generic_request_path_stays_refused_for_the_rescue_stream():
    bus_client.set_read_only(True)
    with pytest.raises(PermissionError):
        bus_client.request("rescue_public", {"type": "public_rescue",
                                             "args": {"spec": _pcs()}})
    assert _rescue_stream_commands() == []


@pytest.mark.parametrize("fn, params, builder", [
    ("request_public_ladder", ["raw_symbol", "raw_expiry"],
     "public_rescue.ladder_command(raw_symbol, raw_expiry)"),
    ("request_public_rescue", ["raw_spec"],
     "public_rescue.compute_command(raw_spec)"),
])
def test_each_public_rescue_request_writes_one_command_to_one_place(fn, params, builder):
    """The caller controls the fields; never the stream, the command type, or
    anything written besides what the validator built."""
    import ast
    import inspect
    import textwrap
    node = ast.parse(textwrap.dedent(inspect.getsource(
        getattr(bus_client, fn)))).body[0]
    assert [a.arg for a in node.args.args + node.args.kwonlyargs] == params
    assert not node.args.vararg and not node.args.kwarg
    writes = [n for n in ast.walk(node) if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "enqueue_command"]
    assert len(writes) == 1
    stream, command = writes[0].args
    assert ast.unparse(stream) == "public_rescue.STREAM"
    assert ast.unparse(command) == "command"
    builds = [n for n in ast.walk(node) if isinstance(n, ast.Assign)
              and ast.unparse(n.targets[0]) == "command"]
    assert [ast.unparse(b.value) for b in builds] == [builder]


def test_the_public_origin_has_exactly_six_write_functions():
    """Every bus_client function that enqueues, other than ``request`` (which a
    read-only process refuses), is a public write. Adding a seventh must be a
    decision, made here."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(bus_client))
    writers = sorted(
        f.name for f in tree.body if isinstance(f, ast.FunctionDef)
        and any(isinstance(n, ast.Call) and getattr(n.func, "attr", None)
                == "enqueue_command" for n in ast.walk(f)))
    assert writers == ["request", "request_public_gamma", "request_public_ladder",
                       "request_public_math", "request_public_rescue",
                       "request_public_scan", "request_public_tool"]


# ── the public Gamma page's one write ───────────────────────────────────────

def _gamma_stream_commands():
    from shared import public_gamma
    return bus_client.bus().consume_commands(
        public_gamma.STREAM, group="g", consumer="c", block_ms=50)


def test_a_public_gamma_request_is_allowed_on_a_read_only_process():
    from shared import public_gamma
    bus_client.set_read_only(True)
    assert bus_client.request_public_gamma(" nvda ")
    cmds = _gamma_stream_commands()
    assert len(cmds) == 1
    assert cmds[0][1].type == public_gamma.COMMAND_TYPE
    assert cmds[0][1].args == {"symbol": "NVDA"}
    assert bus_client.bus().consume_commands(
        "cmd:options", group="g", consumer="c", block_ms=50) == []


@pytest.mark.parametrize("bad", ["", None, "spy; flushall", "../x", "TOOLONGSYM"])
def test_an_invalid_gamma_symbol_is_refused_before_anything_is_written(bad):
    bus_client.set_read_only(True)
    with pytest.raises(ValueError):
        bus_client.request_public_gamma(bad)
    assert _gamma_stream_commands() == []


def test_the_gamma_request_takes_one_argument_and_writes_one_place():
    import ast
    import inspect
    import textwrap
    fn = ast.parse(textwrap.dedent(inspect.getsource(
        bus_client.request_public_gamma))).body[0]
    params = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    assert params == ["raw_symbol"] and not fn.args.vararg and not fn.args.kwarg
    writes = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "enqueue_command"]
    assert len(writes) == 1
    assert ast.unparse(writes[0].args[0]) == "public_gamma.STREAM"


# ── the public Calculator and Simulator's two writes ───────────────────────

def _tools_stream_commands(stream):
    return bus_client.bus().consume_commands(
        stream, group="g", consumer="c", block_ms=50)


def _calc_leg(**over):
    leg = {"option_type": "put", "side": "short", "strike": 500.0,
           "expiry": _EXP, "qty": 1, "premium": 1.2}
    leg.update(over)
    return leg


def _price_request(**over):
    req = {"kind": "price", "symbol": " spy ", "strategy": "PCS",
           "spot": 505.0, "iv": 0.2, "rate": 0.045, "ivadj": 0.0, "qty": 1,
           "expiry": _EXP, "num_strikes": 20,
           "legs": [_calc_leg(), _calc_leg(side="long", strike=495.0,
                                           premium=0.6)]}
    req.update(over)
    return req


def test_public_tool_and_math_requests_are_allowed_on_a_read_only_process():
    from shared import public_tools
    bus_client.set_read_only(True)
    assert bus_client.request_public_tool({"kind": "chain", "symbol": " spy "})
    assert bus_client.request_public_math(_price_request())
    tools = [c for _id, c in _tools_stream_commands(public_tools.TOOLS_STREAM)]
    maths = [c for _id, c in _tools_stream_commands(public_tools.MATH_STREAM)]
    assert [c.type for c in tools] == [public_tools.TOOLS_TYPE]
    assert [c.type for c in maths] == [public_tools.MATH_TYPE]


def test_each_public_tools_request_writes_exactly_the_builders_command():
    from shared import public_tools
    raw_tool = {"kind": "expiry", "symbol": "spy", "expiry": _EXP, "junk": 1}
    raw_math = _price_request(junk="x")
    bus_client.request_public_tool(raw_tool)
    bus_client.request_public_math(raw_math)
    tools = [c for _id, c in _tools_stream_commands(public_tools.TOOLS_STREAM)]
    maths = [c for _id, c in _tools_stream_commands(public_tools.MATH_STREAM)]
    want_tool = public_tools.tools_command(raw_tool)
    want_math = public_tools.math_command(raw_math)
    assert len(tools) == 1 and len(maths) == 1
    assert (tools[0].type, tools[0].args) == (want_tool["type"], want_tool["args"])
    assert (maths[0].type, maths[0].args) == (want_math["type"], want_math["args"])
    assert "junk" not in tools[0].args and "junk" not in maths[0].args


def test_each_kind_lives_on_its_own_stream_only():
    """A math kind handed to the tools writer (or the reverse) is refused, not
    re-routed."""
    from shared import public_tools
    with pytest.raises(ValueError):
        bus_client.request_public_tool(_price_request())
    with pytest.raises(ValueError):
        bus_client.request_public_math({"kind": "chain", "symbol": "SPY"})
    assert _tools_stream_commands(public_tools.TOOLS_STREAM) == []
    assert _tools_stream_commands(public_tools.MATH_STREAM) == []


@pytest.mark.parametrize("call", [
    lambda: bus_client.request_public_tool({"kind": "chain", "symbol": "../x"}),
    lambda: bus_client.request_public_tool({"kind": "expiry", "symbol": "SPY",
                                            "expiry": "tomorrow"}),
    lambda: bus_client.request_public_tool("not a dict"),
    lambda: bus_client.request_public_math(
        _price_request(legs=[_calc_leg(strike=float("nan"))])),
    lambda: bus_client.request_public_math(_price_request(spot=True)),
    lambda: bus_client.request_public_math(None),
])
def test_an_invalid_public_tools_request_writes_nothing(call):
    from shared import public_tools
    bus_client.set_read_only(True)
    with pytest.raises(ValueError):
        call()
    assert _tools_stream_commands(public_tools.TOOLS_STREAM) == []
    assert _tools_stream_commands(public_tools.MATH_STREAM) == []


def test_public_tools_requests_never_reach_the_options_stream():
    bus_client.request_public_tool({"kind": "chain", "symbol": "SPY"})
    bus_client.request_public_math(_price_request())
    assert bus_client.bus().consume_commands(
        "cmd:options", group="g", consumer="c", block_ms=50) == []


@pytest.mark.parametrize("domain", ["tools_public", "tools_public_math"])
def test_the_generic_request_path_stays_refused_for_the_tools_streams(domain):
    from shared import public_tools
    bus_client.set_read_only(True)
    with pytest.raises(PermissionError):
        bus_client.request(domain, {"type": "public_tool",
                                    "args": {"kind": "chain", "symbol": "SPY"}})
    assert _tools_stream_commands(public_tools.TOOLS_STREAM) == []
    assert _tools_stream_commands(public_tools.MATH_STREAM) == []


@pytest.mark.parametrize("fn, stream, builder", [
    ("request_public_tool", "public_tools.TOOLS_STREAM",
     "public_tools.tools_command(raw_request)"),
    ("request_public_math", "public_tools.MATH_STREAM",
     "public_tools.math_command(raw_request)"),
])
def test_each_public_tools_request_writes_one_command_to_one_place(fn, stream, builder):
    """The caller controls the request's fields; never the stream, the command
    type, or anything written besides what the builder returned."""
    import ast
    import inspect
    import textwrap
    node = ast.parse(textwrap.dedent(inspect.getsource(
        getattr(bus_client, fn)))).body[0]
    assert [a.arg for a in node.args.args + node.args.kwonlyargs] == ["raw_request"]
    assert not node.args.vararg and not node.args.kwarg
    writes = [n for n in ast.walk(node) if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "enqueue_command"]
    assert len(writes) == 1
    assert not writes[0].keywords
    got_stream, command = writes[0].args
    assert ast.unparse(got_stream) == stream
    assert ast.unparse(command) == "command"
    # Every binding of ``command``, in any spelling, must be the builder's call.
    def _targets(n):
        if isinstance(n, ast.Assign):
            return n.targets
        if isinstance(n, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            return [n.target]
        return []
    builds = [n for n in ast.walk(node)
              if any(ast.unparse(t) == "command" for t in _targets(n))]
    assert [ast.unparse(b.value) for b in builds] == [builder]
