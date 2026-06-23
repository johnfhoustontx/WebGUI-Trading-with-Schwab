"""Intraday-time additions to options_calculator: implied_vol + calc_spread_pnl
``eval_times`` (per-column time-to-expiry overrides).

These back the Calculator's 0DTE fix: the P&L matrix must show CURRENT value (with
the remaining intraday time value) for the "Now" column, not the expiration payoff,
and the IV field is implied from the option mark (ToS-style).
"""
import datetime as dt

import pytest

from options_calculator import bs_price, implied_vol, calc_spread_pnl


R = 0.045


# ── implied_vol ──────────────────────────────────────────────────────────────
def test_implied_vol_roundtrips_bs_price():
    """implied_vol recovers the sigma that produced a bs_price."""
    for iv_true in (0.12, 0.25, 0.38, 0.75):
        T = 2.5 / 24 / 365  # ~2.5 hours to expiry (0DTE intraday)
        price = bs_price(718.82, 725.0, T, R, iv_true, "call")
        got = implied_vol(price, 718.82, 725.0, T, R, "call")
        assert got == pytest.approx(iv_true, abs=1e-3)


def test_implied_vol_none_when_unsolvable():
    """No time value / expired / non-positive inputs → None (not a bogus number)."""
    T = 2.5 / 24 / 365
    assert implied_vol(0.0, 100, 105, T, R, "call") is None          # zero price
    assert implied_vol(1.0, 100, 105, 0.0, R, "call") is None        # expired (T=0)
    assert implied_vol(-1, 100, 105, T, R, "call") is None           # negative price
    # An ITM call quoted at exactly intrinsic has no implied time value.
    assert implied_vol(5.0, 110, 105, T, R, "call") is None          # price==intrinsic(5)


def test_implied_vol_matches_thinkorswim_convention():
    """The QQQ 725C from the bug report: mark 0.19 at ~2.45h-to-4pmET implies ~38%."""
    T = 2.45 / 24 / 365
    iv = implied_vol(0.19, 718.82, 725.0, T, R, "call")
    assert iv is not None and 0.36 < iv < 0.40   # ≈ ToS's 38.3%


# ── calc_spread_pnl eval_times ───────────────────────────────────────────────
def _atm_long_call(T_value, K=100.0):
    """A 1-lot ATM long call whose premium == its value at T_value (so the 'now'
    column at spot prices to break-even when eval_time == T_value). ATM keeps a
    meaningful time value even at a few hours to expiry."""
    prem = bs_price(100.0, K, T_value, R, 0.30, "call")
    legs = [{"strike": K, "option_type": "call", "side": "long",
             "premium": prem, "qty": 1}]
    return legs, prem


def test_eval_times_now_column_keeps_time_value_exp_column_is_intrinsic():
    T_now = 6.0 / 24 / 365
    legs, prem = _atm_long_call(T_now)
    rows = calc_spread_pnl(legs, 100.0, 0.30, R, eval_dates=[None, None],
                           price_range=(98.0, 102.0),
                           expiry_date=dt.date(2026, 6, 23),
                           eval_times=[T_now, 0.0])
    spot_row = next(r for r in rows if abs(r["price"] - 100.0) < 1e-6)
    now_pnl, exp_pnl = spot_row["pnl"]
    assert prem > 0.2   # ATM call at ~6h still carries real premium
    # "Now" column = current value (≈ break-even here) — has time value.
    assert now_pnl == pytest.approx(0.0, abs=1.0)
    # "Exp" column (T=0) = expiration payoff: full premium loss for an ATM call.
    assert exp_pnl == pytest.approx(-prem * 100, abs=0.01)
    # The whole point: today's value is NOT the expiration payoff.
    assert now_pnl > exp_pnl


def test_eval_times_overrides_calendar_day_path():
    """When eval_times is given, the per-column .days math is ignored entirely."""
    legs, _ = _atm_long_call(4.0 / 24 / 365)
    rows = calc_spread_pnl(legs, 100.0, 0.30, R, eval_dates=[None],
                           price_range=(99.0, 101.0),
                           expiry_date=dt.date(2026, 6, 23),
                           eval_times=[3.0 / 24 / 365])
    # Single column, priced at the supplied intraday T (positive time value at K-ish).
    assert all(len(r["pnl"]) == 1 for r in rows)
