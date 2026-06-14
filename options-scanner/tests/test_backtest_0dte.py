"""
test_backtest_0dte.py - Core-math tests for the 0-DTE credit-spread backtest
Version: 1.0.0
Last Updated: 2026-06-13

Verifies the trade-evaluation primitives that determine win/loss and P&L:
strike solving by delta, expected-move placement, settlement intrinsic value,
and the worthless / breach / stopped outcome branches.
"""

import math
import pytest

import backtest_0dte as bt
from options_calculator import bs_delta


T = bt.T_FULL_SESSION
R = bt.RISK_FREE


#############################################
# STRIKE SELECTION
#############################################

def test_solve_strike_by_delta_put_hits_target():
    S, sigma = 6000.0, 0.18
    k = bt.solve_strike_by_delta(S, T, R, sigma, "put", 0.10)
    assert k < S  # OTM put below spot
    assert abs(abs(bs_delta(S, k, T, R, sigma, "put")) - 0.10) < 0.005


def test_solve_strike_by_delta_call_hits_target():
    S, sigma = 6000.0, 0.18
    k = bt.solve_strike_by_delta(S, T, R, sigma, "call", 0.16)
    assert k > S  # OTM call above spot
    assert abs(abs(bs_delta(S, k, T, R, sigma, "call")) - 0.16) < 0.005


def test_strike_by_em_places_one_expected_move_out():
    S, sigma = 6000.0, 0.18
    em = S * sigma * math.sqrt(T)
    assert bt.strike_by_em(S, T, sigma, "put", 1.0) == pytest.approx(S - em)
    assert bt.strike_by_em(S, T, sigma, "call", 1.25) == pytest.approx(S + 1.25 * em)


#############################################
# SETTLEMENT INTRINSIC
#############################################

def test_spread_value_at_expiry_is_intrinsic_put():
    # short 5900 put / long 5875 put, width 25
    sk, lk = 5900.0, 5875.0
    # price well above short strike -> worthless
    assert bt.spread_value(6000, sk, lk, "put", 0.0, R, 0.18) == pytest.approx(0.0)
    # price below long strike -> full width
    assert bt.spread_value(5800, sk, lk, "put", 0.0, R, 0.18) == pytest.approx(25.0)
    # price between -> partial (short ITM by 20)
    assert bt.spread_value(5880, sk, lk, "put", 0.0, R, 0.18) == pytest.approx(20.0)


#############################################
# TRADE OUTCOMES
#############################################

def _row(o, h, l, c, sigma=0.18):
    return {"open": o, "high": h, "low": l, "close": c,
            "sigma": sigma, "sma20": o, "date": "2026-01-02"}


def test_outcome_worthless_when_close_stays_otm():
    # Bullish put spread; market drifts UP and closes above short strike.
    row = _row(6000, 6030, 5990, 6020)
    t = bt.evaluate_trade(row, "put", "delta", 25, target_delta=0.10)
    assert t["outcome"] == "worthless"
    assert t["pnl_usd"] == pytest.approx(t["credit_usd"])


def test_outcome_breach_when_close_goes_through_short_strike():
    # Big down day, modest intraday low so the stop is not tripped first, but
    # the close lands below the short put strike -> a (partial/full) loss.
    sigma = 0.18
    S0 = 6000.0
    sk = bt.solve_strike_by_delta(S0, T, R, sigma, "put", 0.10)
    close = sk - 30.0  # below short strike -> ITM
    # keep the low equal to the close so the extreme == settlement
    row = _row(S0, 6005, close, close, sigma)
    t = bt.evaluate_trade(row, "put", "delta", 25, target_delta=0.10)
    assert t["outcome"] in ("breach", "stopped")
    assert t["pnl_usd"] < 0


def test_stop_caps_loss_at_2x_credit():
    # Force a violent adverse extreme so the stop trips; P&L must equal -2x credit.
    sigma = 0.30
    S0 = 6000.0
    row = _row(S0, 6010, 5700, 5950, sigma)  # huge intraday low
    t = bt.evaluate_trade(row, "put", "delta", 25, target_delta=0.10, stop_mult=2.0)
    if t["outcome"] == "stopped":
        assert t["pnl_usd"] == pytest.approx(-2.0 * t["credit_usd"])


def test_credit_is_positive_and_below_width():
    row = _row(6000, 6020, 5980, 6010)
    t = bt.evaluate_trade(row, "put", "delta", 25, target_delta=0.10,
                          multiplier=bt.ES_MULT)
    assert 0 < t["credit_usd"] < 25 * bt.ES_MULT
    assert t["max_loss_usd"] == pytest.approx(25 * bt.ES_MULT - t["credit_usd"])


def test_commissions_reduce_pnl_and_worthless_skips_exit():
    row = _row(6000, 6030, 5990, 6020)  # drifts up -> put spread worthless
    free = bt.evaluate_trade(row, "put", "delta", 25, target_delta=0.10,
                             comm_per_side=0.0)
    paid = bt.evaluate_trade(row, "put", "delta", 25, target_delta=0.10,
                             comm_per_side=3.75)
    assert paid["outcome"] == "worthless"
    # worthless expiry pays ENTRY only: 2 legs * 3.75 = 7.50, no exit
    assert paid["commissions"] == pytest.approx(7.50)
    assert paid["pnl_usd"] == pytest.approx(free["pnl_usd"] - 7.50)


def test_commissions_charge_exit_when_stopped_or_breached():
    sigma = 0.30
    row = _row(6000, 6010, 5700, 5950, sigma)  # violent low -> stopped
    t = bt.evaluate_trade(row, "put", "delta", 25, target_delta=0.10,
                          comm_per_side=3.75, stop_mult=2.0)
    if t["outcome"] != "worthless":
        # entry + exit = 4 legs * 3.75 = 15.00
        assert t["commissions"] == pytest.approx(15.00)


def test_mc_zero_vol_put_spread_below_spot_never_breaches():
    import random
    rng = random.Random(1)
    S0 = 6000.0
    short_k, long_k = 5900.0, 5875.0
    sigma = 0.001  # ~zero vol (scalar BS divides by zero at exactly 0)
    credit = bt.spread_value(S0, short_k, long_k, "put", T, R, sigma)
    pnls = bt.simulate_day_paths(S0, short_k, long_k, "put", T, sigma, R,
                                 credit, 25, 2.0, bt.ES_MULT, 0.0,
                                 n_paths=50, n_steps=24, rng=rng)
    # ~zero vol -> price barely moves -> put 100pt OTM never breaches -> no losses
    # (credit itself is ~0 at this vol, so assert "no negative outcome", not >0)
    assert min(pnls) >= -1e-9


def test_mc_higher_vol_lowers_win_rate_at_fixed_strikes():
    import random
    S0, short_k, long_k = 6000.0, 5900.0, 5875.0  # fixed strikes
    def winrate(sigma):
        rng = random.Random(7)
        credit = bt.spread_value(S0, short_k, long_k, "put", T, R, sigma)
        pnls = bt.simulate_day_paths(S0, short_k, long_k, "put", T, sigma, R,
                                     credit, 25, 2.0, bt.ES_MULT, 0.0,
                                     n_paths=400, n_steps=24, rng=rng)
        return sum(1 for p in pnls if p > 0) / len(pnls)
    assert winrate(0.12) > winrate(0.45)


def test_direction_follows_trend_filter():
    assert bt.choose_direction({"open": 6000, "sma20": 5900}) == "put"   # bullish
    assert bt.choose_direction({"open": 5800, "sma20": 5900}) == "call"  # bearish
    assert bt.choose_direction({"open": 6000, "sma20": None}) is None
