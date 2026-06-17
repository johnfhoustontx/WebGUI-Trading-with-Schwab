"""Tests for the driver service handlers (Task #29b).

We monkeypatch ``handlers.compute`` so nothing touches the live morning pipeline
and use a fakeredis ``Bus(fake=True)``. The handlers must compute → validate
against the driver contracts → cache under one view → publish an event, and the
approve/skip handlers must transition the *currently pending* approval only.
"""
import json

from shared.bus import Bus
from shared.contracts.envelope import Command
from shared.contracts.driver import ApprovalState, PerfReport
from services.driver_svc import handlers


def _pending(**over):
    base = {
        "date": "2026-06-16", "grade": "B",
        "grade_reasons": ["Grade B - score 8/11"],
        "conditions": {"vix": 16.0, "spx_spot": 5400.0},
        "pnl_today": 0.0, "pnl_week": -50.0,
        "proposed_trades": [{"bucket": "A", "instrument": "SPX",
                             "structure": "put_credit_spread", "contracts": 1,
                             "max_risk": 300.0, "notes": "SPX 0-DTE pcs"}],
        "status": "pending", "decision": None, "timestamp": "2026-06-16T13:00:00Z",
    }
    base.update(over)
    return base


def test_run_morning_caches_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_morning", lambda: _pending())

    sub = bus.subscribe("events:driver:approvals")
    handlers.run_morning(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:driver:approvals")
    assert env is not None and env.version == 1
    st = ApprovalState.from_json(json.dumps(env.payload))
    assert st.status == "pending"
    assert st.proposed_trades[0]["bucket"] == "A"
    assert msg is not None and "version" in msg


def test_run_morning_gate_rejects_malformed(monkeypatch):
    """A gross shape drift (proposed_trades not a list) raises BEFORE caching."""
    import pytest
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_morning",
                        lambda: _pending(proposed_trades="oops"))
    with pytest.raises(Exception):
        handlers.run_morning(bus)
    assert bus.cache_get("cache:driver:approvals") is None


def test_approve_executes_and_marks(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_morning", lambda: _pending())
    handlers.run_morning(bus)

    seen = {}

    def _exec(trades):
        seen["trades"] = trades
        return [{"success": True, "paper": True, "order_id": "PAPER-1",
                 "trade_id": "2026-06-16-A-1"}]

    monkeypatch.setattr(handlers.compute, "execute", _exec)

    handlers.approve(bus)
    env = bus.cache_get("cache:driver:approvals")
    st = ApprovalState.from_json(json.dumps(env.payload))
    assert st.status == "approved" and st.decision == "approved"
    assert st.results[0]["paper"] is True
    assert seen["trades"][0]["bucket"] == "A"  # the pending trades were executed
    assert env.version == 2  # bumped over the pending cache


def test_approve_noop_when_not_pending(monkeypatch):
    bus = Bus(fake=True)
    # Cache an already-skipped approval; approve must NOT execute.
    monkeypatch.setattr(handlers.compute, "run_morning",
                        lambda: _pending(status="no_trade", proposed_trades=[]))
    handlers.run_morning(bus)
    called = {"n": 0}
    monkeypatch.setattr(handlers.compute, "execute",
                        lambda t: called.__setitem__("n", called["n"] + 1) or [])
    handlers.approve(bus)
    assert called["n"] == 0
    env = bus.cache_get("cache:driver:approvals")
    assert env.version == 1  # unchanged — no re-cache


def test_approve_noop_when_no_cache(monkeypatch):
    bus = Bus(fake=True)
    called = {"n": 0}
    monkeypatch.setattr(handlers.compute, "execute",
                        lambda t: called.__setitem__("n", called["n"] + 1) or [])
    handlers.approve(bus)
    assert called["n"] == 0
    assert bus.cache_get("cache:driver:approvals") is None


def test_skip_marks_skipped(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_morning", lambda: _pending())
    handlers.run_morning(bus)
    handlers.skip(bus)
    env = bus.cache_get("cache:driver:approvals")
    st = ApprovalState.from_json(json.dumps(env.payload))
    assert st.status == "skipped" and st.decision == "skipped"
    assert env.version == 2


def test_refresh_perf_caches_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "build_perf_report",
                        lambda: {"summary": {"total_trades": 2, "win_rate": 50.0},
                                 "trades": [{"pnl": 1.0}]})
    sub = bus.subscribe("events:driver:performance")
    handlers.refresh_perf(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:driver:performance")
    assert env is not None and env.version == 1
    pr = PerfReport.from_json(json.dumps(env.payload))
    assert pr.summary["total_trades"] == 2
    assert pr.timestamp  # stamped
    assert msg is not None and "version" in msg


def test_handle_command_dispatch(monkeypatch):
    bus = Bus(fake=True)
    calls = []
    monkeypatch.setattr(handlers, "run_morning", lambda b: calls.append("run"))
    monkeypatch.setattr(handlers, "approve", lambda b: calls.append("approve"))
    monkeypatch.setattr(handlers, "skip", lambda b: calls.append("skip"))
    monkeypatch.setattr(handlers, "refresh_perf", lambda b: calls.append("perf"))

    for t in ("run", "approve", "skip", "perf"):
        handlers.handle_command(bus, Command(type=t))
    handlers.handle_command(bus, Command(type="bogus"))  # no-op
    assert calls == ["run", "approve", "skip", "perf"]
