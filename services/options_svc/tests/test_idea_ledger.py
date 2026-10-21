"""A posted trade idea, booked into the Paper Ledger (trades.db) as a "Trade idea" row.

The idea dicts are built by running real scanner-row fixtures (the ones
``test_trade_idea`` uses) through ``trade_idea.normalize``, so the shapes -- a credit
row's per-share money, a directional row's per-contract money, the legs -- are the
ones the hourly slot really hands over.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import idea_ledger, site_ideas
from services.options_svc import trade_idea as T
from services.options_svc.tests.test_trade_idea import (
    long_call, long_straddle, pcs, put_backspread)

_CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 17, 10, 35, tzinfo=_CT)
CFG = {"enabled": True, "quantity": 1, "mark_interval_min": 5,
       "debit_stop_frac": 0.60, "clean_mae_pct": 25.0, "min_n": 20,
       "review_model": "sonnet"}

PCS_IDEA = T.normalize(pcs())
LONG_CALL_IDEA = T.normalize(long_call(net_debit=250.0, commission=0.0))
BACKSPREAD_IDEA = T.normalize(put_backspread(net_debit=50.0, net_credit=None,
                                             commission=0.0))
STRADDLE_IDEA = T.normalize(long_straddle())


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A real trades.db under tmp_path, idea booking on."""
    import trade_tracker_client
    import trades_db

    from shared import paper_limits
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(trades_db, "_initialised", set())
    monkeypatch.setattr(trade_tracker_client, "track", lambda t: True)
    monkeypatch.setattr(trade_tracker_client, "untrack", lambda tid: True)
    monkeypatch.setattr(paper_limits, "idea_trades", lambda: dict(CFG))
    import paper_trader
    return paper_trader


def test_the_fixtures_are_the_shapes_the_tests_name():
    assert LONG_CALL_IDEA["entry_cash"] == -250.0
    assert BACKSPREAD_IDEA["entry_cash"] < 0
    assert STRADDLE_IDEA["type"] == "LONG_STRADDLE"


def test_build_row_debit_gets_own_stop():
    row = idea_ledger.build_row(LONG_CALL_IDEA, NOW, CFG)
    assert row["source"] == "trade_idea" and row["direction"] == "DEBIT"
    assert row["entry_credit"] == -2.50 and row["stop_pnl"] == 150.0
    assert row["target_pnl"] == site_ideas.exit_levels(
        site_ideas.entry_facts(LONG_CALL_IDEA, posted=NOW))[0]
    assert row["entry_debit"] == 250.0 and row["entry_debit_total"] == 250.0
    assert row["entry_credit_total"] == -250.0
    assert row["max_loss_per"] == 2.50 and row["max_loss_total"] == 250.0
    assert row["max_profit_total"] is None and row["unbounded"] is True


def test_build_row_credit_keeps_its_stop_and_strikes():
    row = idea_ledger.build_row(PCS_IDEA, NOW, CFG)
    target, stop = site_ideas.exit_levels(
        site_ideas.entry_facts(PCS_IDEA, posted=NOW))
    assert stop is not None                       # stop_mult x credit, not ours
    assert (row["target_pnl"], row["stop_pnl"]) == (target, stop)
    assert row["direction"] is None
    assert row["entry_credit"] == pytest.approx(1.224)
    assert row["entry_credit_total"] == pytest.approx(122.4)
    assert (row["short_strike"], row["long_strike"], row["width"]) == (740.0, 735.0, 5.0)
    assert row["max_loss_per"] == pytest.approx(3.776)
    assert row["max_loss_total"] == pytest.approx(377.6)
    assert row["max_profit_total"] == pytest.approx(122.4) and not row["unbounded"]
    assert row["breakeven"] == "738.78"
    assert row["legs"] == PCS_IDEA["legs"]


def test_build_row_iron_condor_fills_both_sides():
    ic = T.normalize(pcs(type="IC", call_short=775.0, call_long=780.0,
                         net_credit=1.42, id="SPY_IC"))
    row = idea_ledger.build_row(ic, NOW, CFG)
    assert (row["short_strike"], row["long_strike"]) == (740.0, 735.0)
    assert (row["call_short"], row["call_long"]) == (775.0, 780.0)
    assert row["width"] == 5.0
    assert len(row["legs"]) == 4


def test_build_row_carries_the_post_identity():
    row = idea_ledger.build_row(PCS_IDEA, NOW, CFG)
    assert row["mode"] == "PAPER" and row["status"] == "OPEN"
    assert row["symbol"] == "SPY" and row["strategy"] == "PCS"
    assert row["trade_type"] == "SWING" and row["dte_at_entry"] == 8
    assert row["quantity"] == 1 and row["expiration"] == "2026-09-25"
    assert row["underlying_at_entry"] == 759.38
    assert row["entry_time"] == row["idea_posted"] == "2026-09-17T10:35:00-05:00"
    assert row["idea_id"] == PCS_IDEA["id"]
    assert (row["idea_grade"], row["idea_score"]) == ("Good", 70.0)
    assert row["idea_label"] == "Put Credit Spread"
    assert row["mae_pnl"] == 0.0 and row["entry_source"] == "card"
    assert row["notes"] == "Trade idea"
    assert len(row["trade_id"]) == 8


def test_build_row_scales_money_by_the_configured_quantity():
    row = idea_ledger.build_row(PCS_IDEA, NOW, {**CFG, "quantity": 3})
    assert row["quantity"] == 3
    assert row["entry_credit"] == pytest.approx(1.224)         # per share, unscaled
    assert row["entry_credit_total"] == pytest.approx(367.2)
    assert row["max_loss_total"] == pytest.approx(1132.8)
    assert row["max_profit_total"] == pytest.approx(367.2)


def test_build_row_without_a_trade_type_stores_blank():
    idea = T.normalize(long_call(trade_type=None, net_debit=250.0, commission=0.0))
    assert idea_ledger.build_row(idea, NOW, CFG)["trade_type"] == ""


def test_build_row_backspread_has_no_target_and_own_stop():
    row = idea_ledger.build_row(BACKSPREAD_IDEA, NOW, CFG)
    assert row["target_pnl"] is None
    assert row["stop_pnl"] == pytest.approx(30.0)              # 0.60 x the $50 debit
    assert row["direction"] == "DEBIT"


def test_build_row_straddle_is_bookable():
    row = idea_ledger.build_row(STRADDLE_IDEA, NOW, CFG)
    assert row["strategy"] == "LONG_STRADDLE" and row["source"] == "trade_idea"
    assert row["legs"] == STRADDLE_IDEA["legs"] and len(row["legs"]) == 2
    assert row["entry_debit"] == pytest.approx(586.6)
    assert row["stop_pnl"] == pytest.approx(0.60 * 586.6, abs=0.01)


def test_levels_leave_a_credit_with_no_stop_alone():
    """The own-stop fallback is for a DEBIT only; a credit with no stop has none."""
    idea = dict(PCS_IDEA, type="SHORT_PUT")          # loss_rules = false in trade_mgmt
    assert idea["entry_cash"] > 0
    target, stop = idea_ledger.levels(idea, CFG)
    assert target is not None and stop is None


def test_is_idea_reads_the_source_and_nothing_else():
    assert idea_ledger.is_idea({"source": "trade_idea", "notes": ""})
    assert not idea_ledger.is_idea({"source": None, "notes": "Trade idea"})
    assert not idea_ledger.is_idea({})


def test_book_writes_one_open_row(ledger):
    out = idea_ledger.book(PCS_IDEA, NOW)
    assert out["status"] == "booked" and len(out["trade_id"]) == 8
    (row,) = ledger.get_all_trades()
    assert row["trade_id"] == out["trade_id"] and row["status"] == "OPEN"
    assert row["source"] == "trade_idea" and row["idea_id"] == PCS_IDEA["id"]
    assert row["target_pnl"] is not None


def test_book_bypasses_caps(ledger, monkeypatch):
    from shared import book_caps

    def boom(*a, **k):
        raise AssertionError("the cap module was consulted")
    monkeypatch.setattr(book_caps, "evaluate", boom)
    monkeypatch.setattr(book_caps, "first_breach", boom)
    out = idea_ledger.book(STRADDLE_IDEA, NOW)         # a structure the Ledger refuses
    assert out["status"] == "booked"
    assert len(ledger.get_all_trades()) == 1


def test_book_is_idempotent_per_post(ledger):
    first = idea_ledger.book(PCS_IDEA, NOW)
    second = idea_ledger.book(PCS_IDEA, NOW)
    assert first["status"] == "booked" and second["status"] == "duplicate"
    assert second["trade_id"] is None
    assert len(ledger.get_all_trades()) == 1


def test_a_closed_row_still_counts_as_the_post(ledger):
    out = idea_ledger.book(PCS_IDEA, NOW)
    row = next(t for t in ledger.get_all_trades() if t["trade_id"] == out["trade_id"])
    ledger.update_trade(out["trade_id"], {**row, "status": "CLOSED"})
    assert idea_ledger.book(PCS_IDEA, NOW)["status"] == "duplicate"
    assert len(ledger.get_all_trades()) == 1


def test_repost_on_later_day_books_second_row(ledger):
    idea_ledger.book(PCS_IDEA, NOW)
    again = idea_ledger.book(PCS_IDEA, NOW + dt.timedelta(days=1))
    assert again["status"] == "booked"
    rows = ledger.get_all_trades()
    assert len(rows) == 2 and {r["status"] for r in rows} == {"OPEN"}


def test_book_disabled_books_nothing(ledger, monkeypatch):
    from shared import paper_limits
    monkeypatch.setattr(paper_limits, "idea_trades",
                        lambda: {**CFG, "enabled": False})
    out = idea_ledger.book(PCS_IDEA, NOW)
    assert out == {"status": "disabled", "trade_id": None}
    assert ledger.get_all_trades() == []


def test_book_takes_the_book_lock(ledger, monkeypatch):
    import threading

    import paper_lock
    held = []
    real_add = ledger.add_trade

    def checking_add(trade):
        # another thread cannot take the lock while this one holds it
        got = []
        t = threading.Thread(
            target=lambda: got.append(paper_lock.BOOK_LOCK.acquire(timeout=0)))
        t.start()
        t.join()
        if got[0]:
            paper_lock.BOOK_LOCK.release()
        held.append(not got[0])
        return real_add(trade)

    monkeypatch.setattr(ledger, "add_trade", checking_add)
    idea_ledger.book(PCS_IDEA, NOW)
    assert held == [True]
    assert paper_lock.is_serialized(idea_ledger.book)
