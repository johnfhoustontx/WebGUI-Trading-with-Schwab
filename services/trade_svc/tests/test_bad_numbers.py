"""An infinity, NaN, a bool or an int too large for a float is no reading.

Measured 2026-11-28 before the fix (group 3 of the numeric-guard audit): the
four private ``_num`` copies refused NaN and bools but passed an infinity, and
raised on ``10**400``. An infinite price opened a model position at
``entry: inf`` and CLOSED an open one as a target (``-inf``: a stop). An
infinite horizon or earnings day count made ``trade_plan.build`` - "never
raises" - raise ``OverflowError``. Infinite walls gave the dealer context
``inf`` / ``nan`` wall percentages, and a reading stored as text made the live
IC monitor rank text against floats and degrade to "insufficient".
Each bad input must now read exactly as a missing one does.
"""
import datetime as dt

import pytest

from services.trade_svc import dealer_context, live_ic, model_book, trade_plan

BAD = [float("nan"), float("inf"), float("-inf"), "inf", True, 10 ** 400]
IDS = ["nan", "inf", "-inf", "'inf'", "True", "10**400"]

TODAY = dt.date(2026, 11, 27)
NOW = dt.datetime(2026, 11, 27, 15, 0, tzinfo=dt.timezone.utc)

ANALYSIS = {
    "symbol": "AAPL", "price": 200.0,
    "swing_model": {"verdict": "BUY", "side": "long", "horizon_days": 20,
                    "expected_fwd": 0.02, "band": "top"},
    "momentum": {"atr": 3.0}, "fundamentals": {"days_to_earnings": 12},
    "dealer_context": {"collected": True, "flip": 198.0, "call_wall": 210.0,
                       "put_wall": 190.0},
}


def _plan(**over):
    a = {**ANALYSIS}
    for k, v in over.items():
        section, _, key = k.partition("__")
        if key:
            a[section] = {**a[section], key: v}
        else:
            a[section] = v
    return trade_plan.build(a, TODAY)


BOARD = {"status": "ok", "horizon_days": 20,
         "rows": [{"symbol": "AAPL", "pool": "long", "composite": 1.2}]}
POS = {"symbol": "AAPL", "side": "long", "entry": 200.0, "spy_entry": 600.0,
       "expression": "directional", "stop": 190.0, "target": 220.0,
       "time_stop_on": "2026-12-30"}

ROWS = [{"reading_date": f"2026-10-{d:02d}", "composite": 0.1 * d,
         live_ic.HORIZON_KEY: 0.01 * ((d * 7) % 5 - 2), live_ic.MARKET_KEY: 0.01,
         "swing_verdict": "BUY"} for d in range(1, 40)]

MATRIX_ROW = {"spot": 500.0, "flip": 498.0, "net_gex": 1e9, "atm_iv": 0.2,
              "call_wall": 510.0, "put_wall": 490.0}

CASES = {
    "plan price": lambda v: _plan(price=v),
    "plan horizon_days": lambda v: _plan(swing_model__horizon_days=v),
    "plan expected_fwd": lambda v: _plan(swing_model__expected_fwd=v),
    "plan atr": lambda v: _plan(momentum__atr=v),
    "plan days_to_earnings": lambda v: _plan(fundamentals__days_to_earnings=v),
    "plan flip": lambda v: _plan(dealer_context__flip=v),
    "book candidate price": lambda v: model_book.candidates(
        BOARD, {"AAPL": v, "SPY": 600.0}, TODAY),
    "book candidate composite": lambda v: model_book.candidates(
        {**BOARD, "rows": [{**BOARD["rows"][0], "composite": v}]},
        {"AAPL": 200.0, "SPY": 600.0}, TODAY),
    "book mark price": lambda v: model_book.mark(POS, v, 600.0),
    "book close_reason price": lambda v: model_book.close_reason(POS, v, 600.0, TODAY),
    "book summary pnl": lambda v: model_book.summary(
        [{"status": "closed", "side": "long", "pnl_pct": 0.02},
         {"status": "closed", "side": "long", "pnl_pct": v}]),
    "ic composite": lambda v: live_ic.compute(ROWS + [{**ROWS[0], "composite": v}]),
    "ic forward return": lambda v: live_ic.compute(
        ROWS + [{**ROWS[0], live_ic.HORIZON_KEY: v}]),
    "ic history forward return": lambda v: live_ic.symbol_history(
        [{**ROWS[0], live_ic.HORIZON_KEY: v}]),
}
for _key in MATRIX_ROW:
    CASES[f"dealer {_key}"] = (lambda k: lambda v: dealer_context.build(
        {**MATRIX_ROW, k: v}, NOW.isoformat(), NOW))(_key)


@pytest.mark.parametrize("bad", BAD, ids=IDS)
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_bad_number_reads_as_a_missing_one(case, bad):
    build = CASES[case]
    assert repr(build(bad)) == repr(build(None))


# A real value that differs from the fixture's, so each case is shown to READ
# its input (the close_reason case needs a price past the target to move).
REAL = {"book close_reason price": 230.0, "plan horizon_days": 10,
        "plan days_to_earnings": 40}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_cases_are_not_vacuous(case):
    build = CASES[case]
    assert repr(build(REAL.get(case, 0.37))) != repr(build(None))


def test_a_reading_stored_as_text_is_ranked_as_its_number():
    as_text = [{**r, "composite": str(r["composite"])} for r in ROWS]
    got, want = live_ic.compute(as_text), live_ic.compute(ROWS)
    assert got["status"] == "ok"
    assert got["pooled_ic"] == pytest.approx(want["pooled_ic"])
