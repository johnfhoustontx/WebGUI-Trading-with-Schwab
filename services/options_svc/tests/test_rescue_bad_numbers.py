"""A NaN, an infinity or a bool is no reading: Rescue's risk read must treat it
exactly as it treats a missing value.

Measured 2026-11-28 before the fix: ``rescue._num`` was a bare ``float()``, so a
NaN or infinite underlying or short strike read a range structure as CRITICAL
(heat 80-95, where a missing one read watch/tested at 45-62.5), an infinite
entry credit read a debit position as OK at heat 0, and ``True`` read as a
price of 1.0. ``None`` already had a branch everywhere; these now take it.
"""
import pytest

from services.options_svc import rescue

BAD = [float("nan"), float("inf"), float("-inf"), True, "nan", "inf"]
BAD_IDS = ["nan", "inf", "-inf", "True", "'nan'", "'inf'"]

MARK = {"current_underlying": 505.0, "unrealized_pnl": -150.0, "dte": 3,
        "current_short_delta": -0.35}
NAKED_PUT = {"strategy": "NAKED_PUT", "short_strike": 500.0, "quantity": 1,
             "entry_credit": 2.0}
LONG_CALL = {"strategy": "LONG_CALL", "short_strike": 510.0, "quantity": 1,
             "entry_credit": -3.0}
DEBIT = {"strategy": "VERT_CALL_DEBIT", "long_strike": 500.0, "short_strike": 510.0,
         "quantity": 1, "entry_credit": -4.0}
LEGS = [{"side": "long", "strike": 490.0}, {"side": "short", "strike": 500.0},
        {"side": "short", "strike": 510.0}, {"side": "long", "strike": 520.0}]
RANGE = {"strategy": "LONG_IRON_CONDOR", "legs": LEGS, "quantity": 1,
         "entry_credit": -2.0}


def _with(base, key, value):
    return {**base, key: value}


def _leg(i, value):
    return [({**leg, "strike": value} if n == i else leg) for n, leg in enumerate(LEGS)]


CASES = {
    "single naked put: short_strike":
        lambda v: rescue.assess_single_risk(_with(NAKED_PUT, "short_strike", v), MARK),
    "single naked put: entry_credit":
        lambda v: rescue.assess_single_risk(_with(NAKED_PUT, "entry_credit", v), MARK),
    "single naked put: underlying":
        lambda v: rescue.assess_single_risk(NAKED_PUT, _with(MARK, "current_underlying", v)),
    "single naked put: short delta":
        lambda v: rescue.assess_single_risk(NAKED_PUT, _with(MARK, "current_short_delta", v)),
    "single long call: strike":
        lambda v: rescue.assess_single_risk(_with(LONG_CALL, "short_strike", v), MARK),
    "single long call: entry_credit":
        lambda v: rescue.assess_single_risk(_with(LONG_CALL, "entry_credit", v), MARK),
    "single long call: underlying":
        lambda v: rescue.assess_single_risk(LONG_CALL, _with(MARK, "current_underlying", v)),
    "debit: long_strike":
        lambda v: rescue.assess_debit_risk(_with(DEBIT, "long_strike", v), MARK),
    "debit: entry_credit":
        lambda v: rescue.assess_debit_risk(_with(DEBIT, "entry_credit", v), MARK),
    "debit: underlying":
        lambda v: rescue.assess_debit_risk(DEBIT, _with(MARK, "current_underlying", v)),
    "range: wing strike":
        lambda v: rescue.assess_range_risk(_with(RANGE, "legs", _leg(0, v)), MARK),
    "range: short strike":
        lambda v: rescue.assess_range_risk(_with(RANGE, "legs", _leg(1, v)), MARK),
    "range: underlying":
        lambda v: rescue.assess_range_risk(RANGE, _with(MARK, "current_underlying", v)),
    "range: entry_credit":
        lambda v: rescue.assess_range_risk(_with(RANGE, "entry_credit", v), MARK),
}


@pytest.mark.parametrize("bad", BAD, ids=BAD_IDS)
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_bad_number_reads_as_a_missing_one(case, bad):
    build = CASES[case]
    assert build(bad) == build(None)


def test_the_cases_are_not_vacuous():
    # A real reading must move the answer, or the comparison above proves nothing.
    moved = [c for c, build in CASES.items() if build(None) != build(
        {"single naked put: short delta": -0.6}.get(c, 1e-3 if "underlying" in c else 1.0))]
    assert len(moved) >= len(CASES) // 2, moved


@pytest.mark.parametrize("value, want", [
    (500.0, 500.0), (" 2.5 ", 2.5), ("495", 495.0), (0, 0.0), (None, None),
    ("x", None), (float("nan"), None), (True, None),
])
def test_num_still_reads_numbers_and_numeric_text(value, want):
    assert rescue._num(value) == want
