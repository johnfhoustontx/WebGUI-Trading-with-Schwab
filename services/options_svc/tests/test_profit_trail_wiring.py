"""The profit trail reaches captured signals and the Paper Ledger.

Design: docs/plans/2026-11-15-profit-trail-design.md. The rule is tested in
options-scanner/tests/test_profit_trail.py and its settings in
shared/tests/test_trade_mgmt_protect.py; these tests pin the inputs each book
hands it and what each book does with the answer.
"""
import datetime as dt
import sys
import types
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute, idea_ledger, profit_trail
from shared import trade_mgmt as tm

CT = ZoneInfo("America/Chicago")
MIDDAY = dt.datetime(2026, 11, 16, 11, 0, tzinfo=CT)
CONF = {"trail_start": 0.25, "trail_giveback": 0.10,
        "quiet_windows": [(dt.time(8, 30), dt.time(8, 45))],
        "captured_at_target": "close", "captured": True, "account": True,
        "ledger": True, "ledger_self_managed": True, "ledger_take_profit": True}


@pytest.fixture
def conf(monkeypatch):
    """Pin the settings, and hand the test the dict to change."""
    c = dict(CONF)
    monkeypatch.setattr(tm, "protect", lambda: dict(c))
    return c


# ── captured signals ────────────────────────────────────────────────────────

def test_captured_takes_the_profit_at_the_target_as_shipped():
    """The shipped choice, measured about $14 a trade better than riding."""
    assert tm.protect()["captured_at_target"] == "close"
    assert profit_trail.captured_rides() is False
    assert "TARGET_HIT" in compute._CAPTURED_CLOSE_CODES
    assert "TRAIL_STOP" in compute._CAPTURED_CLOSE_CODES


def test_captured_kwargs_take_this_mark_into_the_best(conf):
    """The stored best lags by a mark; the mark being built is taken into it."""
    got = profit_trail.captured_kwargs({"unrealized_pnl": 40.0}, MIDDAY, peak=30.0)
    assert got == {"lifecycle": False,
                   "protect": {"peak_pnl": 40.0, "mark_time": MIDDAY}}
    got = profit_trail.captured_kwargs({"unrealized_pnl": 10.0}, MIDDAY, peak=30.0)
    assert got["protect"]["peak_pnl"] == 30.0


def test_no_marks_and_no_reprice_is_no_peak(conf):
    """A peak is never invented: no best is "not armed", never "armed at 0"."""
    got = profit_trail.captured_kwargs({"unrealized_pnl": None}, MIDDAY, peak=None)
    assert got["protect"]["peak_pnl"] is None


def test_ride_and_the_book_switch_are_honoured(conf):
    conf.update(captured_at_target="ride", captured=False)
    got = profit_trail.captured_kwargs({"unrealized_pnl": 40.0}, MIDDAY, peak=30.0)
    assert got == {"lifecycle": True, "protect": {}}


def test_build_mark_closes_at_the_target_under_close(conf):
    """Driven through the real ``build_mark``: the captured path used to ARM at
    +50% and hold; under "close" it is a close code."""
    import signal_recommender as sr
    row = {"signal_id": "s1", "strategy": "PCS", "expiration": "2099-12-31",
           "entry_credit": 1.00, "entry_short_delta": -0.15, "be_armed": 0,
           "short_strike": 500.0, "first_seen_ts": "2026-11-16T09:00:00-06:00"}
    rep = {"unrealized_pnl": 55.0, "current_value": 0.45, "current_underlying": 510.0,
           "current_short_delta": -0.10}
    mark = sr.build_mark(row, rep, MIDDAY, be_level=2.6,
                         **profit_trail.captured_kwargs(rep, MIDDAY, peak=50.0))
    assert mark["recommendation_code"] == "TARGET_HIT"
    conf["captured_at_target"] = "ride"
    mark = sr.build_mark(row, rep, MIDDAY, be_level=2.6,
                         **profit_trail.captured_kwargs(rep, MIDDAY, peak=50.0))
    assert mark["recommendation_code"] == "HOLD"


# ── the Paper Ledger: the operator's own trades ─────────────────────────────

def _install(monkeypatch, trades, marks):
    """Stub the Ledger store, the repricer and the peak write."""
    closed, peaks = [], []
    monkeypatch.setitem(sys.modules, "paper_trader", types.SimpleNamespace(
        get_all_trades=lambda: trades,
        close_paper_trade=lambda t, exit_debit, reason: {
            **t, "status": "CLOSED", "exit_debit": exit_debit, "exit_reason": reason},
        update_trade=lambda tid, row: closed.append((tid, row))))
    monkeypatch.setitem(sys.modules, "signal_repricer", types.SimpleNamespace(
        clear_chain_cache=lambda: None,
        reprice_legs=lambda t, client, **kw: marks.get(t["trade_id"], {})))

    class _Conn:
        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "trades_db", types.SimpleNamespace(
        connect=lambda *a, **k: _Conn(),
        raise_peak=lambda conn, tid, pnl, at: peaks.append((tid, pnl))))
    return closed, peaks


def _credit_row(trade_id="c1", mfe=None):
    return {"trade_id": trade_id, "status": "OPEN", "symbol": "QQQ",
            "strategy": "PCS", "expiration": "2099-12-31", "short_strike": 400,
            "long_strike": 399, "entry_credit": 1.00, "quantity": 1,
            "mfe_pnl": mfe}


def test_a_ledger_credit_trade_that_gives_back_the_trail_is_closed(monkeypatch, conf):
    """Best $30 on a $100 credit, now $19: under the $20 trail."""
    rows = [_credit_row(mfe=30.0)]
    closed, peaks = _install(monkeypatch, rows, {"c1": {"unrealized_pnl": 19.0,
                                                       "current_value": 0.81}})
    assert compute.manage_ledger_trades(now_ct=MIDDAY) == 1
    assert closed[0][1]["exit_reason"] == "TRAIL_STOP"
    assert closed[0][1]["mfe_pnl"] == 30.0
    assert peaks == [("c1", 19.0)]


def test_a_ledger_credit_trade_takes_the_profit_at_the_target(monkeypatch, conf):
    """``[protect] ledger_take_profit``: +$58 on a $100 credit is past +50%."""
    rows = [_credit_row("win", mfe=60.0)]
    closed, _ = _install(monkeypatch, rows, {
        "win": {"unrealized_pnl": 58.0, "current_value": 0.42}})
    assert compute.manage_ledger_trades(now_ct=MIDDAY) == 1
    assert closed[0][1]["exit_reason"] == "TARGET_HIT"


def test_a_ledger_credit_trade_gets_no_loss_stop(monkeypatch, conf):
    """Past the money stop it is still held: cutting a loser stays the
    operator's call on their own trades."""
    rows = [_credit_row("loss", mfe=5.0)]
    closed, _ = _install(monkeypatch, rows, {
        "loss": {"unrealized_pnl": -250.0, "current_value": 3.50}})
    assert compute.manage_ledger_trades(now_ct=MIDDAY) == 0
    assert closed == []


def test_with_take_profit_off_the_target_is_not_taken(monkeypatch, conf):
    conf["ledger_take_profit"] = False
    rows = [_credit_row("win", mfe=60.0)]
    closed, _ = _install(monkeypatch, rows, {
        "win": {"unrealized_pnl": 58.0, "current_value": 0.42}})
    assert compute.manage_ledger_trades(now_ct=MIDDAY) == 0


def test_a_new_best_is_recorded_and_carried_into_the_close(monkeypatch, conf):
    """The close writes the whole row back from the loaded copy; the copy must
    carry the best this mark set, or the old one is put back."""
    rows = [_credit_row(mfe=10.0)]
    _, peaks = _install(monkeypatch, rows, {"c1": {"unrealized_pnl": 35.0,
                                                  "current_value": 0.65}})
    compute.manage_ledger_trades(now_ct=MIDDAY)
    assert peaks == [("c1", 35.0)]
    assert rows[0]["mfe_pnl"] == 35.0


def test_with_both_ledger_switches_off_credit_trades_are_untouched(monkeypatch, conf):
    conf.update(ledger=False, ledger_take_profit=False)
    rows = [_credit_row(mfe=30.0)]
    closed, peaks = _install(monkeypatch, rows, {"c1": {"unrealized_pnl": 19.0,
                                                       "current_value": 0.81}})
    assert compute.manage_ledger_trades(now_ct=MIDDAY) == 0
    assert closed == [] and peaks == []


def test_a_ledger_debit_trade_gets_the_trail_on_top_of_its_rules(monkeypatch, conf):
    """$200 long call, best +$80, now +$59: under the $60 trail."""
    rows = [{"trade_id": "d1", "status": "OPEN", "symbol": "SPY",
             "strategy": "LONG_CALL", "direction": "DEBIT", "expiration": "2099-12-31",
             "dte_at_entry": 45, "quantity": 1, "entry_credit": -2.00,
             "entry_debit": 200.0, "max_profit_total": None, "mfe_pnl": 80.0}]
    closed, _ = _install(monkeypatch, rows, {"d1": {"unrealized_pnl": 59.0,
                                                   "current_value": 2.59}})
    assert compute.manage_ledger_trades(now_ct=MIDDAY) == 1
    assert closed[0][1]["exit_reason"] == "TRAIL_STOP"


# ── the Paper Ledger: its self-managed rows ─────────────────────────────────

def _idea_row(**over):
    """A $1.00-credit idea with a stop of $150 and a target of $50."""
    row = {"trade_id": "i1", "status": "OPEN", "source": "trade_idea",
           "strategy": "PCS", "direction": "CREDIT", "entry_credit": 1.00,
           "quantity": 1, "target_pnl": 50.0, "stop_pnl": 150.0, "mae_pnl": 0.0}
    row.update(over)
    return row


def test_the_best_mark_only_rises():
    row = _idea_row()
    for i, pnl in enumerate((10.0, 30.0, 20.0)):
        row, _ = idea_ledger.apply_mark(row, pnl, MIDDAY + dt.timedelta(minutes=5 * i))
    assert row["mfe_pnl"] == 30.0
    assert row["mfe_at"] == (MIDDAY + dt.timedelta(minutes=5)).isoformat(timespec="seconds")


def test_an_idea_that_gives_back_the_trail_closes_as_TRAIL(conf):
    row, hit = idea_ledger.apply_mark(_idea_row(mfe_pnl=30.0), 19.0, MIDDAY)
    assert hit == "TRAIL"


def test_the_ideas_own_lines_come_first(conf):
    assert idea_ledger.apply_mark(_idea_row(mfe_pnl=60.0), 52.0, MIDDAY)[1] == "TARGET"
    assert idea_ledger.apply_mark(_idea_row(mfe_pnl=30.0), -160.0, MIDDAY)[1] == "STOP"


def test_an_idea_waits_out_a_quiet_window(conf):
    opening = MIDDAY.replace(hour=8, minute=35)
    assert idea_ledger.apply_mark(_idea_row(mfe_pnl=30.0), 19.0, opening)[1] is None


def test_the_self_managed_switch(conf):
    conf["ledger_self_managed"] = False
    assert idea_ledger.apply_mark(_idea_row(mfe_pnl=30.0), 19.0, MIDDAY)[1] is None


def test_a_trail_exit_is_its_own_word_everywhere_the_exits_are_counted():
    """Not "closed by hand": the weekly review, /eod and the site each name it."""
    from services.options_svc import site_ideas
    from shared import idea_review
    assert site_ideas._LEDGER_EXIT["TRAIL"] == "trail"
    assert "trail" in site_ideas._FINAL
    assert idea_review.rate({"exit_reason": "TRAIL"}, 25.0) == "trailed"
    fact = idea_review._fact("x", [{"exit_reason": "TRAIL", "realized_pnl": 12.0,
                                    "quantity": 1}], 1)
    assert fact["trails"] == 1 and fact["manual"] == 0
