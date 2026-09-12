"""D3: closing a DEBIT ledger trade before expiry must not use the CREDIT formula.

``close_paper_trade`` computed ``realized_pnl = (entry_credit - exit_debit) x qty
x 100`` with no direction branch. A debit trade stores ``entry_credit`` as the
NEGATIVE per-share debit, so a long call bought at $2.00 and sold at $3.00 booked
``(-2.00 - 3.00) x 100 = -$500`` where the truth is **+$100** — a winner recorded
as a five-times-larger loss.

⚠ It is reachable and it is the money path: the Paper Ledger's Close button
(``paper_close`` -> ``compute.close_paper``) is the ONLY pre-expiry exit the
ledger has. ``_expire_debit_trade`` exists precisely because the same formula is
wrong at expiry; nobody gave the manual close the same treatment.

The ``exit_debit`` column therefore means different things by direction, exactly
as it already does at expiry: for a credit spread it is the debit PAID to close,
for a debit trade the credit RECEIVED (the position's value).
"""
import datetime

import paper_trader

_EXP = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()


def _long_call(net_debit=200.0):
    """A long call. $2.00/share paid, so entry_credit is -2.00."""
    return {"symbol": "SPY", "type": "LONG_CALL", "expiration": _EXP, "dte": 30,
            "legs": [{"kind": "call", "side": "long", "strike": 100, "qty": 1}],
            "net_debit": net_debit, "max_loss": net_debit, "max_profit": None,
            "unbounded": True, "breakevens": [102.0], "underlying_price": 100.0}


def _bull_call():
    """$2.00 paid for a $5 vertical: max profit $3.00/share."""
    return {"symbol": "SPY", "type": "BULL_CALL", "expiration": _EXP, "dte": 30,
            "legs": [{"kind": "call", "side": "long", "strike": 100, "qty": 1},
                     {"kind": "call", "side": "short", "strike": 105, "qty": 1}],
            "net_debit": 200.0, "max_loss": 200.0, "max_profit": 300.0,
            "unbounded": False, "breakevens": [102.0], "underlying_price": 100.0}


def _pcs():
    return {"symbol": "SPY", "type": "PCS", "expiration": _EXP, "short_strike": 690,
            "long_strike": 688, "width": 2.0, "credit": 0.60, "max_loss": 1.40,
            "trade_type": "SWING"}


# ── the defect: a winning debit close booked as a loss ──────────────────────

def test_closing_a_long_call_for_more_than_it_cost_is_a_PROFIT():
    """Paid $2.00, sold for $3.00 -> +$100 per contract. The credit formula said
    -$500."""
    t = paper_trader.create_paper_trade(_long_call(), quantity=1)
    paper_trader.close_paper_trade(t, 3.00, "MANUAL_CLOSE")
    assert t["realized_pnl"] == 100.0


def test_closing_a_long_call_for_less_than_it_cost_is_a_LOSS():
    """Paid $2.00, sold for $0.50 -> -$150."""
    t = paper_trader.create_paper_trade(_long_call(), quantity=1)
    paper_trader.close_paper_trade(t, 0.50, "MANUAL_CLOSE")
    assert t["realized_pnl"] == -150.0


def test_a_worthless_debit_close_loses_exactly_the_DEBIT_and_no_more():
    """The floor: a debit trade's whole risk is what it cost. Selling at 0 must
    book -$200, never more — the old formula booked -$200 here by coincidence and
    was wrong everywhere else."""
    t = paper_trader.create_paper_trade(_long_call(), quantity=1)
    paper_trader.close_paper_trade(t, 0.0, "MANUAL_CLOSE")
    assert t["realized_pnl"] == -200.0


def test_the_debit_close_scales_with_QUANTITY():
    t = paper_trader.create_paper_trade(_long_call(), quantity=3)
    paper_trader.close_paper_trade(t, 3.00, "MANUAL_CLOSE")
    assert t["realized_pnl"] == 300.0


def test_a_debit_vertical_closes_on_the_same_arithmetic():
    """$2.00 paid, closed at $3.50 -> +$150."""
    t = paper_trader.create_paper_trade(_bull_call(), quantity=1)
    paper_trader.close_paper_trade(t, 3.50, "MANUAL_CLOSE")
    assert t["realized_pnl"] == 150.0


def test_exit_debit_on_a_debit_row_records_the_value_RECEIVED():
    """Same convention ``_expire_debit_trade`` already uses, so the two exits
    cannot disagree about what the column holds."""
    t = paper_trader.create_paper_trade(_long_call(), quantity=2)
    paper_trader.close_paper_trade(t, 3.00, "MANUAL_CLOSE")
    assert t["exit_debit"] == 3.00
    assert t["exit_debit_total"] == 600.0


def test_the_manual_close_and_the_EXPIRY_settle_agree_on_the_same_value():
    """⚠ The control that matters: closing at $8.00 by hand and expiring at an
    $8.00 intrinsic are the same economics, so they must book the same P&L. They
    did not."""
    a = paper_trader.create_paper_trade(_long_call(), quantity=1)
    paper_trader.close_paper_trade(a, 8.00, "MANUAL_CLOSE")
    b = paper_trader.create_paper_trade(_long_call(), quantity=1)
    paper_trader.expire_paper_trade(b, settlement_price=108.0)   # $8/share intrinsic
    assert a["realized_pnl"] == b["realized_pnl"] == 600.0


# ── the credit path is untouched (the control) ──────────────────────────────

def test_a_credit_spread_close_is_UNCHANGED():
    """Collected $0.60, closed at $0.20 -> +$40. This path was always right."""
    t = paper_trader.create_paper_trade(_pcs(), quantity=1)
    paper_trader.close_paper_trade(t, 0.20, "MANUAL_CLOSE")
    assert t["realized_pnl"] == 40.0


def test_a_credit_spread_closed_against_itself_is_flat():
    t = paper_trader.create_paper_trade(_pcs(), quantity=1)
    paper_trader.close_paper_trade(t, 0.60, "MANUAL_CLOSE")
    assert t["realized_pnl"] == 0.0
