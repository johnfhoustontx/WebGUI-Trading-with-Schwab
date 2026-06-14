"""Tests for GEX/Charm intraday history helpers."""
import sys
import os
from datetime import datetime
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamma_tool import GammaEngine, extrapolate_linear


class TestSnapshotSummary:
    def test_extracts_top_positive_and_negative(self):
        data = {
            "spot": 6880.0,
            "gex": {
                6875.0: {"call": 10.0, "put": -5.0, "net": 5.0},
                6880.0: {"call": 200.0, "put": -50.0, "net": 150.0},
                6885.0: {"call": 20.0, "put": -300.0, "net": -280.0},
                6890.0: {"call": 15.0, "put": -10.0, "net": 5.0},
            },
        }
        summary = GammaEngine.snapshot_summary(data)
        assert summary["top_pos_strike"] == 6880.0
        assert summary["top_neg_strike"] == 6885.0
        assert summary["net_total"] == 5.0 + 150.0 + (-280.0) + 5.0

    def test_flip_point_between_pos_and_neg(self):
        data = {
            "spot": 6880.0,
            "gex": {
                6875.0: {"call": 0, "put": 0, "net": 100.0},
                6880.0: {"call": 0, "put": 0, "net": 50.0},
                6885.0: {"call": 0, "put": 0, "net": -80.0},
            },
        }
        summary = GammaEngine.snapshot_summary(data)
        # Linear interpolation: 6880 + 5 * 50/130 ≈ 6881.92
        assert summary["flip"] == pytest.approx(6881.92, abs=0.01)

    def test_empty_gex_returns_none_fields(self):
        data = {"spot": 6880.0, "gex": {}}
        summary = GammaEngine.snapshot_summary(data)
        assert summary["top_pos_strike"] is None
        assert summary["top_neg_strike"] is None
        assert summary["net_total"] == 0.0
        assert summary["flip"] is None

    def test_flip_picks_crossing_nearest_spot_when_multiple(self):
        # Two crossings within 3% band — nearest to spot wins
        data = {
            "spot": 6880.0,
            "gex": {
                6870.0: {"call": 0, "put": 0, "net": 100.0},
                6872.0: {"call": 0, "put": 0, "net": -80.0},  # crossing #1 ~6871.1
                6874.0: {"call": 0, "put": 0, "net": -50.0},
                6878.0: {"call": 0, "put": 0, "net": 60.0},
                6882.0: {"call": 0, "put": 0, "net": -40.0},  # crossing #2 ~6880.4 (closest to spot)
            },
        }
        summary = GammaEngine.snapshot_summary(data)
        assert summary["flip"] is not None
        # Second crossing (~6880.4) is closest to spot
        assert 6879.5 < summary["flip"] < 6881.0

    def test_flip_outside_band_returns_none(self):
        # Crossing exists but is > 3% away from spot — should be ignored
        data = {
            "spot": 6880.0,
            "gex": {
                6500.0: {"call": 0, "put": 0, "net": 100.0},
                6510.0: {"call": 0, "put": 0, "net": -100.0},  # far from spot
            },
        }
        summary = GammaEngine.snapshot_summary(data)
        assert summary["flip"] is None


class TestExtrapolateLinear:
    def test_flat_series_stays_flat(self):
        ts = [0.0, 60.0, 120.0, 180.0]
        ys = [100.0, 100.0, 100.0, 100.0]
        projected = extrapolate_linear(ts, ys, 300.0)
        assert abs(projected - 100.0) < 1e-6

    def test_linear_trend_projects_correctly(self):
        ts = [0.0, 60.0, 120.0]
        ys = [100.0, 110.0, 120.0]
        projected = extrapolate_linear(ts, ys, 180.0)
        assert abs(projected - 130.0) < 1e-6

    def test_too_few_points_returns_last_value(self):
        assert extrapolate_linear([], [], 100.0) is None
        assert extrapolate_linear([0.0], [42.0], 100.0) == 42.0

    def test_degenerate_time_axis_returns_last_value(self):
        ts = [0.0, 0.0, 0.0]
        ys = [100.0, 110.0, 120.0]
        projected = extrapolate_linear(ts, ys, 100.0)
        assert projected == 120.0


from gamma_tool import eod_narrative


def _summary(spot, flip, top_pos, top_neg, net_total):
    return {"spot": spot, "flip": flip, "top_pos_strike": top_pos,
            "top_neg_strike": top_neg, "net_total": net_total}


class TestEodNarrative:
    def test_empty_history_returns_insufficient(self):
        result = eod_narrative([])
        assert "INSUFFICIENT" in result["scenario"].upper() or "NEUTRAL" in result["scenario"].upper()

    def test_flat_flip_and_net_returns_pin(self):
        hist = [_summary(6880, 6885, 6900, 6870, 1e9) for _ in range(5)]
        result = eod_narrative(hist)
        assert "PIN" in result["scenario"].upper()

    def test_flip_rising_returns_upward(self):
        hist = [_summary(6880, 6880 + i * 3, 6900, 6870, 1e9) for i in range(5)]
        result = eod_narrative(hist)
        assert "UP" in result["scenario"].upper() or "HIGHER" in result["scenario"].upper()

    def test_net_flipping_negative_returns_volatility(self):
        hist = [_summary(6880, 6885, 6900, 6870, 1e9 - i * 5e8) for i in range(5)]
        # Last net is -1e9 (negative)
        result = eod_narrative(hist)
        assert "VOL" in result["scenario"].upper() or "BREAKOUT" in result["scenario"].upper()

    def test_charm_downward_omits_target_strike(self):
        # Flip migrating down; top_neg_strike on opposite side of spot (charm).
        hist = [_summary(6983, 7000 - i * 8, 6950, 7025, 1e9) for i in range(5)]
        result = eod_narrative(hist, is_charm=True)
        assert "DOWNWARD" in result["scenario"].upper()
        assert "7025" not in result["scenario"]
        # Magnitude still reported.
        assert "pts" in result["scenario"].lower()

    def test_charm_upward_omits_target_strike(self):
        hist = [_summary(6983, 6960 + i * 8, 6950, 7025, 1e9) for i in range(5)]
        result = eod_narrative(hist, is_charm=True)
        assert "UP" in result["scenario"].upper() or "DRIFT" in result["scenario"].upper()
        assert "6950" not in result["scenario"]

    def test_gex_downward_keeps_target_strike(self):
        # Default (is_charm=False) still includes the target strike.
        hist = [_summary(6983, 7000 - i * 8, 7025, 6950, 1e9) for i in range(5)]
        result = eod_narrative(hist)
        assert "6950" in result["scenario"]
