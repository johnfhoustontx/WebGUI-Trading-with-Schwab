"""Tests for iv_analysis expected-move helpers.

Focus: the per-EXPIRATION expected move (2026-08-07). The long-standing
``extract_atm_iv`` deliberately reads the ~30-DTE expiration, so every expected
move derived from it is a 30-day IV applied to whatever horizon the caller
scales to. These cover the per-expiry replacement.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import iv_analysis

#############################################
# Per-expiration expected move (2026-08-07)
#############################################

def _two_expiry_chain(front_iv=30.0, back_iv=15.0, underlying=100.0):
    """A chain whose FRONT expiry is priced far richer than the 30-day expiry —
    the term-structure inversion that `extract_atm_iv` (which reads ~30 DTE)
    cannot see."""
    def leg(iv):
        return [{"volatility": iv, "delta": -0.30, "mark": 1.0}]
    return {
        "underlyingPrice": underlying,
        "callExpDateMap": {
            "2026-08-10:3":  {"99.0": leg(front_iv), "100.0": leg(front_iv), "101.0": leg(front_iv)},
            "2026-09-05:30": {"99.0": leg(back_iv),  "100.0": leg(back_iv),  "101.0": leg(back_iv)},
        },
        "putExpDateMap": {
            "2026-08-10:3":  {"99.0": leg(front_iv), "100.0": leg(front_iv), "101.0": leg(front_iv)},
            "2026-09-05:30": {"99.0": leg(back_iv),  "100.0": leg(back_iv),  "101.0": leg(back_iv)},
        },
    }


def test_expiry_atm_iv_reads_that_expirations_own_iv():
    chain = _two_expiry_chain(front_iv=30.0, back_iv=15.0)
    assert iv_analysis.expiry_atm_iv(chain, "2026-08-10", 100.0) == 30.0
    assert iv_analysis.expiry_atm_iv(chain, "2026-09-05", 100.0) == 15.0
    # Non-vacuity: the symbol-level reader really does return the ~30d value, so
    # the front expiry's 30.0 is genuinely invisible to the current path.
    assert iv_analysis.extract_atm_iv(chain) == 15.0


def test_expiry_atm_iv_averages_call_and_put():
    chain = _two_expiry_chain()
    chain["callExpDateMap"]["2026-08-10:3"]["100.0"][0]["volatility"] = 20.0
    chain["putExpDateMap"]["2026-08-10:3"]["100.0"][0]["volatility"] = 40.0
    assert iv_analysis.expiry_atm_iv(chain, "2026-08-10", 100.0) == 30.0


def test_expiry_atm_iv_none_when_unavailable():
    chain = _two_expiry_chain()
    assert iv_analysis.expiry_atm_iv(chain, "2099-01-01", 100.0) is None
    assert iv_analysis.expiry_atm_iv(None, "2026-08-10", 100.0) is None
    assert iv_analysis.expiry_atm_iv(chain, "2026-08-10", 0) is None


def test_expiry_daily_em_is_a_one_day_move_at_that_expirys_iv():
    # 100 * 0.30 * sqrt(1/365), rounded to cents by calc_expected_move.
    assert iv_analysis.expiry_daily_em(100.0, 30.0) == pytest.approx(
        round(100 * 0.30 * math.sqrt(1 / 365.0), 2), abs=1e-9)
    assert iv_analysis.expiry_daily_em(100.0, 0) is None
    assert iv_analysis.expiry_daily_em(0, 30.0) is None


def test_expiry_daily_em_tracks_the_expirys_iv_not_a_fixed_one():
    """A richer front expiry must produce a bigger daily EM — the whole point."""
    lo = iv_analysis.expiry_daily_em(100.0, 15.0)
    hi = iv_analysis.expiry_daily_em(100.0, 30.0)
    # rel tolerance covers calc_expected_move's rounding to cents.
    assert hi > lo and hi == pytest.approx(2 * lo, rel=0.02)
