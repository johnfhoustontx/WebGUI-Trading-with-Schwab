"""Tests for the driver-domain contracts.

``DriverControl`` (``cache:driver:control``) is the autonomous master switch +
kill-switch; ``AutonomousState`` (``cache:driver:autonomous``) is the live
monitor view for the /driver page. Like the other domain contracts they validate
the *envelope* shape (the lists/dicts are the right container types, a couple of
required-ish fields exist) as a gate against gross drift BEFORE caching — they do
not over-specify the heterogeneous, sparse sub-objects (positions/decisions vary).

The legacy morning-agent order-approval contracts (``ApprovalState`` /
``PerfReport``) were removed with that subsystem.
"""
import pytest

from shared.contracts.driver import (
    AutonomousState,
    DriverControl,
)


def test_driver_control_defaults_disabled():
    c = DriverControl()
    assert c.enabled is False and c.halted is False and c.reason is None
    assert c.halted_date is None and c.timestamp is None


def test_driver_control_roundtrip():
    c = DriverControl(enabled=True, halted=True, reason="Target reached",
                      halted_date="2026-06-24", timestamp="2026-06-24T15:00:00")
    back = DriverControl.from_json(c.to_json())
    assert back.enabled is True and back.halted is True
    assert back.reason == "Target reached" and back.halted_date == "2026-06-24"


def test_autonomous_state_envelope():
    s = AutonomousState(date="2026-06-24", day_pnl=120.0, target=500.0,
                        positions=[{"symbol": "QQQ"}],
                        decisions=[{"thesis": "x", "trades": []}],
                        enabled=True, halted=False)
    assert s.target == 500.0
    assert isinstance(s.decisions, list) and isinstance(s.positions, list)


def test_autonomous_state_defaults_and_roundtrip():
    s = AutonomousState()
    assert s.date == "" and s.target == 500.0
    assert s.positions == [] and s.decisions == []
    assert s.enabled is False and s.halted is False
    assert s.day_pnl is None and s.halt_reason is None
    back = AutonomousState.from_json(s.to_json())
    assert back.target == 500.0 and back.decisions == []


def test_autonomous_state_rejects_wrong_type():
    # positions / decisions must be lists — a gross drift must raise.
    with pytest.raises(Exception):
        AutonomousState.from_json('{"positions": "nope"}')
    with pytest.raises(Exception):
        AutonomousState.from_json('{"decisions": "nope"}')


def test_autonomous_state_perf_field():
    # The driver-account performance scorecard rides the monitor view (additive,
    # loose dict — like ``conditions`` on ApprovalState).
    s = AutonomousState(perf={"win_rate": 0.5, "total_trades": 4})
    assert s.perf["win_rate"] == 0.5
    assert AutonomousState().perf == {}            # additive default
    back = AutonomousState.from_json(s.to_json())
    assert back.perf["total_trades"] == 4
