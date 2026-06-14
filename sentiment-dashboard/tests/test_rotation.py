"""Tests for scoring.rotation."""
import pytest

from scoring import rotation


def _sector_row(name, etf):
    return {'kind': 'sector', 'sector': name, 'etf': etf}


CYC = [
    _sector_row("Information Technology", "XLK"),
    _sector_row("Financials", "XLF"),
    _sector_row("Consumer Discretionary", "XLY"),
    _sector_row("Industrials", "XLI"),
    _sector_row("Communication Services", "XLC"),
    _sector_row("Materials", "XLB"),
    _sector_row("Energy", "XLE"),
]
DEF = [
    _sector_row("Consumer Staples", "XLP"),
    _sector_row("Utilities", "XLU"),
    _sector_row("Health Care", "XLV"),
    _sector_row("Real Estate", "XLRE"),
]


def test_compute_rotation_no_data_returns_none():
    assert rotation.compute_rotation([], {}, {}) is None


def test_compute_rotation_only_day_timeframe_risk_on():
    """Cyc +1%, Def -1% → spread = +2 → day_score = 10. Single timeframe →
    confidence = sqrt(1/3)."""
    sector_data = CYC + DEF
    trends = {}  # no 3d / week
    quotes = {**{row['etf']: {'change_pct': 1.0} for row in CYC},
              **{row['etf']: {'change_pct': -1.0} for row in DEF}}
    r = rotation.compute_rotation(sector_data, trends, quotes)
    assert r is not None
    assert r['score'] == 10
    assert r['confidence'] == pytest.approx((1.0/3.0) ** 0.5)
    assert 'day' in r['timeframes_present']


def test_compute_rotation_risk_off():
    sector_data = CYC + DEF
    quotes = {**{row['etf']: {'change_pct': -1.0} for row in CYC},
              **{row['etf']: {'change_pct': +1.0} for row in DEF}}
    r = rotation.compute_rotation(sector_data, {}, quotes)
    # spread = -2 → day_score = 1
    assert r['score'] == 1


def test_compute_rotation_all_three_timeframes_full_confidence():
    sector_data = CYC + DEF
    trends = {**{row['etf']: {'day3_pct': 0.5, 'week_pct': 0.5} for row in CYC},
              **{row['etf']: {'day3_pct': -0.5, 'week_pct': -0.5} for row in DEF}}
    quotes = {**{row['etf']: {'change_pct': 0.5} for row in CYC},
              **{row['etf']: {'change_pct': -0.5} for row in DEF}}
    r = rotation.compute_rotation(sector_data, trends, quotes)
    assert r['confidence'] == 1.0
    # spread = +1 in all three timeframes → each sub_score = 7.5 → blended = 7.5
    assert r['score'] == 8


def test_spread_to_score_none():
    assert rotation._spread_to_score(None) is None


def test_spread_to_score_linear():
    assert rotation._spread_to_score(0) == 5.0
    assert rotation._spread_to_score(2) == 10.0
    assert rotation._spread_to_score(-2) == 1.0


def test_score_fallback_categorical():
    r = rotation.score_fallback(xly_xlp="Risk-On",
                                smh_spy="Leading",
                                iwm_spy="Leading",
                                qqq_spy="Lagging")
    # 5 + 2 + 1 + 1 - 1 = 8
    assert r.score == 8


# ── v4.4 Dual Momentum + RRG ──────────────────────────────────────

import pytest
from scoring import rotation

SP_WEIGHTS_E = {
    "XLK": 32.53, "XLF": 13.42, "XLC": 10.16, "XLY":  9.94,
    "XLI":  8.86, "XLV":  8.63, "XLE":  4.89, "XLP":  4.61,
    "XLB":  2.74, "XLRE": 2.12, "XLU":  2.09,
}


def _series(ret_pct, n=70):
    """Build a 70-bar series ending at 100 with the given total return."""
    start = 100.0 / (1.0 + ret_pct / 100.0)
    step = (100.0 - start) / (n - 1)
    return [start + step * i for i in range(n)]


def test_dual_momentum_cyclical_leading():
    """Top 7 returns to cyclicals → score ≥ 8."""
    hist = {
        "XLK": _series(15), "XLF": _series(12), "XLY": _series(10),
        "XLI": _series(9), "XLC": _series(8), "XLB": _series(6),
        "XLE": _series(4), "XLV": _series(2), "XLP": _series(1),
        "XLU": _series(0), "XLRE": _series(-1),
    }
    r = rotation.compute_dual_momentum(hist, SP_WEIGHTS_E, irx_yield_pct=4.0)
    assert r['score'] >= 8
    assert not r['crash_active']


def test_dual_momentum_defensive_leading():
    """Top 4 returns to defensives → score ≤ 3."""
    hist = {
        "XLV": _series(15), "XLP": _series(12), "XLU": _series(10),
        "XLRE": _series(9), "XLK": _series(3), "XLF": _series(2),
        "XLY": _series(1), "XLI": _series(0), "XLC": _series(-1),
        "XLB": _series(-2), "XLE": _series(-3),
    }
    r = rotation.compute_dual_momentum(hist, SP_WEIGHTS_E, irx_yield_pct=4.0)
    assert r['score'] <= 3
    assert not r['crash_active']


def test_dual_momentum_balanced():
    """Cyclicals at ranks {2,4,5,6,7,8,10} (mean 6); defensives at
    {1,3,9,11} (mean 6) → spread 0 → score == 5."""
    hist = {
        "XLV": _series(10),   # def rank 1
        "XLK": _series(9),    # cyc rank 2
        "XLP": _series(8),    # def rank 3
        "XLF": _series(7),    # cyc rank 4
        "XLY": _series(6),    # cyc rank 5
        "XLI": _series(5),    # cyc rank 6
        "XLC": _series(4),    # cyc rank 7
        "XLB": _series(3),    # cyc rank 8
        "XLU": _series(2),    # def rank 9
        "XLE": _series(1),    # cyc rank 10
        "XLRE": _series(0),   # def rank 11
    }
    r = rotation.compute_dual_momentum(hist, SP_WEIGHTS_E, irx_yield_pct=4.0)
    assert r['score'] == 5


def test_dual_momentum_crash_filter_triggers():
    """All sectors below cash equivalent → score == 1."""
    hist = {etf: _series(-5) for etf in SP_WEIGHTS_E}
    # IRX 4% annualized ≈ 0.99% over 63 days; top return is -5% << 0.99% → crash.
    r = rotation.compute_dual_momentum(hist, SP_WEIGHTS_E, irx_yield_pct=4.0)
    assert r['crash_active']
    assert r['score'] == 1


def test_dual_momentum_no_irx_skips_crash_filter():
    """irx_yield_pct=None → crash filter never fires, score from spread."""
    hist = {etf: _series(-5) for etf in SP_WEIGHTS_E}
    r = rotation.compute_dual_momentum(hist, SP_WEIGHTS_E, irx_yield_pct=None)
    assert not r['crash_active']
    assert r['score'] != 1 or r['score'] == 5  # symmetric returns → neutral
    # Confidence halved when IRX missing.
    assert r['confidence'] == pytest.approx((1.0 * 0.5) ** 0.5, abs=1e-6)


def test_dual_momentum_partial_data_confidence():
    """6 of 11 sectors with data → confidence reflects partial coverage."""
    hist = {etf: _series(5) for etf in list(SP_WEIGHTS_E)[:6]}
    r = rotation.compute_dual_momentum(hist, SP_WEIGHTS_E, irx_yield_pct=4.0)
    assert r['confidence'] == pytest.approx((6 / 11.0) ** 0.5, abs=1e-6)


def test_dual_momentum_empty_returns():
    """No history → score=0, confidence=0."""
    r = rotation.compute_dual_momentum({}, SP_WEIGHTS_E, irx_yield_pct=4.0)
    assert r['score'] == 0
    assert r['confidence'] == 0.0


def test_rrg_quadrants_all_four():
    """Synthetic series placing four ETFs in distinct quadrants vs SPY."""
    spy = [100.0 + 0.1 * i for i in range(80)]  # slow steady benchmark

    def etf_from_rs(rs_series):
        return [rs * spy_v for rs, spy_v in zip(rs_series, spy)]

    # LEADING: RS rises and accelerates over the last 20 bars.
    rs_leading = [1.0] * 60 + [1.0 + 0.005 * i for i in range(20)]
    # WEAKENING: RS is high but flattening / fading recently.
    rs_weakening = [1.0 + 0.001 * i for i in range(60)] + [1.07 - 0.001 * i for i in range(20)]
    # LAGGING: RS falls steadily.
    rs_lagging = [1.0 - 0.001 * i for i in range(80)]
    # IMPROVING: RS in extended downtrend with a small late uptick.
    # rs_today must stay BELOW the 50-bar mean (rs_strength < 100)
    # while still beating RS from 20 bars ago (rs_momentum > 100).
    rs_improving = [1.0 - 0.001 * i for i in range(60)] + [
        0.94 + 0.0004 * i for i in range(20)]

    hist = {
        "XLK": etf_from_rs(rs_leading),
        "XLF": etf_from_rs(rs_weakening),
        "XLV": etf_from_rs(rs_lagging),
        "XLP": etf_from_rs(rs_improving),
    }
    q = rotation.compute_rrg_quadrants(hist, spy, rs_window=50, mom_window=20)
    assert q.get("XLK") == "Leading"
    assert q.get("XLF") == "Weakening"
    assert q.get("XLV") == "Lagging"
    assert q.get("XLP") == "Improving"


def test_rrg_quadrants_insufficient_history():
    """Series shorter than max(rs_window, mom_window) → ETF skipped."""
    spy = [100.0 + i for i in range(60)]
    hist = {"XLK": [100.0 + i for i in range(30)]}
    q = rotation.compute_rrg_quadrants(hist, spy, rs_window=50, mom_window=20)
    assert q == {}
