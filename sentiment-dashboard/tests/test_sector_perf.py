"""Tests for scoring.sector_perf."""
import pytest

from scoring import sector_perf


def _row(name, etf, weight):
    return {'kind': 'sector', 'sector': name, 'etf': etf, 'sp_weight': weight}


SECTORS = [
    _row("Information Technology", "XLK", 32.53),
    _row("Financials",             "XLF", 13.42),
    _row("Communication Services", "XLC", 10.16),
    _row("Consumer Discretionary", "XLY",  9.94),
    _row("Industrials",            "XLI",  8.86),
    _row("Health Care",            "XLV",  8.63),
    _row("Energy",                 "XLE",  4.89),
    _row("Consumer Staples",       "XLP",  4.61),
    _row("Materials",              "XLB",  2.74),
    _row("Real Estate",            "XLRE", 2.12),
    _row("Utilities",              "XLU",  2.09),
]


def test_weighted_sector_pct_no_data():
    wpct, total_w = sector_perf.weighted_sector_pct([], {})
    assert wpct is None
    assert total_w == 0


def test_weighted_sector_pct_flat_zero():
    quotes = {row['etf']: {'change_pct': 0.0} for row in SECTORS}
    wpct, total_w = sector_perf.weighted_sector_pct(SECTORS, quotes)
    assert wpct == 0.0
    assert total_w > 0


def test_sectors_score_missing_returns_zero():
    assert sector_perf.sectors_score([], {}) == 0.0


def test_sectors_score_neutral_day():
    """All sectors at exactly 0% means 0/N > 0 (pct_up = 0.0 ≤ 0.20) so
    the breadth-penalty branch fires and trims the base 5.0 down to 4.0
    — preserved verbatim from the legacy ``_sectors_score`` behavior."""
    quotes = {row['etf']: {'change_pct': 0.0} for row in SECTORS}
    s = sector_perf.sectors_score(SECTORS, quotes)
    assert s == 4.0


def test_sectors_score_strong_up_day_with_breadth_bump():
    quotes = {row['etf']: {'change_pct': 1.0} for row in SECTORS}
    s = sector_perf.sectors_score(SECTORS, quotes)
    # 5 + 1.0*2.5 + 1 (>=80% green) = 8.5
    assert s == 8.5


def test_sectors_score_strong_down_day_with_breadth_penalty():
    quotes = {row['etf']: {'change_pct': -1.0} for row in SECTORS}
    s = sector_perf.sectors_score(SECTORS, quotes)
    # 5 + -2.5 + -1 = 1.5
    assert s == 1.5


def test_sectors_score_clipped_to_10():
    quotes = {row['etf']: {'change_pct': 5.0} for row in SECTORS}
    assert sector_perf.sectors_score(SECTORS, quotes) == 10.0
