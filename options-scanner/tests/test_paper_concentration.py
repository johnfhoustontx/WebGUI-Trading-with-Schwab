"""Concentration caps for the auto paper engine.

These exist because on 2026-09-08 the paper book held FOURTEEN open positions
and every one of them was ORCL: $2,829 of max loss (11.6% of a $24,490 account)
in one name, one direction and one expiry, over an earnings report scheduled the
day before that expiry. The engine had no per-name limit of any kind -- only
``MAX_RISK_PER_TRADE`` (per trade) and ``MAX_SESSION_DRAWDOWN`` (whole account),
with nothing in between. Fourteen tickets read as a diversified book and were
one bet sliced fourteen ways.

The correlated-loss failure mode had already fired once: on 2026-09-01 ORCL fell
149.12 -> 141.32 in a single session and eight ORCL spreads hit DELTA_STOP /
MONEY_STOP the same day. The stops worked correctly -- that is the point.
"""
import math

import pytest

import paper_concentration as pc


def _pos(symbol, expiration="2026-09-11", max_loss_total=100.0):
    return {"symbol": symbol, "expiration": expiration,
            "max_loss_total": max_loss_total}


LIMITS = {"max_positions_per_symbol": 3,
          "max_risk_per_symbol": 750.0,
          "max_positions_per_expiry": 5}


def test_empty_book_admits_the_first_position():
    assert pc.concentration_reject([], "ORCL", "2026-09-11", 150.0,
                                   limits=LIMITS) is None


def test_a_book_under_every_cap_admits_the_candidate():
    book = [_pos("ORCL"), _pos("MSFT", "2026-09-18")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 150.0,
                                   limits=LIMITS) is None


def test_the_fourth_position_in_one_name_is_refused():
    book = [_pos("ORCL"), _pos("ORCL"), _pos("ORCL")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 100.0,
                                   limits=LIMITS) == pc.SYMBOL_POSITION_CAP


def test_positions_in_other_names_do_not_count_against_a_symbol():
    book = [_pos("MSFT"), _pos("AMD"), _pos("INTC")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 100.0,
                                   limits=LIMITS) is None


def test_risk_that_would_breach_the_per_symbol_budget_is_refused():
    book = [_pos("ORCL", max_loss_total=600.0)]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 200.0,
                                   limits=LIMITS) == pc.SYMBOL_RISK_CAP


def test_risk_landing_exactly_on_the_budget_is_admitted():
    """The cap is a ceiling, not a strict bound -- 750 of 750 is within it."""
    book = [_pos("ORCL", max_loss_total=600.0)]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 150.0,
                                   limits=LIMITS) is None


def test_one_expiry_is_capped_across_the_whole_book_not_per_name():
    """Five names expiring the same Friday is date concentration even though no
    single name is over its own cap."""
    book = [_pos("MSFT"), _pos("AMD"), _pos("INTC"), _pos("NVDA"), _pos("AVGO")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 100.0,
                                   limits=LIMITS) == pc.EXPIRY_POSITION_CAP


def test_a_different_expiry_is_not_blocked_by_a_full_one():
    book = [_pos("MSFT"), _pos("AMD"), _pos("INTC"), _pos("NVDA"), _pos("AVGO")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-18", 100.0,
                                   limits=LIMITS) is None


def test_a_non_finite_row_cannot_silently_switch_the_risk_cap_off():
    """``nan > 750`` is False, so a NaN in the sum disables the ceiling while
    looking like a working guard -- the repo's documented pins-the-bound trap.
    The bad row contributes nothing; the finite ones still bind."""
    book = [_pos("ORCL", max_loss_total=600.0),
            _pos("ORCL", max_loss_total=float("nan"))]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 200.0,
                                   limits=LIMITS) == pc.SYMBOL_RISK_CAP


def test_a_missing_risk_field_does_not_crash_the_gate():
    book = [{"symbol": "ORCL", "expiration": "2026-09-11"}]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 100.0,
                                   limits=LIMITS) is None


def test_symbols_are_compared_case_and_whitespace_insensitively():
    book = [_pos(" orcl "), _pos("ORCL"), _pos("Orcl")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 100.0,
                                   limits=LIMITS) == pc.SYMBOL_POSITION_CAP


def test_the_position_cap_is_reported_before_the_risk_cap():
    """Both are breached; the count is the more legible reason to log."""
    book = [_pos("ORCL", max_loss_total=300.0)] * 3
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 400.0,
                                   limits=LIMITS) == pc.SYMBOL_POSITION_CAP


def test_the_defaults_come_from_config_paper():
    """No ``limits`` argument means the shipped policy, so a config edit moves
    the engine without a code change."""
    import config_paper
    book = [_pos("ORCL")] * config_paper.MAX_POSITIONS_PER_SYMBOL
    assert pc.concentration_reject(book, "ORCL", "2026-09-11",
                                   1.0) == pc.SYMBOL_POSITION_CAP


def test_the_september_orcl_book_would_have_been_refused_at_the_fourth_ticket():
    """The regression this module exists for, in its own numbers."""
    book = []
    admitted = 0
    for _ in range(14):
        if pc.concentration_reject(book, "ORCL", "2026-09-11", 200.0,
                                   limits=LIMITS) is not None:
            break
        book.append(_pos("ORCL", max_loss_total=200.0))
        admitted += 1
    assert admitted == 3
