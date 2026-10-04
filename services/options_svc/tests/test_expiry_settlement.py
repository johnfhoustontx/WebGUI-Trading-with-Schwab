"""Expiry settlement in the service: captured signals, the Ledger, the 15:05 slot.

Audit AC-01 and AC-02 (2026-10-03). The engine half — WHICH price a position
settles against — is ``paper_engine.settlement_underlying``, tested in
``options-scanner/tests/test_expiry_settlement.py``. These tests drive the three
service entry points through the REAL ``paper_engine`` and the REAL
``signal_repricer.expiry_value``; only the stores and the proxy client are fakes.

Every expected figure is worked by hand from the payoff.
"""
import datetime as dt
import sys
import types
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute, handlers, scheduler

_CT = ZoneInfo("America/Chicago")

# 2026-06-05 is a Friday, 2026-06-08 the Monday after it. Neither is a holiday.
FRIDAY, MONDAY = "2026-06-05", "2026-06-08"


def _at(day, hour, minute=0):
    y, m, d = (int(p) for p in day.split("-"))
    return dt.datetime(y, m, d, hour, minute, tzinfo=_CT)


def _candle(day, close):
    y, m, d = (int(p) for p in day.split("-"))
    ms = int(dt.datetime(y, m, d, tzinfo=_CT).timestamp() * 1000)
    return {"datetime": ms, "open": close, "high": close, "low": close,
            "close": close, "volume": 1}


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _Client:
    """A quote that says one thing and a daily history that says another."""

    def __init__(self, last=None, candles=()):
        self.last, self.candles = last, list(candles)
        self.quote_calls, self.history_calls = [], []

    def get_quotes(self, syms):
        self.quote_calls.append(list(syms))
        return _Resp({syms[0]: {"quote": {"lastPrice": self.last}}})

    def get_price_history_every_day(self, symbol):
        self.history_calls.append(symbol)
        return _Resp({"candles": self.candles})


@pytest.fixture
def client(monkeypatch):
    c = _Client()
    monkeypatch.setattr(compute._proxy, "schwab_py_client", c)
    return c


# ── captured signals ─────────────────────────────────────────────────────────

def _signal(**over):
    base = {"signal_id": "M1", "symbol": "SPY", "strategy": "PCS",
            "short_strike": 490.0, "long_strike": 485.0, "call_short": None,
            "call_long": None, "width": 5.0, "expiration": FRIDAY,
            "entry_credit": 1.0, "entry_short_delta": -0.10, "entry_score": 60,
            "entry_underlying": 500.0, "be_armed": 0}
    base.update(over)
    return base


def _rep(**over):
    base = {"current_value": 0.40, "unrealized_pnl": 60.0, "pnl_pct_of_credit": 60.0,
            "current_underlying": 500.0, "current_short_delta": -0.10, "error": None}
    base.update(over)
    return base


@pytest.fixture
def captured(monkeypatch):
    """Stub the signal STORE and the chain reprice; everything that decides
    when and at what price a signal settles stays real."""
    import signal_repricer
    calls = {"closed": [], "marks": [], "armed": [], "reprices": 0}
    state = {"signals": [], "rep": _rep()}

    def _close(sid, exit_value, exit_reason, **kw):
        calls["closed"].append({"signal_id": sid, "exit_value": exit_value,
                                "reason": exit_reason, **kw})

    def _reprice(row, client):
        calls["reprices"] += 1
        return state["rep"]

    monkeypatch.setitem(sys.modules, "signal_db", types.SimpleNamespace(
        get_open_signals_with_latest_mark=lambda: state["signals"],
        set_be_armed=lambda sid, **kw: calls["armed"].append(sid),
        close_signal_manually=_close,
        insert_marks=lambda marks, **kw: calls["marks"].extend(marks),
        peak_unrealized=lambda sid: None))
    monkeypatch.setattr(signal_repricer, "reprice_swing", _reprice)
    monkeypatch.setattr(signal_repricer, "clear_chain_cache", lambda: None)
    calls["state"] = state
    return calls


def test_a_captured_signal_is_not_expired_on_its_expiry_morning(captured, client):
    """The defect: ``dte <= 0`` closed every signal as EXPIRED at the first
    cycle of its expiry day, at whatever the option was marked that morning."""
    captured["state"]["signals"] = [_signal()]
    client.last = 500.0
    out = compute.run_captured_manage_cycle(now_ct=_at(FRIDAY, 9, 5))
    assert captured["closed"] == []
    assert out["closed"] == []
    assert len(captured["marks"]) == 1          # still marked and rule-managed


def test_a_captured_signal_settles_at_intrinsic_after_the_close(captured, client):
    # 490/485 put spread, SPY closes 487: the short put is worth 3.00, the long
    # nothing. The morning's 0.40 mark must not be what it closes at.
    captured["state"]["signals"] = [_signal()]
    client.last = 487.0
    out = compute.run_captured_manage_cycle(now_ct=_at(FRIDAY, 15, 5))
    assert captured["closed"] == [{"signal_id": "M1", "exit_value": 3.0,
                                   "reason": "EXPIRED",
                                   "settlement_underlying": 487.0}]
    assert out["closed"][0]["reason"] == "EXPIRED"
    assert out["closed"][0]["exit_val"] == 3.0


def test_a_settlement_does_not_need_a_chain_reprice(captured, client):
    captured["state"]["signals"] = [_signal()]
    client.last = 500.0
    compute.run_captured_manage_cycle(now_ct=_at(FRIDAY, 15, 5))
    assert captured["reprices"] == 0
    assert captured["closed"][0]["exit_value"] == 0.0     # out of the money


def test_a_missed_expiry_settles_at_that_dates_close_not_mondays_quote(captured, client):
    captured["state"]["signals"] = [_signal()]
    client.last = 470.0                                   # Monday: deep in the money
    client.candles = [_candle(FRIDAY, 487.0), _candle(MONDAY, 470.0)]
    compute.run_captured_manage_cycle(now_ct=_at(MONDAY, 9, 5))
    assert captured["closed"] == [{"signal_id": "M1", "exit_value": 3.0,
                                   "reason": "EXPIRED",
                                   "settlement_underlying": 487.0}]
    assert client.quote_calls == []


def test_no_settlement_price_defers_and_never_uses_the_entry_price(captured, client):
    """The old fallback settled against ``entry_underlying`` when nothing else
    was to hand - 500 here, which books a full win on a spread that may have
    finished through its strikes."""
    captured["state"]["signals"] = [_signal()]
    client.last = None
    out = compute.run_captured_manage_cycle(now_ct=_at(FRIDAY, 15, 5))
    assert captured["closed"] == []
    assert out["closed"] == []


def test_an_assigned_short_put_is_not_booked_as_a_full_win(captured, client):
    # Short the 95 put for 1.00; the stock closes 90. The put is worth 5.00.
    captured["state"]["signals"] = [_signal(
        strategy="SHORT_PUT", short_strike=95.0, long_strike=None, width=None)]
    client.last = 90.0
    compute.run_captured_manage_cycle(now_ct=_at(FRIDAY, 15, 5))
    assert captured["closed"][0]["exit_value"] == 5.0


def test_a_structure_that_cannot_be_valued_is_left_open(captured, client):
    captured["state"]["signals"] = [_signal(strategy="LONG_CALL")]
    client.last = 500.0
    compute.run_captured_manage_cycle(now_ct=_at(FRIDAY, 15, 5))
    assert captured["closed"] == []


def test_a_signal_that_cannot_settle_does_not_stop_the_next(captured, client):
    captured["state"]["signals"] = [
        _signal(signal_id="BAD", symbol=None),       # no symbol: no price, deferred
        _signal(signal_id="GOOD")]
    client.last = 500.0
    compute.run_captured_manage_cycle(now_ct=_at(FRIDAY, 15, 5))
    assert [c["signal_id"] for c in captured["closed"]] == ["GOOD"]


# ── the Ledger ───────────────────────────────────────────────────────────────

@pytest.fixture
def ledger(monkeypatch):
    import paper_trader
    store = {"trades": [], "updated": {}}
    monkeypatch.setattr(paper_trader, "get_all_trades", lambda: store["trades"])
    monkeypatch.setattr(paper_trader, "update_trade",
                        lambda tid, row: store["updated"].__setitem__(tid, row))
    return store


def _ledger_trade(**over):
    base = {"trade_id": "T1", "status": "OPEN", "symbol": "SPY", "strategy": "PCS",
            "direction": "CREDIT", "expiration": FRIDAY, "short_strike": 500.0,
            "long_strike": 495.0, "entry_credit": 1.0, "quantity": 1}
    base.update(over)
    return base


def test_ledger_settles_a_missed_expiry_at_that_dates_close(ledger, client):
    """The audit's reproduction on the Ledger: +$100 at Friday's 505 close,
    against a -$400 max loss if Monday's 480 is used."""
    ledger["trades"] = [_ledger_trade()]
    client.last = 480.0
    client.candles = [_candle(FRIDAY, 505.0)]
    assert compute.expire_ledger_trades(now_ct=_at(MONDAY, 9, 0)) == 1
    assert ledger["updated"]["T1"]["realized_pnl"] == 100.0
    assert ledger["updated"]["T1"]["status"] == "EXPIRED"
    assert client.quote_calls == []


def test_ledger_defers_a_missed_expiry_with_no_close_for_that_date(ledger, client):
    ledger["trades"] = [_ledger_trade()]
    client.last = 480.0
    client.candles = []
    assert compute.expire_ledger_trades(now_ct=_at(MONDAY, 9, 0)) == 0
    assert ledger["updated"] == {}


def test_ledger_settles_todays_expiry_against_the_quote_after_the_close(ledger, client):
    ledger["trades"] = [_ledger_trade()]
    client.last = 497.0                      # short 500 put worth 3.00
    assert compute.expire_ledger_trades(now_ct=_at(FRIDAY, 15, 5)) == 1
    assert ledger["updated"]["T1"]["realized_pnl"] == -200.0


def test_ledger_holds_todays_expiry_before_the_close(ledger, client):
    ledger["trades"] = [_ledger_trade()]
    client.last = 497.0
    assert compute.expire_ledger_trades(now_ct=_at(FRIDAY, 14, 59)) == 0
    assert ledger["updated"] == {}


# ── the Account's settle-only pass ───────────────────────────────────────────

def test_run_settle_cycle_hands_the_engine_todays_date_and_the_proxy_client(monkeypatch, client):
    import paper_engine
    seen = {}

    def _fake(c, now_date, **kw):
        seen.update(client=c, now_date=now_date, **kw)
        return 2

    monkeypatch.setattr(paper_engine, "run_settle_cycle", _fake)
    assert compute.run_settle_cycle(now_ct=_at(FRIDAY, 15, 5)) == 2
    assert seen["client"] is client
    assert seen["now_date"] == FRIDAY
    assert seen["now_ct"] == _at(FRIDAY, 15, 5)


# ── the 15:05 CT slot ────────────────────────────────────────────────────────

def test_the_settle_slot_is_after_the_close():
    """A slot at or before 15:00 CT would fire before ``should_settle`` allows
    anything to settle, and never again that day."""
    for name, (h, m) in scheduler._SETTLE_SLOTS.items():
        assert (h, m) > (15, 0), name


def test_settle_slot_fires_once_after_the_close():
    ran = set()
    assert scheduler.paper_settle_due(_at(FRIDAY, 15, 0), ran) is None
    slot = scheduler.paper_settle_due(_at(FRIDAY, 15, 5), ran)
    assert slot is not None
    ran.add((FRIDAY, slot))
    assert scheduler.paper_settle_due(_at(FRIDAY, 15, 6), ran) is None


def test_settle_slot_still_fires_for_a_service_that_starts_late():
    assert scheduler.paper_settle_due(_at(FRIDAY, 15, 40), set()) is not None


def test_settle_slot_does_not_fire_on_a_weekend():
    assert scheduler.paper_settle_due(_at("2026-06-06", 15, 5), set()) is None


def test_the_hourly_cycle_still_has_no_run_at_the_close():
    # Unchanged on purpose: entries and exit rules must not run on after-close
    # option quotes. Settlement has its own pass.
    assert scheduler.paper_cycle_due(_at(FRIDAY, 15, 0), set()) is None


def test_settle_handler_settles_both_books_and_republishes(monkeypatch):
    order = []
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_settle_cycle",
                        lambda: order.append("account") or 1)
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades",
                        lambda: order.append("ledger") or 1)
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda bus: order.append("pub_account"))
    monkeypatch.setattr(handlers, "refresh_paper_trades",
                        lambda bus, reprice=True: order.append(("pub_ledger", reprice)))
    monkeypatch.setattr(handlers, "publish_rescue_summary",
                        lambda bus: order.append("pub_rescue"))
    handlers.run_paper_settle(object())
    assert order == ["account", "ledger", "pub_account", ("pub_ledger", False),
                     "pub_rescue"]


def test_settle_handler_runs_no_entry_and_no_exit_rule(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("the settle pass must not open, reprice or rule-close")

    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_settle_cycle", lambda: 0)
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades", lambda: 0)
    monkeypatch.setattr(handlers.compute, "run_entry_cycle", _boom)
    monkeypatch.setattr(handlers.compute, "run_manage_cycle", _boom)
    monkeypatch.setattr(handlers.compute, "manage_ledger_trades", _boom)
    monkeypatch.setattr(handlers, "refresh_paper_account", lambda bus: None)
    monkeypatch.setattr(handlers, "refresh_paper_trades", lambda bus, reprice=True: None)
    monkeypatch.setattr(handlers, "publish_rescue_summary", lambda bus: None)
    handlers.run_paper_settle(object())


def test_a_ledger_failure_does_not_stop_the_republish(monkeypatch):
    seen = []

    def _boom():
        raise RuntimeError("ledger store locked")

    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_settle_cycle", lambda: 0)
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades", _boom)
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda bus: seen.append("account"))
    monkeypatch.setattr(handlers, "refresh_paper_trades",
                        lambda bus, reprice=True: seen.append("ledger"))
    monkeypatch.setattr(handlers, "publish_rescue_summary", lambda bus: None)
    handlers.run_paper_settle(object())
    assert seen == ["account", "ledger"]
