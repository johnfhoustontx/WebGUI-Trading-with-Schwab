# options-scanner/tests/test_calc_multileg.py
from datetime import date

import options_calculator as oc


def test_per_leg_expiry_back_leg_retains_value_at_front_expiry():
    # Calendar: short near call, long far call, same strike. With per-leg expiry,
    # at the front-expiry column the back leg still has time value, so the net
    # P&L recovers well above the full net debit (-300).
    legs = [
        {"strike": 100, "option_type": "call", "side": "short", "premium": 2.0,
         "qty": 1, "expiry": "2026-07-17"},
        {"strike": 100, "option_type": "call", "side": "long", "premium": 5.0,
         "qty": 1, "expiry": "2026-08-21"},
    ]
    grid = oc.calc_spread_pnl(
        legs, spot=100, iv=0.30, r=0.04, eval_dates=None,
        price_range=(100, 100), expiry_date=date(2026, 7, 17),
        eval_times=[0.0], per_leg_expiry=True)
    row = grid[0]
    assert row["pnl"][0] > -300


def test_single_expiry_unchanged_when_per_leg_off():
    legs = [{"strike": 95, "option_type": "put", "side": "short", "premium": 1.5, "qty": 1},
            {"strike": 90, "option_type": "put", "side": "long", "premium": 0.5, "qty": 1}]
    a = oc.calc_spread_pnl(legs, 100, 0.2, 0.04, None, (90, 110), date(2026, 7, 17),
                           eval_times=[0.02, 0.0])
    b = oc.calc_spread_pnl(legs, 100, 0.2, 0.04, None, (90, 110), date(2026, 7, 17),
                           eval_times=[0.02, 0.0], per_leg_expiry=True)
    assert a == b   # legs without 'expiry' -> column T unchanged, identical output


def test_generic_summary_long_call_butterfly():
    # 95/100/105 call fly, 1-2-1. Defined-risk: max loss ~ net debit,
    # max profit at the body, two breakevens between the wings.
    legs = [
        {"strike": 95, "option_type": "call", "side": "long", "premium": 6.0, "qty": 1},
        {"strike": 100, "option_type": "call", "side": "short", "premium": 3.0, "qty": 2},
        {"strike": 105, "option_type": "call", "side": "long", "premium": 1.5, "qty": 1},
    ]
    s = oc.calc_summary_generic(legs, spot=100, r=0.04, iv=0.25, T=0.05)
    assert abs(s["max_loss"] - 150) < 25
    assert 300 < s["max_profit"] < 400
    assert len(s["breakevens"]) == 2
    assert 95 < s["breakevens"][0] < 100 < s["breakevens"][1] < 105
    assert 0 <= s["pop"] <= 100


def test_generic_summary_keys():
    legs = [{"strike": 100, "option_type": "call", "side": "long", "premium": 2, "qty": 1}]
    s = oc.calc_summary_generic(legs, 100, 0.04, 0.2, 0.05)
    assert set(s) >= {"entry_credit", "max_profit", "max_loss",
                      "breakevens", "return_on_risk", "pop"}


def test_calc_spread_pnl_uses_explicit_price_rows():
    # The Calculator's Number-of-strikes control hands the grid the ±N real chain
    # strikes; calc_spread_pnl must use them verbatim as the price rows.
    legs = [{"strike": 100, "option_type": "call", "side": "long", "premium": 2.0, "qty": 1}]
    rows = oc.calc_spread_pnl(legs, 100, 0.2, 0.045, [None], (0, 1e9),
                              date(2026, 7, 17), eval_times=[0.05],
                              price_rows=[90, 95, 100, 105, 110])
    assert [r["price"] for r in rows] == [90.0, 95.0, 100.0, 105.0, 110.0]


def test_calc_spread_pnl_price_rows_none_keeps_step_gen():
    # No price_rows → the legacy spot-magnitude step grid (unchanged fallback).
    legs = [{"strike": 100, "option_type": "call", "side": "long", "premium": 2.0, "qty": 1}]
    rows = oc.calc_spread_pnl(legs, 100, 0.2, 0.045, [None], (95, 105),
                              date(2026, 7, 17), eval_times=[0.05])
    assert len(rows) >= 3 and all("price" in r for r in rows)
