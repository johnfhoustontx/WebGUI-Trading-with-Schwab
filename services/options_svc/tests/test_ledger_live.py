"""The Paper Ledger marked live from the option stream (``ledger_live``).

Design: docs/plans/2026-11-24-ledger-live-marks-design.md. What these pin:

* a live mark is the HOURLY mark's arithmetic (the repricers' own valuation
  halves on a chain built from the quotes), not a copy that can drift;
* a mark that cannot be made is no mark, never a zero;
* an exit fires only on the operator's own rows, only after the rule has held
  for ``confirm_sec``, and only on a re-read row that is still OPEN;
* the self-managed rows are marked and never closed here.

The real socket is not exercised (as ``flow_stream``): the stream's only job is
``QuoteBook.merge``, tested directly.
"""
import datetime as dt
import json
import sys
import types
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import ledger_live as ll
from shared import trade_mgmt as tm

import signal_repricer as sr
import structure_marks as sm

CT = ZoneInfo("America/Chicago")
MIDDAY = dt.datetime(2026, 11, 16, 11, 0, tzinfo=CT)      # a Monday, in the session
EVENING = dt.datetime(2026, 11, 16, 17, 0, tzinfo=CT)
EXP = "2099-12-18"
CFG = {"enabled": True, "exits": True, "interval_sec": 3, "confirm_sec": 10,
       "peak_write_sec": 60}
PROTECT = {"trail_start": 0.25, "trail_giveback": 0.10, "quiet_windows": [],
           "captured_at_target": "close", "captured": True, "account": True,
           "ledger": True, "ledger_self_managed": True, "ledger_take_profit": True}


@pytest.fixture(autouse=True)
def _protect(monkeypatch):
    monkeypatch.setattr(tm, "protect", lambda: dict(PROTECT))


def _osi(kind, strike, root="SPY"):
    return f"{root:<6}991218{'C' if kind == 'call' else 'P'}{int(strike * 1000):08d}"


def _chain(puts=None, calls=None):
    def side(kind, quotes):
        return {f"{EXP}:30": {f"{k:.1f}": [{"symbol": _osi(kind, k), "bid": b, "ask": a}]
                              for k, (b, a) in (quotes or {}).items()}}
    return {"underlyingPrice": 410.0, "putExpDateMap": side("put", puts),
            "callExpDateMap": side("call", calls)}


PUTS = {400.0: (0.50, 0.60), 399.0: (0.15, 0.20), 395.0: (0.05, 0.10)}
CALLS = {420.0: (0.80, 1.00), 421.0: (0.40, 0.60), 450.0: (2.00, 2.20)}


def _pcs(tid="c1", credit=1.00, **over):
    return {"trade_id": tid, "status": "OPEN", "symbol": "SPY", "strategy": "PCS",
            "expiration": EXP, "short_strike": 400, "long_strike": 399,
            "entry_credit": credit, "quantity": 2, "mfe_pnl": None, **over}


def _long_call(tid="d1"):
    return {"trade_id": tid, "status": "OPEN", "symbol": "SPY", "strategy": "LONG_CALL",
            "direction": "DEBIT", "expiration": EXP, "dte_at_entry": 30, "quantity": 1,
            "entry_credit": -1.50, "entry_debit": 150.0, "max_profit_total": None,
            "legs": [{"kind": "call", "side": "long", "strike": 450, "qty": 1}]}


def _condor(tid="k1"):
    legs = [{"kind": "put", "side": "short", "strike": 400.0, "expiration": EXP, "qty": 1},
            {"kind": "put", "side": "long", "strike": 395.0, "expiration": EXP, "qty": 1}]
    return {"trade_id": tid, "status": "OPEN", "symbol": "SPY", "strategy": "PCS",
            "source": "daily_condor", "expiration": EXP, "entry_credit": 0.80,
            "quantity": 1, "legs": legs, "stop_pnl": 10.0, "target_pnl": 1.0}


def _book(rows, chain=None):
    book = ll.QuoteBook()
    chain = chain or _chain(PUTS, CALLS)
    for t in rows:
        book.seed(t["symbol"], EXP, chain, ll.wanted_legs(t), 0.0)
    return book


# ── which contracts ─────────────────────────────────────────────────────────

def test_the_legs_of_each_kind_of_row():
    assert ll.wanted_legs(_pcs()) == [(EXP, "put", 400.0), (EXP, "put", 399.0)]
    ic = _pcs(strategy="IC", call_short=420, call_long=421)
    assert ll.wanted_legs(ic)[2:] == [(EXP, "call", 420.0), (EXP, "call", 421.0)]
    assert ll.wanted_legs(_long_call()) == [(EXP, "call", 450.0)]
    assert ll.wanted_legs(_condor()) == [(EXP, "put", 400.0), (EXP, "put", 395.0)]


@pytest.mark.parametrize("row", [
    _pcs(strategy="STRADDLE_THING"), _pcs(long_strike=None),
    {**_long_call(), "legs": []}, {**_condor(), "legs": None}, {"expiration": None}])
def test_a_row_that_cannot_be_priced_has_no_legs(row):
    assert ll.wanted_legs(row) is None


# ── the quote book ──────────────────────────────────────────────────────────

def test_a_contract_symbol_is_read_off_the_chain():
    book = _book([_pcs()])
    assert book.osis() == sorted([_osi("put", 400.0), _osi("put", 399.0)])


def test_a_partial_tick_keeps_the_field_it_does_not_carry():
    book = _book([_pcs()])
    book.merge({"symbol": _osi("put", 400.0), "bid": 0.70, "ask": None})
    chain = book.chain("SPY", EXP, ll.wanted_legs(_pcs()))
    assert chain["putExpDateMap"][EXP]["400.0"] == [{"bid": 0.70, "ask": 0.60}]


def test_half_a_quote_is_no_market():
    book = ll.QuoteBook()
    chain = _chain({400.0: (None, 0.60), 399.0: (0.15, 0.20)})
    book.seed("SPY", EXP, chain, ll.wanted_legs(_pcs()), 0.0)
    assert "400.0" not in book.chain("SPY", EXP, ll.wanted_legs(_pcs()))["putExpDateMap"][EXP]
    assert ll.mark(_pcs(), book) is None


def test_a_new_row_on_an_expiration_already_read_is_looked_up_at_once():
    book = _book([_pcs()])
    assert not book.needs_read("SPY", EXP, ll.wanted_legs(_pcs()), 1.0, 60)
    assert book.needs_read("SPY", EXP, ll.wanted_legs(_long_call()), 1.0, 60)
    book.forget_resolution()
    assert book.needs_read("SPY", EXP, ll.wanted_legs(_pcs()), 1.0, 60)


# ── a mark is the hourly mark's arithmetic ──────────────────────────────────

def test_a_credit_row_marks_as_reprice_swing_would():
    hourly = sr.value_swing(_pcs(), _chain(PUTS, CALLS))
    live = ll.mark(_pcs(), _book([_pcs()]))
    assert live["current_value"] == hourly["current_value"]
    assert live["unrealized_pnl"] == hourly["unrealized_pnl"]


def test_a_debit_row_marks_as_reprice_legs_would():
    hourly = sr.value_legs(_long_call(), _chain(PUTS, CALLS))
    live = ll.mark(_long_call(), _book([_long_call()]))
    assert (live["current_value"], live["unrealized_pnl"]) == (
        hourly["current_value"], hourly["unrealized_pnl"])


def test_a_self_managed_row_marks_as_structure_marks_would():
    row = _condor()
    hourly = sm.value_from_chains({**row, "legs_json": json.dumps(row["legs"])},
                                  {EXP: _chain(PUTS, CALLS)})
    live = ll.mark(row, _book([row]))
    assert live["unrealized_pnl"] == hourly["unrealized_pnl"]


def test_a_streamed_quote_moves_the_mark():
    book = _book([_pcs()])
    before = ll.mark(_pcs(), book)["current_value"]
    book.merge({"symbol": _osi("put", 400.0), "bid": 1.50, "ask": 1.60})
    assert ll.mark(_pcs(), book)["current_value"] > before


def test_an_expired_row_has_no_mark():
    row = _pcs(expiration="2001-01-19")
    assert ll.mark(row, _book([_pcs()])) is None


def test_the_page_gets_the_total_not_the_per_contract_pnl():
    rep = {"unrealized_pnl": 30.0, "current_value": 0.70}
    assert ll.display(_pcs(), rep) == {"unrealized_pnl": 60.0, "current_value": 0.70}


# ── confirmation and the held best ──────────────────────────────────────────

def test_an_exit_waits_for_the_confirmation():
    w, rec = ll.ExitWatch(), {"code": "TAKE_PROFIT"}
    assert not w.observe("a", rec, 100.0, 10)
    assert not w.observe("a", rec, 105.0, 10)
    assert w.observe("a", rec, 110.0, 10)


def test_a_hold_or_another_rule_starts_the_confirmation_over():
    w = ll.ExitWatch()
    w.observe("a", {"code": "TAKE_PROFIT"}, 100.0, 10)
    w.observe("a", None, 105.0, 10)
    assert not w.observe("a", {"code": "TAKE_PROFIT"}, 111.0, 10)
    assert not w.observe("a", {"code": "TRAIL_STOP"}, 125.0, 10)
    assert w.observe("a", {"code": "TRAIL_STOP"}, 135.0, 10)


def test_no_confirmation_fires_at_once():
    assert ll.ExitWatch().observe("a", {"code": "X"}, 0.0, 0)


def test_the_best_is_written_on_its_schedule_and_only_when_it_rose():
    p = ll.Peaks()
    p.update({"trade_id": "a", "mfe_pnl": 5.0}, 3.0)
    assert p.due(0.0, 60) == [("a", 5.0)]
    p.wrote("a", 5.0, 0.0)
    p.update({"trade_id": "a"}, 9.0)
    assert p.due(30.0, 60) == []
    assert p.due(61.0, 60) == [("a", 9.0)]
    p.wrote("a", 9.0, 61.0)
    p.update({"trade_id": "a"}, 4.0)
    assert p.best("a") == 9.0 and p.due(500.0, 60) == []


# ── the pass ────────────────────────────────────────────────────────────────

@pytest.fixture
def store(monkeypatch):
    """A Ledger store and a peak table in memory."""
    s = types.SimpleNamespace(rows=[], closed=[], peaks=[])

    def _close(t, exit_debit, reason):
        return {**t, "status": "CLOSED", "exit_debit": exit_debit, "exit_reason": reason}

    def _update(tid, row):
        s.closed.append((tid, row))
        for r in s.rows:
            if r["trade_id"] == tid:
                r.update(row)

    monkeypatch.setitem(sys.modules, "paper_trader", types.SimpleNamespace(
        get_all_trades=lambda: [dict(r) for r in s.rows],
        close_paper_trade=_close, update_trade=_update))

    class _Conn:
        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "trades_db", types.SimpleNamespace(
        connect=lambda *a, **k: _Conn(),
        raise_peak=lambda conn, tid, pnl, at: s.peaks.append((tid, pnl))))
    return s


def _state(connected=True):
    st = ll.State()
    if connected:
        st.connected.set()
    return st


def _fetch(chain):
    return lambda client, symbol, exp: chain


def _run(state, now, cfg=CFG, chain=None, when=MIDDAY, on_close=None):
    return ll.evaluate(state, when, now, cfg, client=object(), on_close=on_close,
                       fetch=_fetch(chain or _chain(PUTS, CALLS)))


def test_a_winning_credit_row_closes_after_the_confirmation(store):
    # Short 400P 0.50x0.60, long 399P 0.15x0.20: about 0.40 to close a 1.00
    # credit, past the +50% target.
    store.rows = [_pcs()]
    st, closes = _state(), []
    marks = _run(st, 0.0, on_close=lambda: closes.append(1))
    assert marks["c1"]["unrealized_pnl"] > 100            # two contracts
    assert store.closed == []
    _run(st, 10.0, on_close=lambda: closes.append(1))
    assert len(store.closed) == 1 and closes == [1]
    tid, row = store.closed[0]
    assert tid == "c1" and row["status"] == "CLOSED"
    assert row["exit_debit"] == ll.mark(_pcs(), st.book)["current_value"]


def test_a_row_closed_in_the_meantime_is_not_closed_again(store):
    store.rows = [_pcs()]
    st = _state()
    _run(st, 0.0)
    store.rows[0]["status"] = "CLOSED"           # by hand, between two marks
    _run(st, 10.0)
    assert store.closed == []


def test_a_self_managed_row_is_marked_but_never_closed(store):
    store.rows = [_condor()]                       # 0.85 to close 0.80: its stop is $10
    st = _state()
    for now in (0.0, 20.0, 40.0):
        marks = _run(st, now)
    assert "k1" in marks and store.closed == []


def test_exits_off_is_display_only(store):
    store.rows = [_pcs()]
    st = _state()
    for now in (0.0, 20.0):
        marks = _run(st, now, cfg={**CFG, "exits": False})
    assert "c1" in marks and store.closed == []


def test_nothing_outside_the_session_with_the_switch_off_or_the_stream_down(store):
    store.rows = [_pcs()]
    assert _run(_state(), 0.0, when=EVENING) == {}
    assert _run(_state(), 0.0, cfg={**CFG, "enabled": False}) == {}
    st = _state(connected=False)
    assert _run(st, 0.0) == {}
    assert st.book.osis()                           # it still resolved the contracts


def test_a_chain_that_cannot_be_read_is_no_mark_and_is_retried_later(store):
    store.rows = [_pcs()]
    st, calls = _state(), []

    def fetch(client, symbol, exp):
        calls.append(exp)
        return None

    ll.evaluate(st, MIDDAY, 0.0, CFG, client=object(), fetch=fetch)
    ll.evaluate(st, MIDDAY, 30.0, CFG, client=object(), fetch=fetch)
    assert len(calls) == 1
    assert ll.evaluate(st, MIDDAY, 61.0, CFG, client=object(), fetch=fetch) == {}
    assert len(calls) == 2


def test_a_held_best_reaches_the_trail_and_the_store(store):
    # A best of $90 a contract seen earlier on the stream, never stored yet: the
    # first pass writes it, so a restart does not lose what the trail has seen.
    store.rows = [_pcs()]
    st = _state()
    st.peaks.update({"trade_id": "c1"}, 90.0)      # seen earlier on this stream
    _run(st, 0.0)
    assert ("c1", 90.0) in store.peaks
