"""A NaN, an infinity or a bool is no reading on the performance views.

Measured 2026-11-28 before the fix (group 2 of the numeric-guard audit): the
helpers returned any int or float unchanged, so a NaN realized P&L made the
scorecard's total ``nan``, an infinite one made the profit factor ``inf``, a
``True`` counted as a $1 win, and a NaN or infinite sentiment score made
``build_gamma_read`` raise. Each bad input must now read exactly as a missing
one does.
"""
import pytest

from services.options_svc import book_perf, compute, perf_analytics

BAD = [float("nan"), float("inf"), float("-inf"), True, "nan", "inf"]
IDS = ["nan", "inf", "-inf", "True", "'nan'", "'inf'"]

CLOSED = [
    {"status": "CLOSED", "symbol": "SPY", "strategy": "PCS", "exit_reason": "TARGET",
     "realized_pnl": 120.0, "mae": -40.0, "mfe": 150.0, "exit_ts": "2026-11-20T10:00"},
    {"status": "CLOSED", "symbol": "QQQ", "strategy": "CCS", "exit_reason": "STOP",
     "realized_pnl": -80.0, "mae": -90.0, "mfe": 20.0, "exit_ts": "2026-11-21T10:00"},
]


def _with(**kw):
    row = {"status": "CLOSED", "symbol": "IWM", "strategy": "PCS", "exit_reason": "TARGET",
           "realized_pnl": 50.0, "mae": -10.0, "mfe": 60.0, "exit_ts": "2026-11-22T10:00"}
    row.update(kw)
    return CLOSED + [row]


GX = {"flip": 500.0}
CH = {"flip": 501.0, "top_pos_strike": 505.0, "top_neg_strike": 495.0}
DX = {"net_total": 1.5e9, "hedge_pressure": 2.0e8, "projected_flip": 502.0, "flip": 499.0}
VN = {"net_total": 3.0e8}
WALLS = {"call_wall": 510.0, "put_wall": 490.0}
REG = {"composite_score": 7.2, "aggregate_confidence": 80, "bias": "bullish"}


def _gamma(**over):
    args = dict(symbol="SPY", spot=500.0, gex_summary=GX, charm_summary=CH,
                dex_summary=DX, vanna_summary=VN, walls=WALLS, regime=REG)
    args.update(over)
    return vars(compute.build_gamma_read(**args))


CASES = {
    "scorecard realized_pnl":
        lambda v: book_perf.build_scorecard(_with(realized_pnl=v), {}),
    "scorecard open_unrealized":
        lambda v: book_perf.build_scorecard(CLOSED, {"open_unrealized": v}),
    "scorecard session_pnl":
        lambda v: book_perf.build_scorecard(CLOSED, {"session_pnl": v}),
    "equity_curve realized_pnl":
        lambda v: perf_analytics.equity_curve(_with(realized_pnl=v)),
    "excursions mae": lambda v: perf_analytics.excursion_stats(_with(mae=v)),
    "excursions mfe": lambda v: perf_analytics.excursion_stats(_with(mfe=v)),
    "excursions realized_pnl":
        lambda v: perf_analytics.excursion_stats(_with(realized_pnl=v)),
    "gamma spot": lambda v: _gamma(spot=v),
    "gamma call_wall": lambda v: _gamma(walls={**WALLS, "call_wall": v}),
    "gamma flip": lambda v: _gamma(gex_summary={**GX, "flip": v}),
    "gamma dex net_total": lambda v: _gamma(dex_summary={**DX, "net_total": v}),
    "gamma hedge_pressure": lambda v: _gamma(dex_summary={**DX, "hedge_pressure": v}),
    "gamma projected_flip": lambda v: _gamma(dex_summary={**DX, "projected_flip": v}),
    "gamma delta flip": lambda v: _gamma(dex_summary={**DX, "flip": v}),
    "gamma vanna net_total": lambda v: _gamma(vanna_summary={**VN, "net_total": v}),
    "gamma composite_score":
        lambda v: _gamma(regime={**REG, "composite_score": v}),
    "gamma aggregate_confidence":
        lambda v: _gamma(regime={**REG, "aggregate_confidence": v}),
}


@pytest.mark.parametrize("bad", BAD, ids=IDS)
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_bad_number_reads_as_a_missing_one(case, bad):
    build = CASES[case]
    assert repr(build(bad)) == repr(build(None))


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_cases_are_not_vacuous(case):
    # Each case's input is actually read: a real number differs from None.
    build = CASES[case]
    real = {"gamma spot": 480.0, "gamma composite_score": 2.0,
            "gamma aggregate_confidence": 40.0}.get(case, 7.0)
    assert repr(build(real)) != repr(build(None))


def test_an_int_is_still_read():
    card = book_perf.build_scorecard(_with(realized_pnl=50), {"open_unrealized": 5})
    assert card["realized_pnl"] == 90.0 and card["total_pnl"] == 95.0
    assert _gamma(regime={**REG, "composite_score": 3})["sentiment_score"] == 3
