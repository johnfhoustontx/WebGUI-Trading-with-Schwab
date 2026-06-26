"""Tests for the driver-domain contracts (Task #28).

``ApprovalState`` is the pending/decided morning-agent payload cached at
``cache:driver:approvals``; ``PerfReport`` is the read-only performance
aggregation cached at ``cache:driver:performance``. Like the other domain
contracts they validate the *envelope* shape (the lists/dicts are the right
container types, a couple of required-ish fields exist) as a gate against gross
drift BEFORE caching — they do not over-specify the heterogeneous, sparse
sub-objects (proposed-trade dicts vary by bucket; perf rows vary by source).
"""
import pytest

from shared.contracts.driver import (
    ApprovalState,
    AutonomousState,
    DriverControl,
    PerfReport,
)


def test_approval_pending_roundtrip():
    st = ApprovalState(
        date="2026-06-16",
        grade="B",
        grade_reasons=["Grade B - score 8/11", "VIX=16.0 - normal range"],
        conditions={"vix": 16.0, "spx_spot": 5400.0, "vix1d": 14.0},
        pnl_today=0.0,
        pnl_week=-50.0,
        proposed_trades=[
            {"bucket": "A", "instrument": "SPX", "structure": "put_credit_spread",
             "strikes": {"short": 5350, "long": 5340}, "contracts": 1,
             "max_risk": 300.0, "notes": "SPX 0-DTE put credit spread"},
        ],
        status="pending",
    )
    back = ApprovalState.from_json(st.to_json())
    assert back.grade == "B"
    assert back.status == "pending"
    assert back.proposed_trades[0]["bucket"] == "A"
    assert back.conditions["vix"] == 16.0
    assert back.decision is None
    assert back.results == []


def test_approval_defaults_empty():
    st = ApprovalState(status="no_trade")
    assert st.grade == ""
    assert st.proposed_trades == []
    assert st.grade_reasons == [] and st.reasons == []
    assert st.results == []
    assert st.decision is None
    assert st.error is None
    assert st.pnl_today is None


def test_approval_decided_roundtrip():
    st = ApprovalState(
        date="2026-06-16", grade="A", status="approved", decision="approved",
        proposed_trades=[{"bucket": "B", "instrument": "QQQ", "side": "BUY"}],
        results=[{"success": True, "paper": True, "order_id": "PAPER-101530",
                  "trade_id": "2026-06-16-B-101530"}],
    )
    back = ApprovalState.from_json(st.to_json())
    assert back.decision == "approved"
    assert back.results[0]["paper"] is True


def test_approval_rejects_wrong_type():
    # proposed_trades must be a list — a gross drift must raise.
    with pytest.raises(Exception):
        ApprovalState.from_json('{"status": "pending", "proposed_trades": "nope"}')
    with pytest.raises(Exception):
        ApprovalState.from_json('{"status": "pending", "conditions": "nope"}')


def test_perf_report_roundtrip():
    pr = PerfReport(
        summary={"total_trades": 3, "wins": 2, "losses": 1, "win_rate": 66.7,
                 "realized_pnl": 125.5, "pnl_by_bucket": {"A": 100.0, "B": 25.5}},
        trades=[{"trade_id": "2026-06-16-A-100000", "bucket": "A", "pnl": 100.0,
                 "status": "closed", "source": "streamed"}],
    )
    back = PerfReport.from_json(pr.to_json())
    assert back.summary["win_rate"] == 66.7
    assert back.trades[0]["bucket"] == "A"


def test_perf_report_defaults_empty():
    pr = PerfReport()
    assert pr.summary == {} and pr.trades == []
    assert pr.timestamp is None


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
