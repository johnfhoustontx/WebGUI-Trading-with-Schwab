"""Tests for normalized sub-score primitives."""
import pytest

from src.analysis.scoring import (
    score_rsi,
    score_adx_directional,
    score_macd,
    score_relative_volume,
    score_vwap,
    score_volume_profile_location,
    score_relative_strength_percentile,
    score_distance_from_52wk_high,
    score_pe_vs_sector,
    score_peg,
    score_growth_metric,
    score_roe,
    score_margin_trend,
    score_earnings_surprise_streak,
    score_guidance_direction,
)


class TestScoreRsi:
    @pytest.mark.parametrize("rsi,expected", [
        (50, 60),
        (55, 60),
        (59.99, 60),
        (60, 30),
        (65, 30),
        (70, 30),
        (70.01, -20),
        (80, -20),
        (49.99, -20),
        (40, -20),
        (45, -20),
        (39.99, -60),
        (30, -60),
        (35, -60),
        (29.99, -90),
        (10, -90),
    ])
    def test_score_rsi(self, rsi, expected):
        assert score_rsi(rsi) == expected


class TestScoreAdxDirectional:
    @pytest.mark.parametrize("adx,slope,expected", [
        (30, 1.0, 100),
        (25, 0.5, 100),
        (30, -1.0, -100),
        (25, -0.5, -100),
        (24.99, 1.0, 60),
        (20, 1.0, 60),
        (20, -1.0, -60),
        (24.99, -0.1, -60),
        (19.99, 1.0, 30),
        (15, 1.0, 30),
        (15, -1.0, -30),
        (19.99, -0.1, -30),
        (14.99, 1.0, 0),
        (0, 1.0, 0),
        (10, -1.0, 0),
        (25, 0, 100),  # slope=0 -> direction +1
    ])
    def test_score_adx_directional(self, adx, slope, expected):
        assert score_adx_directional(adx, slope) == expected


class TestScoreMacd:
    @pytest.mark.parametrize("hist,prev,expected", [
        (1.0, 0.5, 80),     # >0 rising
        (1.0, 0.5, 80),
        (0.5, 1.0, 30),     # >0 falling
        (1.0, 1.0, 30),     # >0 not rising (equal)
        (-0.5, -1.0, -20),  # <=0 rising
        (0, -1.0, -20),     # <=0 rising
        (-1.0, -0.5, -80),  # <=0 falling
        (-1.0, -1.0, -80),  # <=0 equal
        (0, 0, -80),        # 0 equal
    ])
    def test_score_macd(self, hist, prev, expected):
        assert score_macd(hist, prev) == expected


class TestScoreRelativeVolume:
    @pytest.mark.parametrize("rv,slope,expected", [
        (2.0, 1.0, 60),
        (1.51, 1.0, 60),
        (2.0, -1.0, -60),
        (1.5, 1.0, 20),
        (1.0, 1.0, 20),
        (1.5, -1.0, -20),
        (1.0, -1.0, -20),
        (0.69, 1.0, -30),
        (0.5, -1.0, -30),
        (0.7, 1.0, 0),
        (0.99, -1.0, 0),
        (1.0, 0, 20),  # slope=0 -> +1 direction
    ])
    def test_score_relative_volume(self, rv, slope, expected):
        assert score_relative_volume(rv, slope) == expected


class TestScoreVwap:
    @pytest.mark.parametrize("price,vwap,expected", [
        (101.0, 100.0, 30),    # exactly 1% above -> +30 (>= 1%)
        (100.5, 100.0, 60),    # 0.5% above
        (100.01, 100.0, 60),   # just above
        (102.0, 100.0, 30),    # 2% above
        (101.01, 100.0, 30),   # just over 1%
        (99.5, 100.0, -40),    # 0.5% below
        (99.0, 100.0, -80),    # exactly 1% below -> -80 (>= 1%)
        (98.0, 100.0, -80),    # 2% below
        (98.99, 100.0, -80),   # just over 1% below
    ])
    def test_score_vwap(self, price, vwap, expected):
        assert score_vwap(price, vwap) == expected


class TestScoreVolumeProfileLocation:
    def test_above_poc_within_vah(self):
        vp = {"poc": 100.0, "vah": 105.0, "val": 95.0}
        assert score_volume_profile_location(103.0, vp) == 50

    def test_at_vah(self):
        vp = {"poc": 100.0, "vah": 105.0, "val": 95.0}
        assert score_volume_profile_location(105.0, vp) == 50

    def test_within_half_pct_of_poc(self):
        vp = {"poc": 100.0, "vah": 105.0, "val": 95.0}
        assert score_volume_profile_location(100.4, vp) == 0
        assert score_volume_profile_location(99.6, vp) == 0

    def test_below_val(self):
        vp = {"poc": 100.0, "vah": 105.0, "val": 95.0}
        assert score_volume_profile_location(94.0, vp) == -60

    def test_otherwise(self):
        vp = {"poc": 100.0, "vah": 105.0, "val": 95.0}
        # above vah - not the +50 case, not near poc, not below val
        assert score_volume_profile_location(110.0, vp) == 0


class TestScoreRelativeStrengthPercentile:
    @pytest.mark.parametrize("pct,expected", [
        (1.0, 100),
        (0.5, 0),
        (0.0, -100),
        (0.75, 50),
        (0.25, -50),
    ])
    def test_score_rs_percentile(self, pct, expected):
        assert score_relative_strength_percentile(pct) == expected


class TestScoreDistanceFrom52wkHigh:
    @pytest.mark.parametrize("dist,expected", [
        (0.0, 60),
        (0.05, 60),
        (0.06, 20),
        (0.15, 20),
        (0.16, -20),
        (0.30, -20),
        (0.31, -60),
        (0.50, -60),
    ])
    def test_distance(self, dist, expected):
        assert score_distance_from_52wk_high(dist) == expected


class TestScorePeVsSector:
    def test_none_pe(self):
        assert score_pe_vs_sector(None, 20.0) == 0

    def test_none_sector(self):
        assert score_pe_vs_sector(15.0, None) == 0

    def test_both_none(self):
        assert score_pe_vs_sector(None, None) == 0

    @pytest.mark.parametrize("pe,sec,expected", [
        (14.0, 20.0, 60),   # ratio 0.7
        (10.0, 20.0, 60),   # ratio 0.5
        (15.0, 20.0, 30),   # ratio 0.75
        (20.0, 20.0, 30),   # ratio 1.0
        (25.0, 20.0, -10),  # ratio 1.25
        (26.0, 20.0, -10),  # ratio 1.3
        (30.0, 20.0, -50),  # ratio 1.5
    ])
    def test_ratios(self, pe, sec, expected):
        assert score_pe_vs_sector(pe, sec) == expected


class TestScorePeg:
    def test_none(self):
        assert score_peg(None) == 0

    @pytest.mark.parametrize("peg,expected", [
        (0.5, 40),
        (0.99, 40),
        (1.0, 0),
        (1.5, 0),
        (2.0, 0),
        (2.01, -40),
        (3.0, -40),
    ])
    def test_peg(self, peg, expected):
        assert score_peg(peg) == expected


class TestScoreGrowthMetric:
    def test_none(self):
        assert score_growth_metric(None) == 0

    @pytest.mark.parametrize("g,expected", [
        (0.20, 80),
        (0.16, 80),
        (0.15, 30),
        (0.05, 30),
        (0.04, 0),
        (0.0, 0),
        (-0.01, -30),
        (-0.05, -30),
        (-0.06, -80),
        (-0.20, -80),
    ])
    def test_growth(self, g, expected):
        assert score_growth_metric(g) == expected


class TestScoreRoe:
    def test_none(self):
        assert score_roe(None) == 0

    @pytest.mark.parametrize("roe,expected", [
        (0.20, 60),
        (0.16, 60),
        (0.15, 20),
        (0.10, 20),
        (0.05, 20),
        (0.04, -40),
        (0.0, -40),
        (-0.10, -40),
    ])
    def test_roe(self, roe, expected):
        assert score_roe(roe) == expected


class TestScoreMarginTrend:
    def test_none(self):
        assert score_margin_trend(None) == 0

    def test_true(self):
        assert score_margin_trend(True) == 30

    def test_false(self):
        assert score_margin_trend(False) == -30


class TestScoreEarningsSurpriseStreak:
    def test_none(self):
        assert score_earnings_surprise_streak(None) == 0

    def test_empty(self):
        assert score_earnings_surprise_streak([]) == 0

    def test_all_four_strong_beats(self):
        assert score_earnings_surprise_streak([0.10, 0.08, 0.07, 0.06]) == 80

    def test_last_missed(self):
        # last is index -1
        assert score_earnings_surprise_streak([0.10, 0.08, 0.07, -0.02]) == -60

    def test_otherwise(self):
        assert score_earnings_surprise_streak([0.10, 0.08, 0.07, 0.03]) == 0

    def test_three_only_beats(self):
        # only three entries, all strong - not "all four"
        assert score_earnings_surprise_streak([0.10, 0.08, 0.07]) == 0

    # ── ordering ────────────────────────────────────────────────────────────
    # The list is CHRONOLOGICAL: `[-1]` is the most recent quarter, which is
    # what `test_last_missed` above asserts and what `Fundamentals` builds.
    # Every fixture above holds exactly four entries, so `[:4]` and `[-4:]` are
    # the same slice and the distinction never showed. Real vendor data runs to
    # 100+ quarters, where checking the FIRST four means scoring a streak from
    # the 1990s.

    def test_the_streak_is_the_MOST_RECENT_four_not_the_oldest(self):
        old_beats_then_flat = [0.10, 0.09, 0.08, 0.07,   # ancient history
                               0.01, 0.01, 0.01, 0.01]   # the last year
        assert score_earnings_surprise_streak(old_beats_then_flat) == 0

    def test_a_recent_streak_scores_even_with_a_long_weak_history(self):
        weak_then_strong = [-0.20, 0.00, 0.01, 0.02,
                            0.10, 0.08, 0.07, 0.06]
        assert score_earnings_surprise_streak(weak_then_strong) == 80

    def test_a_miss_in_the_latest_quarter_still_dominates(self):
        assert score_earnings_surprise_streak(
            [0.10, 0.08, 0.07, 0.06, 0.09, -0.02]) == -60


class TestScoreGuidanceDirection:
    def test_none(self):
        assert score_guidance_direction(None) == 0

    def test_raised(self):
        assert score_guidance_direction("RAISED") == 40

    def test_lowered(self):
        assert score_guidance_direction("LOWERED") == -60

    def test_cut(self):
        assert score_guidance_direction("CUT") == -60

    def test_maintained(self):
        assert score_guidance_direction("MAINTAINED") == 0

    def test_unknown(self):
        assert score_guidance_direction("WHATEVER") == 0
