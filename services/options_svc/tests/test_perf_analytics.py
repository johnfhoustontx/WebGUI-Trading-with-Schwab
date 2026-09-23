"""Tests for perf_analytics — the PURE paper-book analytics (no I/O)."""
from services.options_svc import perf_analytics as pa


def _pos(**kw):
    base = {"status": "CLOSED", "exit_ts": "2026-07-10T15:00:00", "realized_pnl": 0.0,
            "strategy": "PCS", "mae": None, "mfe": None}
    base.update(kw)
    return base


# ── equity_curve ─────────────────────────────────────────────────────────────
def test_equity_curve_buckets_by_exit_date_and_accumulates():
    positions = [
        _pos(exit_ts="2026-07-08T15:00:00", realized_pnl=100.0),
        _pos(exit_ts="2026-07-08T15:05:00", realized_pnl=-40.0),
        _pos(exit_ts="2026-07-09T15:00:00", realized_pnl=50.0),
        _pos(status="OPEN", exit_ts=None, realized_pnl=None),   # open → excluded
    ]
    curve = pa.equity_curve(positions, starting_balance=25000.0)
    assert [c["date"] for c in curve] == ["2026-07-08", "2026-07-09"]
    assert curve[0]["realized"] == 60.0 and curve[0]["trades"] == 2
    assert curve[0]["cum_realized"] == 60.0 and curve[0]["equity"] == 25060.0
    assert curve[1]["cum_realized"] == 110.0 and curve[1]["equity"] == 25110.0


def test_equity_curve_empty_when_nothing_closed():
    assert pa.equity_curve([_pos(status="OPEN", exit_ts=None, realized_pnl=None)]) == []
    assert pa.equity_curve([]) == []


# ── excursion_stats ──────────────────────────────────────────────────────────
def test_excursion_stats_aggregates_and_mfe_capture():
    positions = [
        _pos(realized_pnl=80.0, mae=-20.0, mfe=100.0),   # winner, captured 0.8 of peak
        _pos(realized_pnl=-50.0, mae=-60.0, mfe=10.0),   # loser
        _pos(realized_pnl=30.0, mae=None, mfe=None),     # no excursion → excluded
    ]
    st = pa.excursion_stats(positions)
    assert st["n"] == 2
    assert st["avg_mae"] == -40.0 and st["avg_mfe"] == 55.0
    # mfe_capture = mean(80/100, -50/10) over mfe>0 = mean(0.8, -5.0) = -2.1
    assert st["mfe_capture"] == -2.1
    assert st["avg_mae_on_winners"] == -20.0   # only the winner's mae


def test_excursion_stats_empty_when_no_excursions():
    st = pa.excursion_stats([_pos(mae=None, mfe=None)])
    assert st["n"] == 0 and st["mfe_capture"] is None and st["avg_mae_on_winners"] is None


def test_build_analytics_bundles_two_views():
    positions = [_pos(realized_pnl=-100.0), _pos(realized_pnl=80.0, mae=-10.0, mfe=90.0)]
    out = pa.build_analytics(positions, starting_balance=25000.0)
    assert set(out) == {"equity_curve", "excursions"}
    assert out["excursions"]["n"] == 1
