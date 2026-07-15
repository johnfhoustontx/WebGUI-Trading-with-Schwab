"""Ledger paper-trade support for non-credit DEFINED-RISK structures (long options +
debit verticals) — create + expiration settlement. Credit spreads are unaffected.
"""
import datetime

import paper_trader

_EXP = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()


def _long_call(strike=100, mark=2.5, net_debit=250.0, max_loss=260.0):
    return {"symbol": "SPY", "type": "LONG_CALL", "expiration": _EXP, "dte": 30,
            "legs": [{"kind": "call", "side": "long", "strike": strike, "mark": mark,
                      "qty": 1, "delta": 0.5}],
            "net_debit": net_debit, "net_credit": None, "max_loss": max_loss,
            "max_profit": None, "unbounded": True, "breakevens": [102.6],
            "underlying_price": 100.0}


def _bull_call():
    return {"symbol": "SPY", "type": "BULL_CALL", "expiration": _EXP, "dte": 30,
            "legs": [{"kind": "call", "side": "long", "strike": 100, "mark": 3.0, "qty": 1},
                     {"kind": "call", "side": "short", "strike": 105, "mark": 1.0, "qty": 1}],
            "net_debit": 200.0, "net_credit": None, "max_loss": 205.0,
            "max_profit": 295.0, "unbounded": False, "breakevens": [102.0],
            "underlying_price": 100.0}


def test_create_long_call_is_debit_trade():
    t = paper_trader.create_paper_trade(_long_call(), quantity=2)
    assert t["direction"] == "DEBIT" and t["strategy"] == "LONG_CALL" and t["status"] == "OPEN"
    assert t["entry_debit"] == 250.0 and t["entry_debit_total"] == 500.0
    assert t["max_loss_total"] == 520.0                # 260 × 2
    assert t["entry_credit"] == -2.5                   # per-share debit reads as neg credit
    assert t["unbounded"] is True
    assert t["legs"][0] == {"kind": "call", "side": "long", "strike": 100,
                            "expiration": _EXP, "qty": 1}   # greeks stripped
    assert t["short_strike"] is None                  # not a credit-spread shape


def test_create_bull_call_debit_vertical():
    t = paper_trader.create_paper_trade(_bull_call(), quantity=1)
    assert t["direction"] == "DEBIT" and len(t["legs"]) == 2
    assert t["entry_debit"] == 200.0 and t["max_profit_total"] == 295.0 and t["unbounded"] is False


def test_create_credit_spread_unchanged():
    pcs = {"symbol": "SPY", "type": "PCS", "expiration": _EXP, "short_strike": 690,
           "long_strike": 688, "width": 2.0, "credit": 0.6, "max_loss": 1.4,
           "trade_type": "SWING"}
    t = paper_trader.create_paper_trade(pcs, quantity=1)
    assert t.get("direction") is None and t["strategy"] == "PCS"
    assert t["entry_credit"] == 0.6 and t["short_strike"] == 690   # credit path intact


def test_expire_long_call_itm():
    t = paper_trader.create_paper_trade(_long_call(strike=100, net_debit=250.0), quantity=2)
    paper_trader.expire_paper_trade(t, settlement_price=108.0)   # intrinsic $8/share = $800
    assert t["status"] == "EXPIRED"
    assert t["exit_debit"] == 8.0                       # exit value per share
    assert t["realized_pnl"] == (800.0 - 250.0) * 2     # ($800 − $250) × 2 = $1100


def test_expire_long_call_otm_loses_debit():
    t = paper_trader.create_paper_trade(_long_call(net_debit=250.0), quantity=1)
    paper_trader.expire_paper_trade(t, settlement_price=95.0)
    assert t["realized_pnl"] == -250.0 and t["exit_debit"] == 0.0


def test_expire_bull_call_max_profit():
    t = paper_trader.create_paper_trade(_bull_call(), quantity=1)   # width 5, debit $200
    paper_trader.expire_paper_trade(t, settlement_price=110.0)      # both ITM → worth $500
    assert t["realized_pnl"] == 500.0 - 200.0          # $300 = width×100 − debit
