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

from shared.contracts.driver import ApprovalState, PerfReport


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
