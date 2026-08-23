"""Tests for GEX/Charm intraday history helpers."""
import sys
import os
from datetime import datetime
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamma_tool import GammaEngine


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
        # Four strikes, not three: the sign must be seen to HOLD two live strikes
        # either side of the crossing (see TestFlipIgnoresDeadStrikes), and a
        # three-strike fixture cannot show that. Real grids carry hundreds.
        data = {
            "spot": 6880.0,
            "gex": {
                6875.0: {"call": 0, "put": 0, "net": 100.0},
                6880.0: {"call": 0, "put": 0, "net": 50.0},
                6885.0: {"call": 0, "put": 0, "net": -80.0},
                6890.0: {"call": 0, "put": 0, "net": -100.0},
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
                6890.0: {"call": 0, "put": 0, "net": -100.0},
            },
        }
        assert GammaEngine.snapshot_summary(data)["flip"] == pytest.approx(
            6881.92, abs=0.01)

    def test_a_genuine_crossing_survives_dead_strikes_around_it(self):
        """Dead strikes must not shadow the real sign change. The old rule
        offered the dead-strike boundary at 6890 as a candidate and, being
        nearest spot (6889), returned it instead of the real flip.

        This also pins that dead strikes are SKIPPED when checking that the sign
        holds, rather than counted as breaking it — on an index ladder carrying
        ~135 dead strikes, treating a zero as a sign break would reject most
        genuine flips."""
        data = {
            "spot": 6889.0,
            "gex": {
                6870.0: {"call": 0, "put": 0, "net": 150.0},
                6875.0: {"call": 0, "put": 0, "net": 120.0},
                6880.0: {"call": 0, "put": 0, "net": -60.0},   # the real flip
                6882.0: {"call": 0, "put": 0, "net": -70.0},
                6885.0: {"call": 0, "put": 0, "net": 0.0},     # dead
                6890.0: {"call": 0, "put": 0, "net": 0.0},     # dead
                6895.0: {"call": 0, "put": 0, "net": 90.0},
            },
        }
        flip = GammaEngine.snapshot_summary(data)["flip"]
        assert flip == pytest.approx(6878.33, abs=0.01), (
            "must report the genuine sign change, not a dead-strike boundary")

    def test_a_crossing_that_immediately_reverses_is_not_a_flip(self):
        """Oscillation, not structure. The profile pops negative for ONE strike
        and returns - that is a lumpy strike, not a regime boundary. Requiring
        the sign to hold either side is what separates the two."""
        data = {
            "spot": 6880.0,
            "gex": {
                6870.0: {"call": 0, "put": 0, "net": 100.0},
                6875.0: {"call": 0, "put": 0, "net": 90.0},
                6880.0: {"call": 0, "put": 0, "net": -70.0},   # a single dip
                6885.0: {"call": 0, "put": 0, "net": 80.0},
                6890.0: {"call": 0, "put": 0, "net": 95.0},
            },
        }
        assert GammaEngine.snapshot_summary(data)["flip"] is None

    def test_a_sustained_sign_change_IS_a_flip(self):
        """The real thing: positive above, negative below, and it holds."""
        data = {
            "spot": 6880.0,
            "gex": {
                6870.0: {"call": 0, "put": 0, "net": 120.0},
                6875.0: {"call": 0, "put": 0, "net": 60.0},
                6880.0: {"call": 0, "put": 0, "net": -40.0},
                6885.0: {"call": 0, "put": 0, "net": -90.0},
            },
        }
        # 6875 -> 6880 crosses: 6875 + 5 * 60/100 = 6878.0
        assert GammaEngine.snapshot_summary(data)["flip"] == pytest.approx(
            6878.0, abs=0.01)

    def test_an_oscillating_profile_reports_the_SUSTAINED_crossing(self):
        """$NDX's shape in miniature: noise crossings nearer spot than the real
        one. The old rule took the nearest, which is why the level tracked spot;
        the sustained crossing is the level that means something."""
        data = {
            "spot": 6900.0,
            "gex": {
                6860.0: {"call": 0, "put": 0, "net": 150.0},
                6865.0: {"call": 0, "put": 0, "net": 110.0},
                6870.0: {"call": 0, "put": 0, "net": -80.0},   # sustained flip
                6875.0: {"call": 0, "put": 0, "net": -120.0},
                6890.0: {"call": 0, "put": 0, "net": 60.0},    # noise: one up...
                6895.0: {"call": 0, "put": 0, "net": -55.0},   # ...one down
                6900.0: {"call": 0, "put": 0, "net": 70.0},    # ...one up
            },
        }
        flip = GammaEngine.snapshot_summary(data)["flip"]
        # 6865 + 5 * 110/190 = 6867.89 — the sustained crossing, ~32 points from
        # spot, chosen over three oscillation crossings sitting right beside it.
        assert flip == pytest.approx(6867.89, abs=0.05), (
            "must report the sustained crossing, not the oscillation beside spot")

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
            # Both crossings are SUSTAINED (the sign holds two live strikes each
            # side), so both reach the nearest-to-spot tie-break this test is
            # about. The old fixture's crossings were single-strike wobbles,
            # which no longer qualify as levels at all.
            "gex": {
                6864.0: {"call": 0, "put": 0, "net": 120.0},
                6866.0: {"call": 0, "put": 0, "net": 110.0},
                6868.0: {"call": 0, "put": 0, "net": 100.0},
                6872.0: {"call": 0, "put": 0, "net": -80.0},  # crossing #1 ~6870.2
                6874.0: {"call": 0, "put": 0, "net": -70.0},
                6876.0: {"call": 0, "put": 0, "net": 90.0},
                6878.0: {"call": 0, "put": 0, "net": 85.0},
                6882.0: {"call": 0, "put": 0, "net": -60.0},  # crossing #3 ~6880.3 (closest to spot)
                6884.0: {"call": 0, "put": 0, "net": -70.0},
            },
        }
        summary = GammaEngine.snapshot_summary(data)
        assert summary["flip"] is not None
        # The crossing nearest spot (~6880.3) wins over the ones at ~6870.2/~6874.9
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
