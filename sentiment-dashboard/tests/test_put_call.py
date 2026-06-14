"""Boundary, missing-data, and typical-day tests for scoring.put_call."""
import pytest

from scoring import put_call


@pytest.mark.parametrize("pc, expected_base_score", [
    (1.31, 1),   # >=1.3 → 1
    (1.15, 2),   # >=1.1 → 2
    (0.95, 5),   # >=0.9 → 5
    (0.75, 8),   # >=0.7 → 8
    (0.50, 10),  # >=0.0 → 10
])
def test_score_breakpoints(pc, expected_base_score):
    """Threshold-table breakpoints with no MA / Normal skew."""
    r = put_call.score(pc, pc_ma=0.0, options_skew="Normal")
    assert r.score == expected_base_score


def test_score_missing(bridge_v39_snapshot):
    case = bridge_v39_snapshot["cases"]["put_call_missing"]
    i = case["inputs"]
    r = put_call.score(i["pc"], i["pc_ma"], i["skew"])
    assert r.score == 0
    assert r.confidence == 0.0


def test_score_call_dominant_typical(bridge_v39_snapshot):
    case = bridge_v39_snapshot["cases"]["put_call_call_dominant"]
    i = case["inputs"]
    r = put_call.score(i["pc"], i["pc_ma"], i["skew"])
    assert r.score == case["expected_score"]
    assert r.confidence == pytest.approx(case["expected_confidence"])
    assert "call-dominant" in r.interp


def test_score_with_ma_bump_and_inverted_skew(bridge_v39_snapshot):
    """MA ratio < 0.85 bumps +1; Inverted skew bumps +1 — clamped to 10."""
    case = bridge_v39_snapshot["cases"]["put_call_with_ma_and_skew"]
    i = case["inputs"]
    r = put_call.score(i["pc"], i["pc_ma"], i["skew"])
    assert r.score == case["expected_score"]
    assert r.confidence == pytest.approx(case["expected_confidence"])


def test_score_elevated_skew_drops_one():
    r_norm = put_call.score(0.75, pc_ma=0.0, options_skew="Normal")
    r_elev = put_call.score(0.75, pc_ma=0.0, options_skew="Elevated")
    assert r_elev.score == max(1, r_norm.score - 1)


def test_score_put_spike_via_ma_ratio_drops_one():
    """pc/ma > 1.15 → score -=1."""
    r = put_call.score(1.20, pc_ma=1.0, options_skew="Normal")
    # base for 1.20: >=1.1 → 2; r=1.20 > 1.15 → -1 → 1 (clamped)
    assert r.score == 1


# ── score_sector_weighted (v4.3) ───────────────────────────────────

SP_WEIGHTS = {
    "XLK": 32.53, "XLF": 13.42, "XLC": 10.16, "XLY":  9.94,
    "XLI":  8.86, "XLV":  8.63, "XLE":  4.89, "XLP":  4.61,
    "XLB":  2.74, "XLRE": 2.12, "XLU":  2.09,
}


def test_score_sector_weighted_call_dominated_bullish():
    """All sectors P/C < 0.9 → blended < 0.9 → score ≥ 8 (bullish)."""
    pcr = {etf: 0.75 for etf in SP_WEIGHTS}
    r = put_call.score_sector_weighted(pcr, SP_WEIGHTS)
    assert r.score >= 8
    assert r.confidence == pytest.approx(1.0, abs=1e-6)


def test_score_sector_weighted_put_dominated_bearish():
    """All sectors P/C > 1.3 → blended > 1.3 → score == 1 (bearish)."""
    pcr = {etf: 1.40 for etf in SP_WEIGHTS}
    r = put_call.score_sector_weighted(pcr, SP_WEIGHTS)
    assert r.score == 1


def test_score_sector_weighted_balanced():
    """All sectors P/C ≈ 0.95 → score in 5..7 range (balanced)."""
    pcr = {etf: 0.95 for etf in SP_WEIGHTS}
    r = put_call.score_sector_weighted(pcr, SP_WEIGHTS)
    assert 4 <= r.score <= 7


def test_score_sector_weighted_partial_data_confidence():
    """4 of 11 sectors present → confidence ≈ sqrt(4/11)."""
    pcr = {"XLK": 0.8, "XLF": 1.0, "XLV": 0.9, "XLE": 1.1}
    r = put_call.score_sector_weighted(pcr, SP_WEIGHTS)
    assert r.score > 0
    assert r.confidence == pytest.approx((4 / 11) ** 0.5, abs=1e-6)


def test_score_sector_weighted_empty():
    """No sectors → score=0, confidence=0.0."""
    r = put_call.score_sector_weighted({}, SP_WEIGHTS)
    assert r.score == 0
    assert r.confidence == 0.0


def test_score_sector_weighted_none_input():
    r = put_call.score_sector_weighted(None, SP_WEIGHTS)
    assert r.score == 0
    assert r.confidence == 0.0


def test_score_sector_weighted_all_invalid_values():
    """All P/C values <= 0 → treated as missing."""
    pcr = {etf: 0.0 for etf in SP_WEIGHTS}
    r = put_call.score_sector_weighted(pcr, SP_WEIGHTS)
    assert r.score == 0
    assert r.confidence == 0.0


def test_score_sector_weighted_cap_weighting():
    """XLK (32.53%) skews call-heavy while small sectors skew put-heavy.
    Result should track the dominant XLK reading, not a simple avg."""
    pcr = {etf: 1.40 for etf in SP_WEIGHTS}
    pcr["XLK"] = 0.50    # dominant tech sector very call-heavy
    r = put_call.score_sector_weighted(pcr, SP_WEIGHTS)
    # Cap-weighted blend: 0.50*32.53 + 1.40*(100-32.53) ≈ 1.107 / 1.00
    # Simple-avg blend would be ≈ 1.32 → score=1; cap-weighted gives
    # blended ≈ 1.11 → falls in the 1.1–1.3 band → score=2 (or just
    # above). Either way, materially better than the simple-avg case.
    assert r.score >= 2
