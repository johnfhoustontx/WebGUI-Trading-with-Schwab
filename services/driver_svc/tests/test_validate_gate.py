"""Unit tests for the pure pieces of the offline directional-gate backtest harness.

The harness itself does I/O (reads the driver DB + gex_history) and is run manually; only
its pure transforms — trend→posture and the impact tally — are unit-tested here.
"""
from services.driver_svc import validate_directional_gate as v


def test_trend_to_posture_bands():
    assert v.trend_to_posture(0.5, 0.3) == "up"
    assert v.trend_to_posture(-0.5, 0.3) == "down"
    assert v.trend_to_posture(0.1, 0.3) == "neutral"     # below threshold
    assert v.trend_to_posture(-0.1, 0.3) == "neutral"
    assert v.trend_to_posture(0.3, 0.3) == "up"          # exactly at threshold
    assert v.trend_to_posture(None, 0.3) == "neutral"    # uncovered


def test_tally_saved_forgone_net():
    rows = [
        {"strategy": "CCS", "pnl": -300.0, "posture": "up", "blocked": True},    # saved loss
        {"strategy": "CCS", "pnl": +50.0, "posture": "up", "blocked": True},     # forgone win
        {"strategy": "PCS", "pnl": +64.0, "posture": "neutral", "blocked": False},  # kept win
        {"strategy": "PCS", "pnl": -40.0, "posture": "neutral", "blocked": False},  # kept loss
    ]
    agg = v.tally(rows)
    assert agg["blocked"] == 2 and agg["kept"] == 2
    assert agg["saved"] == 300.0 and agg["forgone"] == 50.0
    assert agg["net_impact"] == 250.0
    assert agg["kept_realized"] == 24.0            # 64 - 40
    assert agg["kept_win_rate"] == 0.5             # 1 of 2 kept is a winner


def test_tally_empty():
    agg = v.tally([])
    assert agg["blocked"] == 0 and agg["kept"] == 0
    assert agg["net_impact"] == 0.0 and agg["kept_win_rate"] == 0.0
