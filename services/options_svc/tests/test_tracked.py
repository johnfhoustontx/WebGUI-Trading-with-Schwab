"""``services/options_svc/tracked`` - the tracked structures' manage loop and view.

Driven end to end against a real per-test ``signals.db``: rows go in through the
real recorder, are marked through the real ``structure_marks`` against a fake
client's chains, and are read back through the store's own readers. What is
pinned is the lifecycle - mark, target, stop, expiry, the calendar's own close -
and that none of it reaches a credit-spread reader or a paper book.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute  # noqa: F401 - puts options-scanner on sys.path
from services.options_svc import tracked

import signal_db  # noqa: E402
import signal_recorder  # noqa: E402
import signal_repricer  # noqa: E402

CT = ZoneInfo("America/Chicago")
FRONT, BACK = "2026-10-16", "2026-11-13"
OPENED = dt.datetime(2026, 10, 7, 10, 0, tzinfo=CT)             # a Wednesday


def _at(day, hour, minute=10):
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=CT)


def _leg(kind, side, strike, exp=FRONT, qty=1):
    return {"kind": kind, "side": side, "strike": strike, "expiration": exp,
            "qty": qty, "mark": 1.0}


def _sig(structure, legs, symbol="SPY", **over):
    s = {"symbol": symbol, "type": structure, "group": "X", "expiration": FRONT,
         "dte": 9, "legs": legs, "net_debit": None, "net_credit": None,
         "max_profit": None, "max_loss": 500.0, "capital": 500.0,
         "unbounded_loss": False, "composite_score": 60.0, "grade": "Good",
         "iv_rank": 40.0, "underlying_price": 500.0, "net_delta": 0.0,
         "net_theta": 0.0}
    s.update(over)
    return s


STRADDLE = _sig("LONG_STRADDLE",
                [_leg("call", "long", 500.0), _leg("put", "long", 500.0)],
                net_debit=540.0, max_loss=542.6)
STRANGLE = _sig("SHORT_STRANGLE",
                [_leg("call", "short", 510.0), _leg("put", "short", 490.0)],
                net_credit=135.0, max_profit=132.4, max_loss=2002.6,
                unbounded_loss=True, symbol="QQQ")
CALENDAR = _sig("CALENDAR_PUT",
                [_leg("put", "short", 500.0, FRONT), _leg("put", "long", 500.0, BACK)],
                net_debit=380.0, max_profit=150.0, max_loss=382.6, symbol="IWM")


class _Resp:
    def __init__(self, payload):
        self._payload, self.status_code = payload, 200 if payload is not None else 500

    def json(self):
        return self._payload


class _Client:
    class Options:
        class ContractType:
            ALL = "ALL"

    def __init__(self, quotes):
        # quotes: {(symbol, expiration): {"call"|"put": {strike: (bid, ask)}}}
        self.quotes, self.calls = quotes, []

    def get_option_chain(self, symbol, from_date=None, to_date=None, **kw):
        exp = from_date.isoformat()
        self.calls.append((symbol, exp))
        book = self.quotes.get((symbol, exp))
        if book is None:
            return _Resp(None)

        def side(kind):
            return {f"{exp}:9": {f"{k}": [{"bid": b, "ask": a, "delta": 0.5}]
                                 for k, (b, a) in (book.get(kind) or {}).items()}}
        return _Resp({"underlyingPrice": 501.0, "callExpDateMap": side("call"),
                      "putExpDateMap": side("put")})


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "signals.db"
    monkeypatch.setattr(signal_recorder._scfg, "capture_max_open_per_symbol_tracked",
                        lambda: 0)
    assert signal_recorder.record_tracked([STRADDLE, STRANGLE, CALENDAR],
                                          "SWING_STRUCT", db_path=path, now=OPENED) == 3
    signal_repricer.clear_chain_cache()
    yield path
    signal_repricer.clear_chain_cache()


def _open(db):
    return {r["strategy"]: r for r in
            signal_db.get_open_signals_with_latest_mark(db_path=db, tracked=True)}


def _outcomes(db):
    return {r["strategy"]: r for r in signal_db.get_tracked_outcomes(db_path=db)}


QUIET = {("SPY", FRONT): {"call": {500.0: (2.6, 2.8)}, "put": {500.0: (2.6, 2.8)}},
         ("QQQ", FRONT): {"call": {510.0: (0.6, 0.8)}, "put": {490.0: (0.6, 0.8)}},
         ("IWM", FRONT): {"put": {500.0: (3.0, 3.2)}},
         ("IWM", BACK): {"put": {500.0: (6.8, 7.0)}}}


# ── marking ─────────────────────────────────────────────────────────────────

def test_a_cycle_marks_every_open_row_and_closes_nothing_at_rest(db):
    res = tracked.manage_cycle(_at(7, 10, 25), client=_Client(QUIET), db_path=db)
    assert res == {"closed": [], "marked": 3, "deferred": 0}
    rows = _open(db)
    assert rows["LONG_STRADDLE"]["unrealized_pnl"] == pytest.approx(0.0)
    assert rows["SHORT_STRANGLE"]["unrealized_pnl"] == pytest.approx(-5.0)
    assert rows["CALENDAR_PUT"]["unrealized_pnl"] == pytest.approx(0.0)
    assert all(r["recommendation"] == "HOLD" for r in rows.values())


def test_a_calendar_fetches_both_of_its_expirations(db):
    client = _Client(QUIET)
    tracked.manage_cycle(_at(7, 10, 25), client=client, db_path=db)
    assert ("IWM", FRONT) in client.calls and ("IWM", BACK) in client.calls


def test_a_row_with_no_quote_is_left_open_and_unmarked(db):
    quotes = dict(QUIET)
    quotes[("SPY", FRONT)] = {"call": {500.0: (2.6, 2.8)}}            # no put
    res = tracked.manage_cycle(_at(7, 10, 25), client=_Client(quotes), db_path=db)
    assert res["marked"] == 2 and res["closed"] == []
    assert _open(db)["LONG_STRADDLE"]["unrealized_pnl"] is None


def test_nothing_is_marked_outside_the_regular_session(db):
    client = _Client(QUIET)
    for when in (_at(7, 8, 10), _at(7, 15, 10)):
        assert tracked.manage_cycle(when, client=client, db_path=db) == {
            "closed": [], "marked": 0, "deferred": 0}
    assert client.calls == []


def test_no_open_rows_costs_no_fetch(tmp_path):
    client = _Client(QUIET)
    path = tmp_path / "empty.db"
    signal_db.init_db(path)
    assert tracked.manage_cycle(_at(7, 10, 25), client=client, db_path=path) == {
        "closed": [], "marked": 0, "deferred": 0}
    assert client.calls == []


# ── exits on the mark ───────────────────────────────────────────────────────

def test_a_straddle_that_reaches_half_its_debit_is_closed_at_its_mark(db):
    quotes = dict(QUIET)
    quotes[("SPY", FRONT)] = {"call": {500.0: (8.0, 8.2)}, "put": {500.0: (0.0, 0.05)}}
    res = tracked.manage_cycle(_at(8, 10, 25), client=_Client(quotes), db_path=db)
    closed = {c["strategy"]: c for c in res["closed"]}
    assert closed["LONG_STRADDLE"]["reason"] == "TARGET_HIT"
    out = _outcomes(db)["LONG_STRADDLE"]
    assert out["exit_reason"] == "TARGET_HIT"
    assert out["realized_pnl"] == pytest.approx((-5.40 + 8.125) * 100)
    assert "LONG_STRADDLE" not in _open(db)


def test_a_short_strangle_is_stopped_at_twice_its_credit(db):
    quotes = dict(QUIET)
    quotes[("QQQ", FRONT)] = {"call": {510.0: (4.0, 4.2)}, "put": {490.0: (0.0, 0.05)}}
    res = tracked.manage_cycle(_at(8, 10, 25), client=_Client(quotes), db_path=db)
    assert {c["strategy"]: c["reason"] for c in res["closed"]} == {
        "SHORT_STRANGLE": "MONEY_STOP"}
    out = _outcomes(db)["SHORT_STRANGLE"]
    assert out["realized_pnl"] == pytest.approx((1.35 - 4.125) * 100)
    assert out["unbounded"] == 1


def test_a_closed_row_is_not_marked_again(db):
    quotes = dict(QUIET)
    quotes[("SPY", FRONT)] = {"call": {500.0: (8.0, 8.2)}, "put": {500.0: (0.0, 0.05)}}
    tracked.manage_cycle(_at(8, 10, 25), client=_Client(quotes), db_path=db)
    again = tracked.manage_cycle(_at(8, 10, 40), client=_Client(quotes), db_path=db)
    assert again["closed"] == [] and again["marked"] == 2


# ── the calendar's own close ────────────────────────────────────────────────

def test_a_calendar_is_closed_on_its_mark_on_the_front_expiry_day(db):
    before = tracked.manage_cycle(_at(16, 13, 55), client=_Client(QUIET), db_path=db)
    assert "CALENDAR_PUT" not in {c["strategy"] for c in before["closed"]}
    signal_repricer.clear_chain_cache()
    res = tracked.manage_cycle(_at(16, 14, 10), client=_Client(QUIET), db_path=db)
    closed = {c["strategy"]: c for c in res["closed"]}
    assert closed["CALENDAR_PUT"]["reason"] == "FRONT_EXPIRY"
    out = _outcomes(db)["CALENDAR_PUT"]
    # short front 3.10 bought back, long back 6.90 sold: worth 3.80, paid 3.80.
    assert out["realized_pnl"] == pytest.approx(0.0)
    assert out["exit_value"] == pytest.approx(-3.80)


def test_the_close_hour_is_read_from_the_config(db, monkeypatch):
    monkeypatch.setattr(tracked._trade_mgmt, "tracked", lambda: {
        "mark_interval_min": 15, "mark_offset_min": 10,
        "front_expiry_close": dt.time(11, 0)})
    res = tracked.manage_cycle(_at(16, 11, 10), client=_Client(QUIET), db_path=db)
    assert "FRONT_EXPIRY" in {c["reason"] for c in res["closed"]}


def test_a_calendar_that_was_never_marked_is_closed_with_no_outcome(db, monkeypatch):
    """After the settlement hour its front leg no longer quotes, and intrinsic
    is not what a position with a live back month is worth."""
    monkeypatch.setattr(tracked, "_proxy", None, raising=False)
    import paper_engine
    monkeypatch.setattr(paper_engine, "settlement_underlying", lambda *a, **k: 500.0)
    res = tracked.manage_cycle(_at(16, 15, 10), client=_Client({}), db_path=db)
    reasons = {c["strategy"]: c["reason"] for c in res["closed"]}
    assert reasons["CALENDAR_PUT"] == "UNMARKABLE"
    out = _outcomes(db)["CALENDAR_PUT"]
    assert out["realized_pnl"] is None and out["exit_value"] is None


# ── settlement ──────────────────────────────────────────────────────────────

def test_single_expiry_rows_settle_at_intrinsic_against_the_shared_rule(db, monkeypatch):
    import paper_engine
    asked = []

    def _settle(client, symbol, expiration, today, close_fn=None):
        asked.append((symbol, expiration))
        return {"SPY": 512.0, "QQQ": 500.0}.get(symbol)

    monkeypatch.setattr(paper_engine, "settlement_underlying", _settle)
    client = _Client({})
    res = tracked.manage_cycle(_at(16, 15, 10), client=client, db_path=db)
    assert client.calls == []                                # no option quote read
    assert ("SPY", FRONT) in asked and ("QQQ", FRONT) in asked
    out = _outcomes(db)
    # Straddle at 500 with the stock at 512: worth 12, paid 5.40.
    assert out["LONG_STRADDLE"]["exit_reason"] == "EXPIRED"
    assert out["LONG_STRADDLE"]["realized_pnl"] == pytest.approx(660.0)
    # Short strangle 490/510 at 500: both worthless, the whole credit kept.
    assert out["SHORT_STRANGLE"]["realized_pnl"] == pytest.approx(135.0)
    assert {c["reason"] for c in res["closed"]} == {"EXPIRED", "UNMARKABLE"}
    conn_row = signal_db.connect(db).execute(
        "SELECT settlement_underlying FROM signal_outcomes o JOIN signals s "
        "USING (signal_id) WHERE s.strategy = 'LONG_STRADDLE'").fetchone()
    assert conn_row[0] == 512.0


def test_no_settlement_price_defers_and_never_falls_back_to_a_mark(db, monkeypatch):
    import paper_engine
    monkeypatch.setattr(paper_engine, "settlement_underlying", lambda *a, **k: None)
    res = tracked.manage_cycle(_at(16, 15, 10), client=_Client(QUIET), db_path=db)
    assert res["deferred"] == 2
    assert {"LONG_STRADDLE", "SHORT_STRANGLE"} <= set(_open(db))


def test_before_the_close_on_expiry_day_a_row_is_still_marked_not_settled(db, monkeypatch):
    import paper_engine
    monkeypatch.setattr(paper_engine, "settlement_underlying",
                        lambda *a, **k: pytest.fail("settled before the close"))
    res = tracked.manage_cycle(_at(16, 10, 25), client=_Client(QUIET), db_path=db)
    assert res["marked"] == 3 and res["deferred"] == 0


# ── isolation ───────────────────────────────────────────────────────────────

def test_one_row_that_raises_costs_only_that_row(db, monkeypatch):
    import structure_marks as sm
    real = sm.reprice

    def _one_bad(row, client, today=None):
        if row["symbol"] == "SPY":
            raise RuntimeError("boom")
        return real(row, client, today=today)

    monkeypatch.setattr(sm, "reprice", _one_bad)
    res = tracked.manage_cycle(_at(7, 10, 25), client=_Client(QUIET), db_path=db)
    assert res["marked"] == 2


def test_an_unreadable_store_is_an_empty_cycle_not_a_raise(monkeypatch):
    monkeypatch.setattr(signal_db, "get_open_signals_with_latest_mark",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("locked")))
    assert tracked.manage_cycle(_at(7, 10, 25), client=_Client({})) == {
        "closed": [], "marked": 0, "deferred": 0}


def test_nothing_the_cycle_does_is_visible_to_a_credit_spread_reader(db):
    quotes = dict(QUIET)
    quotes[("SPY", FRONT)] = {"call": {500.0: (8.0, 8.2)}, "put": {500.0: (0.0, 0.05)}}
    tracked.manage_cycle(_at(8, 10, 25), client=_Client(quotes), db_path=db)
    assert signal_db.get_open_signals_with_latest_mark(db_path=db) == []
    assert signal_db.get_open_signals(db_path=db) == []
    assert signal_db.get_outcomes_for_date("2026-10-08", db_path=db) == []
    assert signal_db.get_outcomes_in_range("2026-10-01", "2026-10-31", db_path=db) == []
    assert signal_db.count_open_by_symbol(db_path=db) == {}


# ── the view ────────────────────────────────────────────────────────────────

def test_the_view_carries_open_rows_with_their_legs_and_last_mark(db):
    tracked.manage_cycle(_at(7, 10, 25), client=_Client(QUIET), db_path=db)
    v = tracked.view(_at(7, 10, 30), db_path=db)
    assert v["date"] == "2026-10-07"
    assert v["counts"] == {"open": 3, "closed": 0, "closed_today": 0}
    row = next(r for r in v["open"] if r["strategy"] == "CALENDAR_PUT")
    assert [l["expiration"] for l in row["legs"]] == [FRONT, BACK]
    assert row["entry_credit"] == pytest.approx(-3.80)
    assert row["unrealized_pnl"] == pytest.approx(0.0)
    assert "legs_json" not in row and "dedup_key" not in row
    assert v["stats"] == [] and v["closed_today"] == []


def test_the_view_reports_results_by_structure(db, monkeypatch):
    import paper_engine
    monkeypatch.setattr(paper_engine, "settlement_underlying",
                        lambda c, symbol, *a, **k: {"SPY": 512.0, "QQQ": 500.0}.get(symbol))
    tracked.manage_cycle(_at(16, 15, 10), client=_Client({}), db_path=db)
    v = tracked.view(_at(16, 15, 15), db_path=db)
    by = {s["strategy"]: s for s in v["stats"]}
    assert by["LONG_STRADDLE"]["n"] == 1 and by["LONG_STRADDLE"]["wins"] == 1
    assert by["LONG_STRADDLE"]["total_pnl"] == pytest.approx(660.0)
    assert by["LONG_STRADDLE"]["avg_r"] == pytest.approx(660.0 / 542.6, abs=1e-3)
    assert by["LONG_STRADDLE"]["win_pct"] == 100.0
    assert by["SHORT_STRANGLE"]["unbounded"] is True
    # The calendar has no outcome: counted as unmarkable, never as a trade.
    assert by["CALENDAR_PUT"]["n"] == 0 and by["CALENDAR_PUT"]["unmarkable"] == 1
    assert by["CALENDAR_PUT"]["win_pct"] is None and by["CALENDAR_PUT"]["avg_r"] is None
    assert v["counts"] == {"open": 0, "closed": 3, "closed_today": 3}
    assert len(v["closed_today"]) == 3


def test_stats_are_pure_and_tolerate_junk():
    rows = [{"strategy": "A", "realized_pnl": 100.0, "entry_max_loss": 2.0},
            {"strategy": "A", "realized_pnl": -50.0, "entry_max_loss": 2.0},
            {"strategy": "A", "realized_pnl": float("nan"), "entry_max_loss": 2.0},
            {"strategy": "B", "realized_pnl": 10.0, "entry_max_loss": 0},
            {"strategy": None, "realized_pnl": 10.0}, None, "junk"]
    out = {s["strategy"]: s for s in tracked.stats(rows)}
    assert out["A"]["n"] == 2 and out["A"]["wins"] == 1 and out["A"]["unmarkable"] == 1
    assert out["A"]["total_pnl"] == 50.0 and out["A"]["win_pct"] == 50.0
    assert out["A"]["avg_r"] == pytest.approx((0.5 - 0.25) / 2)
    assert out["B"]["avg_r"] is None             # no risk figure: no R, not a zero
    assert [s["strategy"] for s in tracked.stats(rows)] == ["A", "B"]
    assert tracked.stats(None) == []


def test_an_unreadable_store_is_an_empty_view(monkeypatch):
    monkeypatch.setattr(signal_db, "get_open_signals_with_latest_mark",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("locked")))
    v = tracked.view(_at(7, 10, 30))
    assert v["open"] == [] and v["stats"] == [] and v["counts"]["open"] == 0


# ── publishing and the schedule ─────────────────────────────────────────────

def test_the_view_is_published_and_an_unchanged_one_is_not_republished(monkeypatch):
    from services.options_svc import handlers
    from shared.bus import Bus
    bus = Bus(fake=True)
    payload = {"date": "2026-10-07", "open": [{"signal_id": "a"}], "closed_today": [],
               "stats": [], "counts": {"open": 1, "closed": 0, "closed_today": 0}}
    monkeypatch.setattr(handlers.tracked, "view", lambda: dict(payload))
    handlers.publish_tracked(bus)
    env = bus.cache_get("cache:options:tracked")
    assert env.payload == payload
    handlers.publish_tracked(bus)
    assert bus.cache_get("cache:options:tracked").version == env.version


def test_the_tick_runs_the_cycle_then_publishes_and_never_pushes(monkeypatch):
    from services.options_svc import handlers
    from shared.bus import Bus
    bus, order = Bus(fake=True), []
    monkeypatch.setattr(handlers.tracked, "manage_cycle", lambda: order.append(
        "cycle") or {"closed": [{"symbol": "SPY", "reason": "TARGET_HIT"}]})
    monkeypatch.setattr(handlers.tracked, "view", lambda: order.append("view") or {
        "open": [], "stats": []})
    monkeypatch.setattr(handlers.push_notify, "notify_signals",
                        lambda *a, **k: pytest.fail("a tracked row was pushed"))
    handlers.run_tracked_manage_and_publish(bus)
    assert order == ["cycle", "view"]
    assert bus.cache_get("cache:options:tracked") is not None


def test_reloading_the_captured_page_republishes_the_tracked_view(monkeypatch):
    from services.options_svc import handlers
    from shared.bus import Bus
    from shared.contracts.envelope import Command
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers, "refresh_captured", lambda b: None)
    monkeypatch.setattr(handlers.tracked, "view", lambda: {"open": [], "stats": []})
    handlers.handle_command(bus, Command(type="captured_reload"))
    assert bus.cache_get("cache:options:tracked").payload == {"open": [], "stats": []}


def test_the_tracked_slot_fires_once_off_the_quarter_hour():
    from services.options_svc import scheduler
    due, slot = scheduler.tracked_manage_due(_at(7, 10, 10), None)
    assert due is True
    assert scheduler.tracked_manage_due(_at(7, 10, 12), slot) == (False, slot)
    nxt, slot2 = scheduler.tracked_manage_due(_at(7, 10, 25), slot)
    assert nxt is True and slot2 != slot
    # On the quarter hour, where the autoscan fetches, it never fires.
    for minute in (0, 2, 5, 15, 17, 30, 45):
        assert scheduler.tracked_manage_due(_at(7, 10, minute), None)[0] is False


def test_a_start_mid_slot_waits_for_the_next_slot():
    from services.options_svc import scheduler
    assert scheduler.tracked_manage_due(_at(7, 10, 18), None)[0] is False
    assert scheduler.tracked_manage_due(_at(7, 10, 25), None)[0] is True


def test_the_last_slot_of_the_day_is_after_the_settlement_hour():
    """15:10 CT is inside the scan window and past 15:00, so expired rows settle
    the same afternoon rather than the next morning."""
    from services.options_svc import scheduler
    assert scheduler.tracked_manage_due(_at(7, 15, 10), None)[0] is True
    assert scheduler.tracked_manage_due(_at(7, 15, 25), None)[0] is False


def test_no_tracked_slot_outside_a_trading_session():
    from services.options_svc import scheduler
    assert scheduler.tracked_manage_due(_at(10, 10, 10), None)[0] is False   # Saturday
    assert scheduler.tracked_manage_due(_at(7, 7, 10), None)[0] is False     # before 08:00
    assert scheduler.tracked_manage_due(_at(7, 0, 5), None)[0] is False


def test_the_cadence_is_read_from_the_config(monkeypatch):
    from services.options_svc import scheduler
    monkeypatch.setattr(scheduler._trade_mgmt, "tracked", lambda: {
        "mark_interval_min": 30, "mark_offset_min": 20,
        "front_expiry_close": dt.time(14, 0)})
    assert scheduler.tracked_manage_due(_at(7, 10, 20), None)[0] is True
    assert scheduler.tracked_manage_due(_at(7, 10, 50), None)[0] is True
    assert scheduler.tracked_manage_due(_at(7, 10, 10), None)[0] is False
    assert scheduler.tracked_manage_due(_at(7, 10, 35), None)[0] is False
