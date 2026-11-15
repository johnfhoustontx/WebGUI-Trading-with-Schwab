"""``signal_recommender.exit_levels``: the Stop and Target the rules close at.

The paper books show these levels in their tables, so they must be the rules'
own: for every P&L on a grid, ``recommend`` closes on a P&L stop exactly when the
P&L is at or below the level's stop, and takes the profit exactly when it is at
or above the target. Delta and time are held far from their stops, which are not
P&L levels and are not shown.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import signal_recommender as sr

CT = ZoneInfo("America/Chicago")
MIDDAY = dt.datetime(2026, 11, 16, 11, 0, tzinfo=CT)
CONF = {"trail_start": 0.25, "trail_giveback": 0.10,
        "quiet_windows": [], "captured_at_target": "close", "captured": True,
        "account": True, "ledger": True, "ledger_self_managed": True,
        "ledger_take_profit": True}
GRID = [x / 2 for x in range(-500, 301)]          # -$250 .. +$150 in 50-cent steps
STOPS = {"MONEY_STOP", "BREAKEVEN_STOP", "TRAIL_STOP", "DEBIT_STOP"}


@pytest.fixture(autouse=True)
def _conf(monkeypatch):
    monkeypatch.setattr(sr._trade_mgmt, "protect", lambda: dict(CONF))


def _credit(**kw):
    """A $1.00-credit put spread, delta and time far from their stops."""
    return {"entry_credit": 1.00, "strategy": "PCS", "dte_remaining": 30,
            "current_short_delta": -0.10, "entry_short_delta": -0.15,
            "mark_time": MIDDAY, **kw}


def _agrees(ctx, levels, **kw):
    for pnl in GRID:
        code = (sr.protect_only(dict(ctx, unrealized_pnl=pnl), **kw) if kw
                else sr.recommend(dict(ctx, unrealized_pnl=pnl)))["code"]
        stop, target = levels["stop"], levels["target"]
        assert (code in STOPS) == (stop is not None and pnl <= stop), (pnl, code, levels)
        if code not in STOPS:
            assert (code == "TARGET_HIT") == (target is not None and pnl >= target), \
                (pnl, code, levels)


@pytest.mark.parametrize("extra, stop, kind, target", [
    ({}, -200.0, "money", 50.0),                                   # nothing armed
    ({"peak_pnl": 40.0}, 30.0, "trail", 50.0),                     # the trail, armed
    ({"peak_pnl": 20.0}, -200.0, "money", 50.0),                   # under its start
])
def test_a_credit_spreads_levels_are_where_recommend_closes(extra, stop, kind, target):
    ctx = _credit(**extra)
    got = sr.exit_levels(ctx)
    assert (got["stop"], got["stop_kind"], got["target"]) == (stop, kind, target)
    _agrees(ctx, got)


def test_riding_under_break_even_has_no_target_and_the_higher_stop():
    """A lifecycle position armed at +50% rides on: no target, and the stop is
    the higher of break-even plus commissions and the trail."""
    ctx = _credit(lifecycle=True, be_armed=True, be_level=2.6, peak_pnl=60.0,
                  trail_ladder=sr.DEFAULT_TRAIL_LADDER, peak_pnl_frac=0.6)
    got = sr.exit_levels(ctx)
    assert got == {"stop": 50.0, "stop_kind": "trail", "target": None}
    _agrees(ctx, got)
    ctx["peak_pnl"] = None
    got = sr.exit_levels(ctx)
    assert got == {"stop": 2.6, "stop_kind": "break-even", "target": None}
    _agrees(ctx, got)


def test_a_debit_trades_levels():
    """A $200 long call: target 50% of the debit, trail on the same base."""
    ctx = {"strategy": "LONG_CALL", "direction": "DEBIT", "entry_credit": -2.00,
           "entry_debit": 200.0, "max_profit": None, "dte_remaining": 60,
           "dte_at_entry": 60, "mark_time": MIDDAY, "peak_pnl": 80.0}
    got = sr.exit_levels(ctx)
    assert got["stop"] == 60.0 and got["stop_kind"] == "trail"
    assert got["target"] == pytest.approx(100.0)
    _agrees(ctx, got)


def test_the_ledgers_credit_trades_have_no_loss_stop_and_take_the_profit():
    ctx = {"strategy": "PCS", "entry_credit": 1.00, "mark_time": MIDDAY}
    got = sr.exit_levels(ctx, protect_only_book=True, take_profit=True)
    assert got == {"stop": None, "stop_kind": None, "target": 50.0}
    _agrees(ctx, got, take_profit=True)
    ctx["peak_pnl"] = 40.0
    got = sr.exit_levels(ctx, protect_only_book=True, take_profit=True)
    assert got == {"stop": 30.0, "stop_kind": "trail", "target": 50.0}
    _agrees(ctx, got, take_profit=True)


def test_without_take_profit_the_ledger_shows_no_target_and_never_takes_one():
    ctx = {"strategy": "PCS", "entry_credit": 1.00, "mark_time": MIDDAY}
    assert sr.exit_levels(ctx, protect_only_book=True)["target"] is None
    assert sr.protect_only(dict(ctx, unrealized_pnl=90.0))["code"] == "HOLD"


def test_no_credit_is_no_level_never_zero():
    assert sr.exit_levels({"strategy": "PCS", "entry_credit": None}) == {
        "stop": None, "stop_kind": None, "target": None}
