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
    _legs, long_call, long_put, long_straddle, pcs, put_backspread, put_butterfly,
    structure)

_CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 17, 10, 35, tzinfo=_CT)
CFG = {"enabled": True, "quantity": 1, "mark_interval_min": 5,
       "debit_stop_frac": 0.60, "clean_mae_pct": 25.0, "min_n": 20,
       "review_model": "sonnet"}

PCS_IDEA = T.normalize(pcs())
LONG_CALL_IDEA = T.normalize(long_call(net_debit=250.0, commission=0.0))
LONG_PUT_IDEA = T.normalize(long_put())
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


# ── the stream tracker: every idea row must survive trade_tracker_client ─────
IC_IDEA = T.normalize(pcs(type="IC", call_short=775.0, call_long=780.0,
                          net_credit=1.42, id="SPY_IC"))
EVERY_SHAPE = [LONG_CALL_IDEA, BACKSPREAD_IDEA, STRADDLE_IDEA, PCS_IDEA, IC_IDEA]


@pytest.mark.parametrize("idea", EVERY_SHAPE, ids=lambda i: i["type"])
def test_every_idea_row_builds_the_trackers_payload(idea):
    """``_payload`` subscripts short_strike / long_strike; a row without them raised
    AFTER add_trade had committed it."""
    import trade_tracker_client
    row = idea_ledger.build_row(idea, NOW, CFG)
    payload = trade_tracker_client._payload(row)
    assert payload["trade_id"] == row["trade_id"]
    assert payload["entry_credit"] == row["entry_credit"]


@pytest.fixture
def real_track(tmp_path, monkeypatch):
    """The real ``track`` path; only the HTTP post inside it is stubbed."""
    import paper_trader
    import trade_tracker_client
    import trades_db

    from shared import paper_limits
    posts = []

    class _Resp:
        status_code = 200

    def fake_post(url, json=None, **kw):
        posts.append((url, json))
        return _Resp()

    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(trades_db, "_initialised", set())
    monkeypatch.setattr(trade_tracker_client.requests, "post", fake_post)
    monkeypatch.setattr(trade_tracker_client, "_headers", lambda: {})
    monkeypatch.setattr(paper_limits, "idea_trades", lambda: dict(CFG))
    return paper_trader, posts


@pytest.mark.parametrize("idea", EVERY_SHAPE, ids=lambda i: i["type"])
def test_book_goes_through_the_real_tracker_for_every_shape(real_track, idea):
    paper_trader, posts = real_track
    out = idea_ledger.book(idea, NOW)
    assert out["status"] == "booked"
    ((url, payload),) = posts
    assert url.endswith("/track") and payload["trade_id"] == out["trade_id"]
    assert len(paper_trader.get_all_trades()) == 1


def test_an_idea_debit_row_tracks_like_the_ledgers_own_debit_row():
    """Same keys, same None / negative conventions as ``_create_debit_trade``."""
    import paper_trader
    import trade_tracker_client

    scanner_row = long_call(net_debit=250.0, commission=0.0)
    own = paper_trader.create_paper_trade(
        {**scanner_row, "max_loss": 250.0, "max_profit": None, "unbounded": True}, 1)
    mine = idea_ledger.build_row(LONG_CALL_IDEA, NOW, CFG)
    a, b = trade_tracker_client._payload(own), trade_tracker_client._payload(mine)
    assert set(a) == set(b)
    for key in ("strategy", "expiration", "quantity", "entry_credit", "short_strike",
                "long_strike", "call_short", "call_long", "target_mid", "stop_mid"):
        assert a[key] == b[key], key
    assert b["entry_credit"] < 0 and b["short_strike"] is None


# ── the 5-minute idea cycle: marks, the worst point, target / stop / expiry ──
def _mark_row(**over):
    """A booked long call (entry -2.50 a share, target 125, stop 150), overridable."""
    row = idea_ledger.build_row(LONG_CALL_IDEA, NOW, CFG)
    row.update(over)
    return row


def _at(minutes):
    return NOW + dt.timedelta(minutes=minutes)


def test_mae_only_moves_down():
    row = _mark_row()
    seen = []
    for i, pnl in enumerate((-40.0, -10.0, -80.0), start=1):
        row, hit = idea_ledger.apply_mark(row, pnl, _at(5 * i))
        assert hit is None
        seen.append((row["mae_pnl"], row["mae_at"], row["last_mark_pnl"]))
    stamp = lambda m: _at(m).isoformat(timespec="seconds")   # noqa: E731
    assert seen == [(-40.0, stamp(5), -40.0), (-40.0, stamp(5), -10.0),
                    (-80.0, stamp(15), -80.0)]
    assert row["last_mark_at"] == stamp(15)


def test_mae_stays_zero_when_never_negative():
    row = _mark_row()
    for i, pnl in enumerate((0.0, 30.0, 5.0), start=1):
        row, _ = idea_ledger.apply_mark(row, pnl, _at(5 * i))
    assert row["mae_pnl"] == 0.0 and row.get("mae_at") is None
    assert row["last_mark_pnl"] == 5.0


def test_apply_mark_is_pure():
    row = _mark_row()
    before = dict(row)
    idea_ledger.apply_mark(row, -60.0, _at(5))
    assert row == before


def test_stop_checked_before_target():
    both = _mark_row(stop_pnl=100.0, target_pnl=-150.0)     # -120 satisfies both
    assert idea_ledger.apply_mark(both, -120.0, _at(5))[1] == "STOP"
    edge = _mark_row(stop_pnl=100.0, target_pnl=None)
    assert idea_ledger.apply_mark(edge, -100.0, _at(5))[1] == "STOP"   # at the line
    assert idea_ledger.apply_mark(edge, -99.99, _at(5))[1] is None
    tgt = _mark_row(stop_pnl=None, target_pnl=125.0)
    assert idea_ledger.apply_mark(tgt, 125.0, _at(5))[1] == "TARGET"
    assert idea_ledger.apply_mark(tgt, -10_000.0, _at(5))[1] is None    # no stop set


def test_close_row_is_pure_and_books_the_value_given():
    row = _mark_row(quantity=3)
    before = dict(row)
    out = idea_ledger.close_row(row, -3.10, "TARGET", _at(5))
    assert row == before
    assert out["status"] == "CLOSED" and out["exit_reason"] == "TARGET"
    assert out["realized_pnl"] == pytest.approx(180.0)       # (-2.50 + 3.10) x 100 x 3
    assert out["exit_time"] == _at(5).isoformat(timespec="seconds")
    assert idea_ledger.close_row(row, 0.0, "EXPIRED", _at(5))["status"] == "EXPIRED"


def test_exit_debit_is_the_value_received_on_a_debit_row():
    """The Ledger's own convention (``paper_trader.close_paper_trade``): a debit row
    stores the POSITIVE value it was sold for, not the negative cost to close."""
    out = idea_ledger.close_row(_mark_row(quantity=3), -3.10, "TARGET", _at(5))
    assert out["exit_debit"] == pytest.approx(3.10)
    assert out["exit_debit_total"] == pytest.approx(930.0)
    assert out["realized_pnl"] == pytest.approx(180.0)


def test_exit_debit_is_the_debit_paid_on_a_credit_row():
    row = idea_ledger.build_row(PCS_IDEA, NOW, {**CFG, "quantity": 2})   # 1.224 credit
    out = idea_ledger.close_row(row, 0.60, "TARGET", _at(5))
    assert out["exit_debit"] == pytest.approx(0.60)
    assert out["exit_debit_total"] == pytest.approx(120.0)
    assert out["realized_pnl"] == pytest.approx(124.8)       # (1.224 - 0.60) x 100 x 2
    assert idea_ledger.exit_debit_of(row, 0.60) == pytest.approx(0.60)
    assert idea_ledger.exit_debit_of(_mark_row(), -3.10) == pytest.approx(3.10)


@pytest.fixture
def cycle(ledger, monkeypatch):
    """``mark_cycle`` over a real tmp trades.db. The chain mark is scripted per trade
    id (a cost to close per share), the settlement price is ``state['sp']``, and every
    reprice and cache clear is recorded."""
    import paper_engine
    import signal_repricer
    import structure_marks
    state = {"values": {}, "sp": None, "repriced": [], "cleared": 0, "legs_json": []}

    def fake_reprice(row, client, today=None):
        state["repriced"].append(row["trade_id"])
        state["legs_json"].append(row["legs_json"] == row["legs"])
        value = state["values"].get(row["trade_id"])
        if isinstance(value, Exception):
            raise value
        if value is None:
            return {"current_value": None, "unrealized_pnl": None, "error": "no chain"}
        return {"current_value": value,
                "unrealized_pnl": structure_marks.pnl_of(row, value), "error": None}

    def clear():
        state["cleared"] += 1

    monkeypatch.setattr(structure_marks, "reprice", fake_reprice)
    monkeypatch.setattr(paper_engine, "settlement_underlying",
                        lambda client, symbol, expiration, today: state["sp"])
    monkeypatch.setattr(signal_repricer, "clear_chain_cache", clear)
    return state


def _only_row(ledger):
    (row,) = ledger.get_all_trades()
    return row


def test_cycle_marks_and_records_the_worst_point(ledger, cycle):
    tid = idea_ledger.book(LONG_CALL_IDEA, NOW)["trade_id"]
    cycle["values"][tid] = -1.70                               # -0.80 a share: -80
    out = idea_ledger.mark_cycle(_at(5), client="CLIENT")
    assert out == {"marked": 1, "closed": 0, "settled": 0, "skipped": 0}
    row = _only_row(ledger)
    assert row["status"] == "OPEN"
    assert (row["last_mark_pnl"], row["mae_pnl"]) == (-80.0, -80.0)
    assert row["mae_at"] == row["last_mark_at"] == _at(5).isoformat(timespec="seconds")
    assert cycle["legs_json"] == [True] and cycle["cleared"] == 1


def test_target_closes_at_mark_value_not_level(ledger, cycle, monkeypatch):
    from shared import paper_limits
    monkeypatch.setattr(paper_limits, "idea_trades", lambda: {**CFG, "quantity": 2})
    tid = idea_ledger.book(LONG_CALL_IDEA, NOW)["trade_id"]
    assert _only_row(ledger)["target_pnl"] == 125.0
    cycle["values"][tid] = -4.30                               # +1.80 a share: +180
    out = idea_ledger.mark_cycle(_at(5))
    assert out == {"marked": 1, "closed": 1, "settled": 0, "skipped": 0}
    row = _only_row(ledger)
    assert row["status"] == "CLOSED" and row["exit_reason"] == "TARGET"
    assert row["exit_debit"] == pytest.approx(4.30)            # the value received
    assert row["realized_pnl"] == pytest.approx(360.0)         # 180 x 2, not 125 x 2
    assert row["last_mark_pnl"] == 180.0


def test_stop_closes_at_mark_value(ledger, cycle):
    tid = idea_ledger.book(LONG_CALL_IDEA, NOW)["trade_id"]   # stop 150
    cycle["values"][tid] = -0.70                               # -1.80 a share: -180
    assert idea_ledger.mark_cycle(_at(5))["closed"] == 1
    row = _only_row(ledger)
    assert row["exit_reason"] == "STOP" and row["realized_pnl"] == pytest.approx(-180.0)
    assert row["mae_pnl"] == -180.0


def test_no_mark_leaves_row_untouched(ledger, cycle):
    idea_ledger.book(LONG_CALL_IDEA, NOW)
    before = _only_row(ledger)
    out = idea_ledger.mark_cycle(_at(5))
    assert out == {"marked": 0, "closed": 0, "settled": 0, "skipped": 1}
    assert _only_row(ledger) == before


def test_cycle_leaves_the_users_own_rows_alone(ledger, cycle):
    own = dict(idea_ledger.build_row(LONG_CALL_IDEA, NOW, CFG), source=None,
               trade_id="mine")
    ledger.add_trade(own)
    cycle["values"]["mine"] = -4.30
    assert idea_ledger.mark_cycle(_at(5)) == {
        "marked": 0, "closed": 0, "settled": 0, "skipped": 0}
    assert cycle["repriced"] == [] and _only_row(ledger)["status"] == "OPEN"


def test_cycle_continues_after_one_row_raises(ledger, cycle):
    from services import _degrade
    first = idea_ledger.book(LONG_CALL_IDEA, NOW)["trade_id"]
    second = idea_ledger.book(PCS_IDEA, NOW)["trade_id"]
    cycle["values"][first] = RuntimeError("chain blew up")
    cycle["values"][second] = 1.10                             # +12.40 on a 1.224 credit
    _degrade.reset()
    out = idea_ledger.mark_cycle(_at(5))
    assert out == {"marked": 1, "closed": 0, "settled": 0, "skipped": 1}
    assert sorted(cycle["repriced"]) == sorted([first, second])
    by_id = {t["trade_id"]: t for t in ledger.get_all_trades()}
    assert by_id[second]["last_mark_pnl"] == pytest.approx(12.4)
    assert by_id[first]["last_mark_pnl"] is None
    assert _degrade.counts().get("options.idea_ledger.mark_cycle") == 1


def test_booking_switched_off_still_marks_closes_and_settles(ledger, cycle, monkeypatch):
    """``enabled = false`` stops BOOKING only: rows already booked are marked,
    closed and settled until they close, so switching it off strands none open."""
    from shared import paper_limits
    marked = idea_ledger.book(LONG_CALL_IDEA, NOW)["trade_id"]          # 09-26
    closing = idea_ledger.book(T.normalize(SHAPES["BEAR_PUT"]), NOW)["trade_id"]
    settling = idea_ledger.book(LONG_PUT_IDEA, NOW)["trade_id"]         # 09-21
    monkeypatch.setattr(paper_limits, "idea_trades", lambda: {**CFG, "enabled": False})
    assert idea_ledger.book(PCS_IDEA, NOW)["status"] == "disabled"
    cycle["values"][marked] = -2.60                                     # +10
    cycle["values"][closing] = -1.95                                    # far past target
    cycle["sp"] = 900.0
    out = idea_ledger.mark_cycle(dt.datetime(2026, 9, 22, 10, 1, tzinfo=_CT))
    assert out == {"marked": 2, "closed": 1, "settled": 1, "skipped": 0}
    by_id = {t["trade_id"]: t for t in ledger.get_all_trades()}
    assert by_id[marked]["status"] == "OPEN" and by_id[marked]["last_mark_pnl"] == 10.0
    assert by_id[closing]["status"] == "CLOSED"
    assert by_id[settling]["status"] == "EXPIRED"


def _events(trade_id):
    import trades_db
    conn = trades_db.connect()
    try:
        return [e["event"] for e in trades_db.fetch_events(conn)
                if e["trade_id"] == trade_id]
    finally:
        conn.close()


def test_marks_write_no_trade_event_and_a_close_writes_one(ledger, cycle):
    tid = idea_ledger.book(LONG_CALL_IDEA, NOW)["trade_id"]
    for i, value in enumerate((-2.40, -2.20, -2.60), start=1):
        cycle["values"][tid] = value
        assert idea_ledger.mark_cycle(_at(5 * i))["marked"] == 1
    assert _only_row(ledger)["last_mark_pnl"] == 10.0 and _only_row(ledger)["mae_pnl"] == -30.0
    assert _events(tid) == ["OPENED"]
    cycle["values"][tid] = -4.30
    assert idea_ledger.mark_cycle(_at(20))["closed"] == 1
    assert _events(tid) == ["OPENED", "CLOSED"]


def test_an_expiry_writes_one_event(ledger, cycle):
    tid = idea_ledger.book(LONG_CALL_IDEA, NOW)["trade_id"]
    cycle["values"][tid] = -2.40
    idea_ledger.mark_cycle(_at(5))
    cycle["sp"] = 193.0
    assert idea_ledger.mark_cycle(_expiry_close(LONG_CALL_IDEA))["settled"] == 1
    assert _events(tid) == ["OPENED", "EXPIRED"]


def test_only_a_close_or_expiry_untracks(ledger, cycle, monkeypatch):
    import trade_tracker_client
    untracked = []
    monkeypatch.setattr(trade_tracker_client, "untrack", untracked.append)
    tid = idea_ledger.book(LONG_CALL_IDEA, NOW)["trade_id"]
    cycle["values"][tid] = -2.40
    idea_ledger.mark_cycle(_at(5))
    assert untracked == []
    cycle["values"][tid] = -4.30
    idea_ledger.mark_cycle(_at(10))
    assert untracked == [tid]


def test_mark_cycle_takes_the_book_lock():
    import paper_lock
    assert paper_lock.is_serialized(idea_ledger.mark_cycle)


def test_mark_cycle_uses_the_proxy_client_by_default(ledger, cycle, monkeypatch):
    import structure_marks

    from services import _proxy
    seen = []
    monkeypatch.setattr(_proxy, "schwab_py_client", "PROXY", raising=False)
    monkeypatch.setattr(structure_marks, "reprice",
                        lambda row, client, today=None: seen.append(client) or {
                            "current_value": None, "unrealized_pnl": None,
                            "error": "no chain"})
    idea_ledger.book(LONG_CALL_IDEA, NOW)
    idea_ledger.mark_cycle(_at(5))
    assert seen == ["PROXY"]


def test_cycle_marks_through_the_real_leg_pricer(ledger, monkeypatch):
    """No stubbed ``reprice``: the row's stored legs reach ``structure_marks`` as
    ``legs_json`` and are priced off a chain at the leg's own strike."""
    import signal_repricer
    chain = {"underlyingPrice": 188.0, "putExpDateMap": {},
             "callExpDateMap": {"2026-09-26:9": {"190.0": [{"bid": 3.0, "ask": 3.2}]}}}
    monkeypatch.setattr(signal_repricer, "_fetch_chain",
                        lambda client, symbol, exp, max_age=None: chain)
    idea_ledger.book(LONG_CALL_IDEA, NOW)
    assert idea_ledger.mark_cycle(_at(5), client="CLIENT")["marked"] == 1
    assert _only_row(ledger)["last_mark_pnl"] == pytest.approx(60.0)   # 3.10 - 2.50


# every structure the idea can post, as the scanner row the hourly slot hands over
_E = "2026-10-09"


def _shape(t, *specs, debit=None, credit=None):
    return structure(id=f"XYZ_{t}", symbol="XYZ", type=t, strategy_label=t,
                     underlying_price=100.0, legs=_legs(_E, *specs),
                     net_debit=debit, net_credit=credit, commission=2.6)


SHAPES = {
    "PCS": pcs(),
    "CCS": pcs(type="CCS", id="SPY_CCS", short_strike=780.0, long_strike=785.0),
    "IC": pcs(type="IC", call_short=775.0, call_long=780.0, net_credit=1.42, id="SPY_IC"),
    "LONG_CALL": long_call(),
    "LONG_PUT": long_put(),
    "SHORT_CALL": _shape("SHORT_CALL", ("short", 1, 105.0, "call"), credit=120.0),
    "SHORT_PUT": _shape("SHORT_PUT", ("short", 1, 95.0, "put"), credit=110.0),
    "BULL_CALL": _shape("BULL_CALL", ("long", 1, 100.0, "call"),
                        ("short", 1, 105.0, "call"), debit=240.0),
    "BEAR_PUT": structure(),
    "BUTTERFLY_CALL": _shape("BUTTERFLY_CALL", ("long", 1, 95.0, "call"),
                             ("short", 2, 100.0, "call"), ("long", 1, 105.0, "call"),
                             debit=150.0),
    "BUTTERFLY_PUT": put_butterfly(),
    "IRON_BUTTERFLY": _shape("IRON_BUTTERFLY", ("long", 1, 90.0, "put"),
                             ("short", 1, 100.0, "put"), ("short", 1, 100.0, "call"),
                             ("long", 1, 110.0, "call"), credit=650.0),
    "CONDOR_CALL": _shape("CONDOR_CALL", ("long", 1, 90.0, "call"),
                          ("short", 1, 95.0, "call"), ("short", 1, 105.0, "call"),
                          ("long", 1, 110.0, "call"), debit=180.0),
    "CONDOR_PUT": _shape("CONDOR_PUT", ("long", 1, 90.0, "put"),
                         ("short", 1, 95.0, "put"), ("short", 1, 105.0, "put"),
                         ("long", 1, 110.0, "put"), debit=180.0),
    "LONG_STRADDLE": long_straddle(),
    "LONG_STRANGLE": _shape("LONG_STRANGLE", ("long", 1, 105.0, "call"),
                            ("long", 1, 95.0, "put"), debit=300.0),
    "CALL_BACKSPREAD": _shape("CALL_BACKSPREAD", ("short", 1, 100.0, "call"),
                              ("long", 2, 105.0, "call"), credit=20.0),
    "PUT_BACKSPREAD": put_backspread(),
}
BOUNDED = [t for t, row in SHAPES.items() if T.normalize(row)["max_loss"] is not None]


def test_every_posted_structure_has_a_shape():
    assert set(SHAPES) == set(T.STRATEGY_LABELS)
    assert all(T.normalize(row)["type"] == t for t, row in SHAPES.items())
    assert set(T.STRATEGY_LABELS) - set(BOUNDED) == {"SHORT_CALL"}   # never posted


def _expiry_close(idea):
    day = dt.date.fromisoformat(idea["expiration"])
    return dt.datetime(day.year, day.month, day.day, 15, 5, tzinfo=_CT)


@pytest.mark.parametrize("kind", BOUNDED)
def test_expiry_settles_every_posted_structure(ledger, cycle, kind):
    """Settled at the legs' intrinsic value, checked against the card's own payoff
    (``trade_idea.payoff``: entry cash with commission in, plus each leg's value)."""
    idea = T.normalize(SHAPES[kind])
    strikes = sorted(lg["strike"] for lg in idea["legs"])
    cycle["sp"] = sp = round((strikes[0] + strikes[-1]) / 2 + 0.37, 2)
    idea_ledger.book(idea, NOW)
    out = idea_ledger.mark_cycle(_expiry_close(idea))
    assert out == {"marked": 0, "closed": 0, "settled": 1, "skipped": 0}
    row = _only_row(ledger)
    assert row["status"] == "EXPIRED" and row["exit_reason"] == "EXPIRED"
    assert row["realized_pnl"] == pytest.approx(
        T.payoff(idea["legs"], idea["entry_cash"], sp), abs=0.01)
    assert cycle["repriced"] == []                 # an expired row is never marked


def test_settlement_lowers_the_worst_point_when_it_is_worse(ledger, cycle):
    idea_ledger.book(LONG_CALL_IDEA, NOW)
    cycle["sp"] = 150.0                            # far below the 190 strike: -250
    idea_ledger.mark_cycle(_expiry_close(LONG_CALL_IDEA))
    row = _only_row(ledger)
    assert row["realized_pnl"] == pytest.approx(-250.0)
    assert row["mae_pnl"] == pytest.approx(-250.0)
    assert row["mae_at"] == _expiry_close(LONG_CALL_IDEA).isoformat(timespec="seconds")


def test_expiry_deferred_without_settlement_price(ledger, cycle):
    idea_ledger.book(LONG_CALL_IDEA, NOW)
    before = _only_row(ledger)
    cycle["sp"] = None
    out = idea_ledger.mark_cycle(_expiry_close(LONG_CALL_IDEA))
    assert out == {"marked": 0, "closed": 0, "settled": 0, "skipped": 1}
    assert _only_row(ledger) == before and cycle["repriced"] == []


def test_settle_only_settles_and_marks_nothing(ledger, cycle):
    due = idea_ledger.book(LONG_CALL_IDEA, NOW)["trade_id"]      # expires 2026-09-26
    later = idea_ledger.book(T.normalize(SHAPES["BEAR_PUT"]), NOW)["trade_id"]  # 10-09
    cycle["sp"], cycle["values"][later] = 200.0, 0.10
    out = idea_ledger.mark_cycle(_expiry_close(LONG_CALL_IDEA), settle_only=True)
    assert out == {"marked": 0, "closed": 0, "settled": 1, "skipped": 0}
    by_id = {t["trade_id"]: t for t in ledger.get_all_trades()}
    assert by_id[due]["status"] == "EXPIRED"
    assert by_id[later]["status"] == "OPEN" and by_id[later]["last_mark_pnl"] is None
    assert cycle["repriced"] == []


def test_paper_settle_slot_settles_idea_rows(ledger, cycle, monkeypatch):
    """The 15:05 ``[slots.paper_settle]`` pass: the 5-minute cycle stops at 15:00,
    so expiry-day settlement of an idea row is this slot's."""
    from services.options_svc import handlers
    monkeypatch.setattr(handlers, "_now_ct", lambda: _expiry_close(LONG_CALL_IDEA))
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: False)
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades", lambda: 0)
    monkeypatch.setattr(handlers, "refresh_paper_account", lambda bus: None)
    monkeypatch.setattr(handlers, "refresh_paper_trades", lambda bus, reprice=True: None)
    monkeypatch.setattr(handlers, "publish_rescue_summary", lambda bus: None)
    idea_ledger.book(LONG_CALL_IDEA, NOW)
    cycle["sp"] = 193.0
    handlers.run_paper_settle(object())
    row = _only_row(ledger)
    assert row["status"] == "EXPIRED" and row["realized_pnl"] == pytest.approx(50.0)
    assert cycle["repriced"] == []


# ── exit commission: what the entry cash already carries ───────────────────
def test_exit_commission_basis():
    """The scanner rows' ``commission`` is the ROUND TRIP (open + close), and the
    idea's entry cash has it in, so a close adds no second commission.

    Credit rows: ``scanner_engine._attach_net_economics`` sets ``commission`` and
    ``net_credit`` (credit less it a share); ``trade_idea`` books ``net_credit``.
    Every other structure: ``strategy_scanner.payoff_metrics`` (which the
    ``structure_scan`` tables are built from too) sets ``commission``; ``trade_idea``
    subtracts it from the net cash."""
    import scanner_engine
    import strategy_scanner

    from services.options_svc import commission as fees
    sig = {"type": "PCS", "symbol": "SPY", "credit": 1.25, "max_loss": 3.75}
    scanner_engine._attach_net_economics(sig)
    open_side = fees.commission_for(2, "SPY", 1)
    assert sig["commission"] == pytest.approx(2 * open_side)        # open + close
    assert sig["net_credit"] == round(1.25 - 2 * open_side / 100, 2)  # cents a share
    credit_idea = T.normalize(pcs(commission=sig["commission"], net_credit=sig["net_credit"]))
    assert credit_idea["entry_cash"] == pytest.approx(sig["net_credit"] * 100)

    leg = {"kind": "call", "side": "long", "strike": 190.0, "expiration": "2026-09-26",
           "qty": 1, "mark": 4.10, "delta": 0.4, "theta": -0.1, "vega": 0.2,
           "gamma": 0.01}
    m = strategy_scanner.payoff_metrics([leg], 186.4, "NVDA")
    assert m["commission"] == pytest.approx(2 * fees.commission_for(1, "NVDA", 1))
    debit_idea = T.normalize(long_call(net_debit=m["net_debit"], commission=m["commission"]))
    assert debit_idea["entry_cash"] == pytest.approx(-410.0 - m["commission"])

    # the close books (entry - value) x 100 x qty and nothing more
    row = idea_ledger.build_row(debit_idea, NOW, {**CFG, "quantity": 2})
    out = idea_ledger.close_row(row, -5.0, "TARGET", _at(5))
    assert out["realized_pnl"] == pytest.approx((row["entry_credit"] + 5.0) * 100 * 2)
