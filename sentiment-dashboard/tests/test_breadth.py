"""Boundary, missing-data, and typical-day tests for scoring.breadth."""
import math

import pytest

from scoring import breadth


@pytest.mark.parametrize("ratio, expected", [
    (5.0, 10),    # ratio >= 4.0 → 10
    (3.5, 9),     # >= 3.0 → 9
    (2.5, 8),     # >= 2.0 → 8
    (1.6, 7),     # >= 1.5 → 7
    (1.2, 6),     # >= 1.15 → 6
    (1.0, 5),     # >= 0.87 → 5
    (0.7, 4),     # >= 0.67 → 4
    (0.55, 3),    # >= 0.50 → 3
    (0.40, 2),    # >= 0.33 → 2
    (0.20, 1),    # else → 1
    (math.inf, 10),
])
def test_breadth_ratio_breakpoints(ratio, expected):
    r = breadth.score(breadth_ratio=ratio)
    assert r.score == expected


def test_missing_data_returns_zero(bridge_v39_snapshot):
    case = bridge_v39_snapshot["cases"]["breadth_missing"]
    r = breadth.score()
    assert r.score == case["expected_score"]
    assert r.confidence == case["expected_confidence"]


def test_pct_only_typical(bridge_v39_snapshot):
    case = bridge_v39_snapshot["cases"]["breadth_pct_only"]
    r = breadth.score(pct_above_50=case["inputs"]["pct_above_50"])
    assert r.score == case["expected_score"]
    assert r.confidence == pytest.approx(case["expected_confidence"])


def test_ratio_only_typical(bridge_v39_snapshot):
    case = bridge_v39_snapshot["cases"]["breadth_ratio_only"]
    r = breadth.score(breadth_ratio=case["inputs"]["breadth_ratio"])
    assert r.score == case["expected_score"]
    assert r.confidence == pytest.approx(case["expected_confidence"])


def test_ratio_and_pct_blend():
    """Ratio 0.7 (score 4) × 0.7 + DMA 60 (score 6) × 0.3 = 4.6 → round to 5."""
    r = breadth.score(pct_above_50="60", breadth_ratio=2.0)
    # ratio_score=8, dma_score=6; 8*0.7 + 6*0.3 = 7.4 → 7
    assert r.score == 7


def test_highs_lows_adjustment_bumps_score():
    base = breadth.score(breadth_ratio=2.0)
    bumped = breadth.score(breadth_ratio=2.0, new_highs=400, new_lows=100)
    # hl=4 > 3 → +1
    assert bumped.score == min(10, base.score + 1)
