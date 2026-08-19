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


class TestFlipIgnoresDeadStrikes:
    """A strike with net GEX of exactly zero is the ABSENCE of data, not a level.

    Index chains list far more strikes than trade: measured 2026-08-19, $NDX
    carried ~135 zero-net strikes and $SPX ~45 inside the +/-3% search band, while
    SPY and QQQ carried NONE. With a non-strict `v1 * v2 <= 0` test, every
    boundary between a dead run and live data manufactured a crossing - 8.9 of
    $NDX's 23.6 candidates per snapshot were artifacts of untraded strikes.

    That inflated candidate set is what makes the "nearest to spot" selection
    degenerate into "report a level near spot", which is why the index flip
    tracked spot (corr +0.85/+0.97) while the ETFs did not (-0.10/-0.47).

    Design: docs/plans/2026-08-19-gamma-flip-spot-tracking-design.md
    """

    def test_a_dead_strike_beside_live_data_is_not_a_crossing(self):
        """The whole defect, minimally. Net stays POSITIVE either side of the
        dead strike - there is no sign change here, and reporting one invents a
        flip out of an untraded strike."""
        data = {
            "spot": 6880.0,
            "gex": {
                6875.0: {"call": 0, "put": 0, "net": 100.0},
                6880.0: {"call": 0, "put": 0, "net": 0.0},    # listed, untraded
                6885.0: {"call": 0, "put": 0, "net": 80.0},
            },
        }
        assert GammaEngine.snapshot_summary(data)["flip"] is None

    def test_a_run_of_dead_strikes_yields_no_flip(self):
        """$NDX's real shape: a long dead stretch through the money. Two
        boundaries, so the old rule offered two spurious candidates and picked
        whichever sat nearer spot - a value that moves as spot moves."""
        gex = {6860.0: {"call": 0, "put": 0, "net": 250.0}}
        for k in (6865.0, 6870.0, 6875.0, 6880.0, 6885.0, 6890.0):
            gex[k] = {"call": 0, "put": 0, "net": 0.0}
        gex[6895.0] = {"call": 0, "put": 0, "net": 180.0}
        assert GammaEngine.snapshot_summary(
            {"spot": 6880.0, "gex": gex})["flip"] is None

    def test_a_GENUINE_sign_change_still_reports_a_flip(self):
        """The filter must not cost real crossings - this is the level the page
        exists to show."""
        data = {
            "spot": 6880.0,
            "gex": {
                6875.0: {"call": 0, "put": 0, "net": 100.0},
                6880.0: {"call": 0, "put": 0, "net": 50.0},
                6885.0: {"call": 0, "put": 0, "net": -80.0},
            },
        }
        assert GammaEngine.snapshot_summary(data)["flip"] == pytest.approx(
            6881.92, abs=0.01)

    def test_a_genuine_crossing_survives_dead_strikes_around_it(self):
        """Dead strikes on both sides must not shadow the real sign change: the
        old rule would have offered three candidates and taken the nearest to
        spot, which is the dead-strike boundary at 6890, not the real flip."""
        data = {
            "spot": 6889.0,
            "gex": {
                6875.0: {"call": 0, "put": 0, "net": 120.0},
                6880.0: {"call": 0, "put": 0, "net": -60.0},   # the real flip
                6885.0: {"call": 0, "put": 0, "net": 0.0},     # dead
                6890.0: {"call": 0, "put": 0, "net": 0.0},     # dead
                6895.0: {"call": 0, "put": 0, "net": 90.0},
            },
        }
        flip = GammaEngine.snapshot_summary(data)["flip"]
        assert flip == pytest.approx(6878.33, abs=0.01), (
            "must report the genuine sign change, not a dead-strike boundary")

    def test_the_ETF_shape_is_untouched_by_the_filter(self):
        """The regression guard that matters. SPY and QQQ carry no zero-net
        strikes near the money, so the strict comparison must be a no-op for
        them - a fix that moved the two symbols already behaving correctly would
        be a worse defect than the one it fixes."""
        data = {
            "spot": 770.0,
            "gex": {
                768.0: {"call": 40.0, "put": -10.0, "net": 30.0},
                769.0: {"call": 30.0, "put": -12.0, "net": 18.0},
                770.0: {"call": 20.0, "put": -25.0, "net": -5.0},
                771.0: {"call": 10.0, "put": -30.0, "net": -20.0},
            },
        }
        # 769 -> 770 crosses zero: 769 + 1 * 18/23 = 769.78
        assert GammaEngine.snapshot_summary(data)["flip"] == pytest.approx(
            769.78, abs=0.01)

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
