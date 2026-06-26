"""Tests for the PURE driver performance scorecard (driver_perf.build_scorecard).

No I/O, no engine import — exercised with plain position dicts + an account
snapshot dict. Covers empty / mixed open-closed / all-wins (profit_factor None) /
by-symbol + by-strategy breakdowns / sparse-or-None rows."""
from services.options_svc import driver_perf as dp


def _closed(symbol, strategy, pnl):
    return {"symbol": symbol, "strategy": strategy, "status": "CLOSED",
            "realized_pnl": pnl, "unrealized_pnl": None}


def _open(symbol, strategy, upnl):
    return {"symbol": symbol, "strategy": strategy, "status": "OPEN",
            "realized_pnl": None, "unrealized_pnl": upnl}


def _snap(**o):
    base = {"session_pnl": 0.0, "realized_pnl": 0.0, "open_unrealized": 0.0,
            "equity": 25000.0, "open_count": 0, "halted": False}
    base.update(o)
    return base


def test_empty_account():
    s = dp.build_scorecard([], _snap())
    assert s["total_trades"] == 0 and s["closed"] == 0 and s["win_rate"] == 0.0
    assert s["profit_factor"] is None and s["by_symbol"] == [] and s["best"] is None


def test_win_rate_and_profit_factor():
    pos = [_closed("MU", "PCS", 120.0), _closed("MU", "PCS", -60.0),
           _closed("SPY", "CCS", 40.0), _open("QQQ", "IC", 15.0)]
    s = dp.build_scorecard(pos, _snap(open_unrealized=15.0, realized_pnl=100.0,
                                      session_pnl=115.0, open_count=1))
    assert s["total_trades"] == 4 and s["open"] == 1 and s["closed"] == 3
    assert s["wins"] == 2 and s["losses"] == 1
    assert s["win_rate"] == round(2 / 3, 4)
    assert s["realized_pnl"] == 100.0          # 120 - 60 + 40
    assert s["open_unrealized"] == 15.0 and s["total_pnl"] == 115.0
    assert s["avg_win"] == 80.0 and s["avg_loss"] == -60.0
    assert s["profit_factor"] == round(160.0 / 60.0, 2)   # (120+40)/|-60|
    assert s["best"]["realized_pnl"] == 120.0 and s["worst"]["realized_pnl"] == -60.0


def test_profit_factor_none_when_no_losses():
    s = dp.build_scorecard([_closed("MU", "PCS", 50.0)], _snap(realized_pnl=50.0))
    assert s["profit_factor"] is None     # no losses → undefined, render "—"


def test_breakdown_by_symbol_and_strategy():
    pos = [_closed("MU", "PCS", 100.0), _closed("MU", "PCS", -30.0), _closed("SPY", "CCS", 20.0)]
    s = dp.build_scorecard(pos, _snap())
    bym = {r["symbol"]: r for r in s["by_symbol"]}
    assert bym["MU"]["trades"] == 2 and bym["MU"]["pnl"] == 70.0 and bym["MU"]["win_rate"] == 0.5
    bys = {r["strategy"]: r for r in s["by_strategy"]}
    assert bys["PCS"]["pnl"] == 70.0 and bys["CCS"]["pnl"] == 20.0


def test_tolerates_sparse_rows():
    s = dp.build_scorecard([{"status": "CLOSED"}, None, {}], _snap())
    assert s["closed"] >= 1   # None/empty don't crash


def test_best_worst_exclude_none_pnl_rows():
    # A closed row with no realized_pnl must NOT be reported as best/worst over a
    # real (negative) trade — best/worst use the same None-excluded set as the metrics.
    pos = [_closed("MU", "PCS", -5.0),
           {"symbol": "X", "strategy": "PCS", "status": "CLOSED", "realized_pnl": None}]
    s = dp.build_scorecard(pos, _snap())
    assert s["best"]["realized_pnl"] == -5.0
    assert s["worst"]["realized_pnl"] == -5.0
