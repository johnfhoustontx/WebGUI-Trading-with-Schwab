"""Tests for the autonomous bus I/O handlers (Phase 5).

The bus I/O wrapper around the bus-free ``compute.run_cycle`` brain: the control
key read/write helpers (5.1), ``run_autonomous_cycle`` (5.2 — gate → cycle →
``paper_create`` enqueue → halt-latch → publish), and the autonomous command
dispatch branches (5.3 — ``cycle``/``enable``/``disable``/``stop``).

``compute.run_cycle`` / ``compute.fetch_market_context`` are monkeypatched so no
network/engine work happens; the focus here is the Redis read/execute/publish
plumbing and the kill-switch latching. Uses the ``fake_bus`` fixture (conftest).
"""
import json

from services.driver_svc import handlers


def _cmd(t, **args):
    return type("C", (), {"type": t, "args": args})()


# ── 5.1: control read/write helpers ──────────────────────────────────────────


def test_control_roundtrip(fake_bus):
    handlers.set_control(fake_bus, enabled=True)
    c = handlers.read_control(fake_bus)
    assert c["enabled"] is True and c["halted"] is False


def test_read_control_default_when_unset(fake_bus):
    c = handlers.read_control(fake_bus)
    assert c["enabled"] is False and c["halted"] is False and c["reason"] is None


def test_stop_sets_halted(fake_bus):
    handlers.set_control(fake_bus, enabled=True)
    handlers.set_control(fake_bus, halted=True, reason="manual STOP")
    c = handlers.read_control(fake_bus)
    assert c["halted"] is True and c["reason"] == "manual STOP" and c["enabled"] is True


def test_set_control_publishes_event(fake_bus):
    sub = fake_bus.subscribe(handlers.EVENT_CONTROL)
    handlers.set_control(fake_bus, enabled=True)
    msg = sub.get_message(timeout=1.0)
    sub.close()
    assert msg is not None and "version" in msg


# ── 5.2: run_autonomous_cycle (gate → cycle → execute → publish) ─────────────


def _seed_caches(fake_bus, *, day_pnl=0.0):
    fake_bus.cache_set("cache:options:scan",
                       {"signals_0dte": [], "signals_swing": []})
    fake_bus.cache_set("cache:options:paper_account",
                       {"snapshot": {"day_pnl": day_pnl}, "positions": []})


def _stub_cycle(monkeypatch, out):
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", lambda *a, **k: out)


def test_cycle_disabled_is_noop(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=False)
    called = []
    monkeypatch.setattr(handlers.compute, "run_cycle",
                        lambda *a, **k: called.append(1) or {})
    monkeypatch.setattr(handlers.compute, "fetch_market_context",
                        lambda: called.append("mkt") or {})
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command",
                        lambda stream, cmd: enq.append((stream, cmd)))
    handlers.run_autonomous_cycle(fake_bus)
    assert called == []  # gated off — no cycle, no market fetch
    assert enq == []      # and nothing enqueued
    # And no monitor view was published (genuine no-op).
    assert fake_bus.cache_get("cache:driver:autonomous") is None


def test_cycle_halted_is_noop(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    handlers.set_control(fake_bus, halted=True, reason="banked")
    called = []
    monkeypatch.setattr(handlers.compute, "run_cycle",
                        lambda *a, **k: called.append(1) or {})
    handlers.run_autonomous_cycle(fake_bus)
    assert called == []  # halted → gated off


def test_cycle_enqueues_paper_create(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus, day_pnl=0.0)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": False, "day_thesis": "t",
                     "trades": [{"id": "m0", "quantity": 2}]},
        "executable": [{"id": "m0", "signal": {"symbol": "QQQ", "structure": "PCS"},
                        "qty": 2, "rationale": "r"}],
        "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command",
                        lambda stream, cmd: enq.append((stream, cmd)))
    handlers.run_autonomous_cycle(fake_bus)
    assert len(enq) == 1
    stream, cmd = enq[0]
    assert stream == "cmd:options"
    assert cmd["type"] == "paper_create"
    assert cmd["args"]["qty"] == 2
    assert cmd["args"]["signal"]["source"] == "driver"
    assert cmd["args"]["signal"]["symbol"] == "QQQ"


def test_cycle_enqueue_lands_on_cmd_options_stream(fake_bus, monkeypatch):
    """Read the stream back (no enqueue_command monkeypatch) → real round-trip."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": False, "day_thesis": "bull", "trades": []},
        "executable": [{"id": "m0", "signal": {"symbol": "SPX", "structure": "IC"},
                        "qty": 1, "rationale": ""}],
        "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    entries = fake_bus._r.xrange("cmd:options")
    assert len(entries) == 1
    _msg_id, fields = entries[0]
    payload = json.loads(fields["data"])
    assert payload["type"] == "paper_create"
    assert payload["args"]["qty"] == 1
    assert payload["args"]["signal"]["source"] == "driver"


def test_cycle_does_not_mutate_raw_menu_signal(fake_bus, monkeypatch):
    """The cached menu signal must NOT gain ``source`` — only the enqueued COPY."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    raw_signal = {"symbol": "QQQ", "structure": "PCS", "max_loss": 200}
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": False, "day_thesis": "t", "trades": []},
        "executable": [{"id": "m0", "signal": raw_signal, "qty": 1, "rationale": ""}],
        "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command",
                        lambda stream, cmd: enq.append((stream, cmd)))
    handlers.run_autonomous_cycle(fake_bus)
    # The original signal object is untouched.
    assert "source" not in raw_signal
    # The enqueued one carries the tag (a distinct dict).
    enqueued_sig = enq[0][1]["args"]["signal"]
    assert enqueued_sig["source"] == "driver"
    assert enqueued_sig is not raw_signal


def test_cycle_partial_enqueue_failure_still_publishes(fake_bus, monkeypatch):
    """A mid-loop enqueue failure must NOT skip the publish, and the audit log must
    record only what ACTUALLY fired (M1 — no silent partial execution)."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": False, "day_thesis": "two", "trades": []},
        "executable": [
            {"id": "m0", "signal": {"symbol": "QQQ", "structure": "PCS"}, "qty": 1, "rationale": ""},
            {"id": "m1", "signal": {"symbol": "SPX", "structure": "IC"}, "qty": 1, "rationale": ""}],
        "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    calls = {"n": 0}

    def _flaky(stream, cmd):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("bus down")

    monkeypatch.setattr(fake_bus, "enqueue_command", _flaky)
    handlers.run_autonomous_cycle(fake_bus)   # must not raise
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env is not None                                   # published despite the failure
    executed = env.payload["decisions"][0]["executed"]
    assert len(executed) == 1 and executed[0]["symbol"] == "QQQ"  # only what fired


def test_cycle_stand_down_enqueues_nothing_but_publishes(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus, day_pnl=120.0)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": True, "day_thesis": "no edge", "trades": []},
        "executable": [], "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 120.0, "open_positions": []})
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command",
                        lambda stream, cmd: enq.append((stream, cmd)))
    handlers.run_autonomous_cycle(fake_bus)
    assert enq == []
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env is not None
    assert env.payload["day_pnl"] == 120.0
    assert env.payload["decisions"][0]["thesis"] == "no edge"


def test_cycle_halt_latches_control(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus, day_pnl=600.0)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": True, "trades": []},
        "executable": [], "rejected": [],
        "halted": True, "halt_reason": "Target reached",
        "day_pnl": 600.0, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    control = handlers.read_control(fake_bus)
    assert control["halted"] is True
    assert control["reason"] == "Target reached"
    assert control["halted_date"]  # stamped with today
    # The monitor view reflects the halt too.
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env.payload["halted"] is True
    assert env.payload["halt_reason"] == "Target reached"


def test_cycle_publishes_autonomous_event(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": True, "day_thesis": "", "trades": []},
        "executable": [], "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    sub = fake_bus.subscribe("events:driver:autonomous")
    handlers.run_autonomous_cycle(fake_bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()
    assert msg is not None and "version" in msg


def test_decision_log_is_newest_first_and_capped(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    # Run 55 cycles with a distinguishable thesis each; log must cap at 50, newest first.
    for i in range(55):
        _stub_cycle(monkeypatch, {
            "decision": {"stand_down": True, "day_thesis": f"cycle-{i}", "trades": []},
            "executable": [], "rejected": [], "halted": False, "halt_reason": None,
            "day_pnl": 0.0, "open_positions": []})
        handlers.run_autonomous_cycle(fake_bus)
    log = fake_bus.cache_get("cache:driver:autonomous").payload["decisions"]
    assert len(log) == 50
    assert log[0]["thesis"] == "cycle-54"   # newest first
    assert log[-1]["thesis"] == "cycle-5"   # oldest retained (55 total, cap 50)


# ── 5.3: autonomous command dispatch (cycle/enable/disable/stop) ─────────────


def test_handle_command_enable_disable_stop(fake_bus):
    handlers.handle_command(fake_bus, _cmd("enable"))
    assert handlers.read_control(fake_bus)["enabled"] is True
    handlers.handle_command(fake_bus, _cmd("stop"))
    assert handlers.read_control(fake_bus)["halted"] is True
    handlers.handle_command(fake_bus, _cmd("disable"))
    assert handlers.read_control(fake_bus)["enabled"] is False


def test_handle_command_stop_sets_reason_and_date(fake_bus):
    handlers.handle_command(fake_bus, _cmd("enable"))
    handlers.handle_command(fake_bus, _cmd("stop"))
    c = handlers.read_control(fake_bus)
    assert c["halted"] is True
    assert c["reason"] == "manual STOP"
    assert c["halted_date"]  # ISO date stamped


def test_handle_command_enable_clears_prior_halt(fake_bus):
    # A halted-but-enabled control re-arms on a fresh enable.
    handlers.set_control(fake_bus, enabled=True)
    handlers.set_control(fake_bus, halted=True, reason="banked")
    handlers.handle_command(fake_bus, _cmd("enable"))
    c = handlers.read_control(fake_bus)
    assert c["enabled"] is True and c["halted"] is False and c["reason"] is None


def test_handle_command_cycle_runs_autonomous(fake_bus, monkeypatch):
    called = []
    monkeypatch.setattr(handlers, "run_autonomous_cycle",
                        lambda bus: called.append(bus))
    handlers.handle_command(fake_bus, _cmd("cycle"))
    assert called == [fake_bus]


def test_handle_command_legacy_branches_still_dispatch(fake_bus, monkeypatch):
    calls = []
    monkeypatch.setattr(handlers, "run_morning", lambda b: calls.append("run"))
    monkeypatch.setattr(handlers, "approve", lambda b: calls.append("approve"))
    monkeypatch.setattr(handlers, "skip", lambda b: calls.append("skip"))
    monkeypatch.setattr(handlers, "refresh_perf", lambda b: calls.append("perf"))
    for t in ("run", "approve", "skip", "perf"):
        handlers.handle_command(fake_bus, _cmd(t))
    handlers.handle_command(fake_bus, _cmd("bogus"))  # unknown → no-op
    assert calls == ["run", "approve", "skip", "perf"]
