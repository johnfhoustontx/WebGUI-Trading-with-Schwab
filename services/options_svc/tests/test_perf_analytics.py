"""Tests for perf_analytics — the PURE driver-book analytics (no I/O)."""
import json

from services.options_svc import perf_analytics as pa


def _pos(**kw):
    base = {"status": "CLOSED", "exit_ts": "2026-07-10T15:00:00", "realized_pnl": 0.0,
            "strategy": "PCS", "mae": None, "mfe": None, "entry_context": None}
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


# ── posture_stance ───────────────────────────────────────────────────────────
def test_posture_stance_matrix():
    assert pa.posture_stance("CCS", "up") == "against"      # short calls into a rising tape
    assert pa.posture_stance("PCS", "down") == "against"
    assert pa.posture_stance("CCS", "down") == "with"
    assert pa.posture_stance("PCS", "up") == "with"
    assert pa.posture_stance("IC", "up") == "neutral"       # IC exempt
    assert pa.posture_stance("CCS", "neutral") == "neutral"
    assert pa.posture_stance("PCS", None) == "neutral"


# ── posture_postmortem ───────────────────────────────────────────────────────
def _ctx_pos(posture, strategy, pnl):
    return _pos(strategy=strategy, realized_pnl=pnl,
                entry_context=json.dumps({"posture": posture}))


def test_posture_postmortem_groups_and_computes_edge():
    positions = [
        _ctx_pos("up", "CCS", -100.0),   # against, loss
        _ctx_pos("up", "CCS", -60.0),    # against, loss
        _ctx_pos("up", "PCS", 80.0),     # with, win
        _ctx_pos("down", "PCS", 40.0),   # against (PCS in down), win
        _pos(entry_context=None, realized_pnl=999.0),   # no posture → ignored
    ]
    pm = pa.posture_postmortem(positions)
    assert pm["by_stance"]["against"]["trades"] == 3
    assert pm["by_stance"]["against"]["realized"] == -120.0
    assert pm["by_stance"]["with"]["trades"] == 1 and pm["by_stance"]["with"]["realized"] == 80.0
    # edge: with avg (80) beats against avg (-40) → positive delta
    assert pm["edge"]["with_avg"] == 80.0 and pm["edge"]["against_avg"] == -40.0
    assert pm["edge"]["avg_delta"] == 120.0
    assert pm["edge"]["n_with"] == 1 and pm["edge"]["n_against"] == 3


def test_posture_postmortem_accepts_dict_context_and_ignores_bad_json():
    positions = [
        _pos(strategy="CCS", realized_pnl=-50.0, entry_context={"posture": "up"}),  # dict
        _pos(strategy="CCS", realized_pnl=-50.0, entry_context="{not json"),        # bad → ignored
    ]
    pm = pa.posture_postmortem(positions)
    assert pm["by_stance"]["against"]["trades"] == 1   # only the parseable one counts


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


def test_build_analytics_bundles_three_views():
    positions = [_ctx_pos("up", "CCS", -100.0), _pos(realized_pnl=80.0, mae=-10.0, mfe=90.0)]
    out = pa.build_analytics(positions, starting_balance=25000.0)
    assert set(out) == {"equity_curve", "postmortem", "excursions"}
    assert out["excursions"]["n"] == 1
