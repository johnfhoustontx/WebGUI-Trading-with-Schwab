"""Tests for the driver service compute orchestration (Task #29a).

``compute.run_morning`` ports ``claude-driver/morning_agent.run_morning_agent``'s
orchestration MINUS its file-write / HTTP-post side effects: it calls the same
building-block functions and *returns* the pending-trade payload. We monkeypatch
those building blocks (and ``trade_selector.select_trades`` /
``order_executor.execute_trades`` / ``perf_report.build_report``) so nothing
touches a live proxy, ML server, or the filesystem. Every function must be
defensive — a thrown engine degrades to an ``error``/empty payload, never raises.
"""
import pytest

from services.driver_svc import compute


def _wire_pipeline(monkeypatch, *, holiday=False, grade=("B", ["Grade B - score 8/11"]),
                   conditions=None, pnl=None, trades=None):
    ma = compute.morning_agent
    monkeypatch.setattr(ma, "is_market_holiday", lambda: holiday)
    monkeypatch.setattr(ma, "check_service_health", lambda: {})
    monkeypatch.setattr(ma, "fetch_all_ml_signals", lambda health=None: {"SPX": {"signal": "Bullish"}})
    monkeypatch.setattr(ma, "fetch_gex_snapshot", lambda: {"net_gex": 1.0})
    monkeypatch.setattr(ma, "fetch_market_conditions",
                        lambda: conditions if conditions is not None
                        else {"vix": 16.0, "spx_spot": 5400.0, "vix1d": 14.0})
    monkeypatch.setattr(ma, "fetch_current_pnl",
                        lambda: pnl if pnl is not None
                        else {"today": 0.0, "week": -50.0, "month": -50.0})
    monkeypatch.setattr(ma, "grade_day", lambda c, p: grade)
    monkeypatch.setattr(compute.trade_selector, "select_trades",
                        lambda **kw: trades if trades is not None
                        else [{"bucket": "A", "instrument": "SPX",
                               "structure": "put_credit_spread", "contracts": 1,
                               "max_risk": 300.0, "notes": "SPX 0-DTE pcs"}])


def test_run_morning_pending(monkeypatch):
    _wire_pipeline(monkeypatch)
    res = compute.run_morning()
    assert res["status"] == "pending"
    assert res["grade"] == "B"
    assert res["decision"] is None
    assert res["grade_reasons"] == ["Grade B - score 8/11"]
    assert res["conditions"]["vix"] == 16.0
    assert res["pnl_today"] == 0.0 and res["pnl_week"] == -50.0
    assert res["proposed_trades"][0]["bucket"] == "A"
    assert res["timestamp"]


def test_run_morning_grade_x_skips_selection(monkeypatch):
    called = {"select": 0}

    _wire_pipeline(monkeypatch, grade=("X", ["BLOCKED: VIX=30 > 25"]))

    def _boom(**kw):
        called["select"] += 1
        return []

    monkeypatch.setattr(compute.trade_selector, "select_trades", _boom)
    res = compute.run_morning()
    assert res["status"] == "no_trade"
    assert res["grade"] == "X"
    assert called["select"] == 0  # graded X -> selection never runs
    assert res["reasons"]
    assert res["proposed_trades"] == []


def test_run_morning_no_setups(monkeypatch):
    _wire_pipeline(monkeypatch, trades=[])
    res = compute.run_morning()
    assert res["status"] == "no_trade"
    assert res["grade"] == "B"
    assert res["proposed_trades"] == []
    assert res["reasons"]


def test_run_morning_holiday(monkeypatch):
    _wire_pipeline(monkeypatch, holiday=True)
    res = compute.run_morning()
    assert res["status"] == "no_trade"
    assert res["proposed_trades"] == []
    assert any("holiday" in r.lower() for r in res["reasons"])


def test_run_morning_never_raises(monkeypatch):
    _wire_pipeline(monkeypatch)

    def _boom():
        raise RuntimeError("proxy down")

    monkeypatch.setattr(compute.morning_agent, "fetch_market_conditions", _boom)
    res = compute.run_morning()
    assert res["status"] == "error"
    assert res["error"] and "proxy down" in res["error"]
    assert res["timestamp"]


def test_execute_delegates_to_order_executor(monkeypatch):
    seen = {}

    def _exec(trades):
        seen["trades"] = trades
        return [{"success": True, "paper": True, "order_id": "PAPER-1"}]

    monkeypatch.setattr(compute.order_executor, "execute_trades", _exec)
    out = compute.execute([{"bucket": "B"}])
    assert seen["trades"] == [{"bucket": "B"}]
    assert out[0]["paper"] is True


def test_execute_defensive_on_error(monkeypatch):
    def _boom(trades):
        raise RuntimeError("executor blew up")

    monkeypatch.setattr(compute.order_executor, "execute_trades", _boom)
    out = compute.execute([{"bucket": "B"}])
    assert out and out[0]["success"] is False
    assert "executor blew up" in out[0]["error"]


def test_build_perf_report_delegates(monkeypatch):
    rep = {"summary": {"total_trades": 2, "win_rate": 50.0}, "trades": [{"pnl": 1.0}]}
    monkeypatch.setattr(compute.perf_report, "build_report", lambda: rep)
    out = compute.build_perf_report()
    assert out["summary"]["total_trades"] == 2
    assert out["trades"] == [{"pnl": 1.0}]


def test_build_perf_report_defensive(monkeypatch):
    def _boom():
        raise RuntimeError("db locked")

    monkeypatch.setattr(compute.perf_report, "build_report", _boom)
    out = compute.build_perf_report()
    assert out == {"summary": {}, "trades": []}
