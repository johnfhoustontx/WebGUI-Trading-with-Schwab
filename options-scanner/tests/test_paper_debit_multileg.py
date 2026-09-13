"""The Paper LEDGER must record, reprice and settle a butterfly's two-lot body.

Precondition for sending the Strategy Finder's butterflies and condors to paper
(2026-09-13): the debit path is generic over ``legs`` - ``_create_debit_trade``
copies each leg's ``qty``, ``reprice_legs`` multiplies each leg's mid by it, and
``legs_intrinsic_value`` settles through ``position_intrinsic``. A butterfly is
the first debit structure with a leg quantity other than 1, so a path that
dropped ``qty`` would book it as a long call spread with an extra short call.
The rows are built by the Finder's own ``_assemble``, not by hand.
"""
import datetime as dt

import pytest

import paper_trader
import signal_repricer
import strategy_scanner as ss


def _exp(d):
    return (dt.date.today() + dt.timedelta(days=d)).isoformat()


def _leg(kind, side, strike, mark, qty=1):
    return {"kind": kind, "side": side, "strike": strike, "expiration": _exp(30),
            "qty": qty, "mark": mark, "delta": 0.5, "theta": 0, "vega": 0, "gamma": 0,
            "iv": 28.0}


def _fly_signal():
    legs = [_leg("call", "long", 95.0, 7.0), _leg("call", "short", 100.0, 4.0, qty=2),
            _leg("call", "long", 105.0, 2.0)]
    return ss._assemble("BUTTERFLY_CALL", "NEUTRAL", "Call Butterfly", "neutral",
                        legs, "XYZ", 100.0, 0.28)


def test_a_butterfly_settles_at_its_body_with_the_two_lot_counted():
    trade = paper_trader._create_debit_trade(_fly_signal(), 1, "SWING",
                                             dt.datetime.now(paper_trader.TZ))
    assert [l["qty"] for l in trade["legs"]] == [1, 2, 1]
    per_share, pnl = signal_repricer.legs_intrinsic_value(trade, 100.0)
    assert per_share == 5.0                         # 5 - 0 + 0, not 5 - 0 (qty 1)
    assert pnl == round(500.0 - trade["entry_debit"], 2)
    per_share, _ = signal_repricer.legs_intrinsic_value(trade, 110.0)
    assert per_share == 0.0                         # 15 - 2*10 + 5


def test_a_butterfly_reprices_with_the_two_lot_counted(monkeypatch):
    trade = paper_trader._create_debit_trade(_fly_signal(), 1, "SWING",
                                             dt.datetime.now(paper_trader.TZ))
    key = f"{_exp(30)}:30"

    def q(bid, ask):
        return [{"bid": bid, "ask": ask, "delta": 0.5}]

    chain = {"underlyingPrice": 100.0,
             "callExpDateMap": {key: {"95.0": q(6.9, 7.1), "100.0": q(3.9, 4.1),
                                      "105.0": q(1.9, 2.1)}},
             "putExpDateMap": {}}

    class _Client:
        pass

    monkeypatch.setattr(signal_repricer, "_fetch_chain", lambda client, sym, exp: chain)
    rep = signal_repricer.reprice_legs(trade, _Client())
    assert rep["current_value"] == 1.0              # 7 - 2*4 + 2



def _condor_signal(kind):
    legs = [_leg(kind, "long", 90.0, 11.0), _leg(kind, "short", 95.0, 7.0),
            _leg(kind, "short", 105.0, 2.0), _leg(kind, "long", 110.0, 1.0)]
    return ss._assemble(f"CONDOR_{kind.upper()}", "NEUTRAL", "Condor", "neutral",
                        legs, "XYZ", 100.0, 0.28)


def test_the_ledger_debit_set_is_the_shared_taxonomy():
    from shared import structures
    assert paper_trader.PAPER_DEBIT_TYPES == set(structures.LEDGER_DEBIT)


def test_a_finder_butterfly_and_condor_open_through_the_DEBIT_path():
    """Before 2026-09-13 these fell into the credit branch and KeyErrored on
    ``short_strike``. Now they are booked by their legs, qty included."""
    fly = paper_trader.create_paper_trade(_fly_signal(), 2)
    assert fly["direction"] == "DEBIT" and fly["strategy"] == "BUTTERFLY_CALL"
    assert [l["qty"] for l in fly["legs"]] == [1, 2, 1]
    assert fly["max_profit_total"] is not None           # bounded: target on max profit
    condor = paper_trader.create_paper_trade(_condor_signal("call"), 1)
    assert condor["direction"] == "DEBIT" and len(condor["legs"]) == 4


def _ledger_ctx(strategy, pnl, *, dte_remaining, dte_at_entry=30):
    """The ctx ``options_svc.compute._ledger_exit_ctx`` builds for a debit row -
    every dollar figure PER CONTRACT."""
    return {"strategy": strategy, "direction": "DEBIT", "entry_credit": -2.00,
            "entry_debit": 200.0, "max_profit": 300.0, "unrealized_pnl": pnl,
            "dte_remaining": dte_remaining, "dte_at_entry": dte_at_entry,
            "current_short_delta": None}


@pytest.mark.parametrize("name", ("BUTTERFLY_CALL", "BUTTERFLY_PUT",
                                  "CONDOR_CALL", "CONDOR_PUT"))
def test_a_fly_or_condor_entered_at_30_DTE_is_not_time_exited_at_21(name):
    """Operator decision 2026-09-13: these gain most of their value in the last
    two weeks, so a 21-DTE exit would close every 22-30 DTE entry flat. The
    LONG_CALL control proves the ctx is one the time exit does fire on."""
    import signal_recommender
    for dte in (21, 10, 3):
        rec = signal_recommender.recommend(_ledger_ctx(name, 0.0, dte_remaining=dte))
        assert rec["code"] == "HOLD", (name, dte, rec)
    control = signal_recommender.recommend(_ledger_ctx("LONG_CALL", 0.0, dte_remaining=21))
    assert control["code"] == "TIME_EXIT"


def test_a_fly_still_takes_its_target_on_max_profit():
    import signal_recommender
    rec = signal_recommender.recommend(
        _ledger_ctx("BUTTERFLY_CALL", 150.0, dte_remaining=25))
    assert rec["code"] == "TARGET_HIT"                 # 50% of the $300 max profit
