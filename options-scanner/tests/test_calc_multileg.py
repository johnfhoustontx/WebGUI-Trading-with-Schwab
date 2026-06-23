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
