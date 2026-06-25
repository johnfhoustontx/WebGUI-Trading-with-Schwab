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
