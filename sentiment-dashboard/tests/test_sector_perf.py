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


# ── absence is None, a crash is 1.0, a flat tape is 5.0 (audit AC-49) ──────
# The score is on the composite's 1..10 scale, where 0 means "no reading".
# It used to return 0.0 for no data AND for a real crash day (the clamp's
# floor), and the history backfill then deleted every day that scored 0.

def test_no_data_is_none_not_a_score():
    assert sector_perf.sectors_score([], {}) is None
    assert sector_perf.sectors_score(SECTORS, {}) is None


def test_a_crash_day_is_the_bottom_of_the_scale_not_absent():
    quotes = {row['etf']: {'change_pct': -4.0} for row in SECTORS}
    assert sector_perf.sectors_score(SECTORS, quotes) == 1.0


def test_no_real_day_scores_below_one():
    for pct in (-2.0, -2.5, -3.0, -10.0):
        quotes = {row['etf']: {'change_pct': pct} for row in SECTORS}
        assert sector_perf.sectors_score(SECTORS, quotes) >= 1.0


def test_sectors_score_neutral_day():
    """A flat tape is neutral. Every sector at exactly 0% is neither up nor
    down, so there is no breadth adjustment in either direction.

    This asserted 4.0 until 2026-10-04, "preserved verbatim from the legacy
    behavior": zero sectors were UP, which the penalty read as 80% DOWN."""
    quotes = {row['etf']: {'change_pct': 0.0} for row in SECTORS}
    s = sector_perf.sectors_score(SECTORS, quotes)
    assert s == 5.0


def test_the_breadth_penalty_needs_sectors_that_are_actually_down():
    etfs = [row['etf'] for row in SECTORS]
    mostly_flat = {e: {'change_pct': 0.0} for e in etfs}
    mostly_flat[etfs[0]] = {'change_pct': -0.11}        # one down, the rest flat
    assert sector_perf.sectors_score(SECTORS, mostly_flat) > 4.5


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
