"""The five money-path views are validated before they are published (AR-08).

A payload that does not validate is NOT published: the last good view stays,
and the fault is counted as a degrade. A valid payload is published exactly as
it was built, with every key it had.
"""
import pytest

from services import _degrade
from services.options_svc import handlers
from shared.bus import Bus


@pytest.fixture(autouse=True)
def _fresh_degrades():
    _degrade.reset()
    yield
    _degrade.reset()


def _payload(bus, key):
    env = bus.cache_get(key)
    return env.payload if env is not None else None


# ---- the paper account -------------------------------------------------------

GOOD_ACCOUNT = {"snapshot": {"equity": 24184.2}, "positions": [{"position_id": 1}],
                "orders": [], "lots": [], "has_account": True, "perf": None,
                "greeks": None, "custom": 7}


def _account(monkeypatch, view):
    monkeypatch.setattr(handlers.compute, "paper_account_view", lambda: view)
    monkeypatch.setattr(handlers.compute, "assess_open_positions",
                        lambda: {"per_position": {}, "summary": {}})
    monkeypatch.setattr(handlers.compute, "manual_analytics", lambda: {})


def test_a_valid_paper_account_is_published_as_built(monkeypatch):
    bus = Bus(fake=True)
    _account(monkeypatch, dict(GOOD_ACCOUNT))
    handlers.refresh_paper_account(bus)
    got = _payload(bus, handlers.CACHE_PAPER)
    assert got["custom"] == 7 and got["positions"][0]["position_id"] == 1
    assert _degrade.counts() == {}


def test_a_misshapen_paper_account_does_not_replace_the_last_good_one(monkeypatch):
    bus = Bus(fake=True)
    _account(monkeypatch, dict(GOOD_ACCOUNT))
    handlers.refresh_paper_account(bus)
    _account(monkeypatch, dict(GOOD_ACCOUNT, positions={"1": {}}))
    handlers.refresh_paper_account(bus)
    kept = _payload(bus, handlers.CACHE_PAPER)["positions"]
    assert isinstance(kept, list) and kept[0]["position_id"] == 1
    assert bus.cache_get(handlers.CACHE_PAPER).version == 1
    assert _degrade.counts() == {"options.contract.paper_account": 1}


# ---- the rescue summary ------------------------------------------------------

def test_a_rescue_summary_with_a_bad_count_is_not_published(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "assess_open_positions", lambda: {
        "summary": {"n_tested": 1, "n_critical": 0, "position_ids": [5]}})
    handlers.publish_rescue_summary(bus)
    monkeypatch.setattr(handlers.compute, "assess_open_positions", lambda: {
        "summary": {"n_tested": -3, "n_critical": 0, "position_ids": [5]}})
    handlers.publish_rescue_summary(bus)
    assert _payload(bus, handlers.CACHE_RESCUE_SUMMARY)["n_tested"] == 1
    assert _degrade.counts() == {"options.contract.rescue_summary": 1}


# ---- the ledger --------------------------------------------------------------

def _ledger(monkeypatch, view):
    monkeypatch.setattr(handlers.compute, "paper_trades_view",
                        lambda reprice=True: view)
    monkeypatch.setattr(handlers, "refresh_ledger_caps", lambda bus, trades=None: None)


def test_a_valid_ledger_is_published_as_built(monkeypatch):
    bus = Bus(fake=True)
    _ledger(monkeypatch, {"trades": [{"trade_id": 9, "status": "OPEN"}], "note": "x"})
    handlers.refresh_paper_trades(bus)
    assert _payload(bus, handlers.CACHE_PAPER_TRADES) == {
        "trades": [{"trade_id": 9, "status": "OPEN"}], "note": "x"}


def test_a_misshapen_ledger_does_not_replace_the_last_good_one(monkeypatch):
    bus = Bus(fake=True)
    _ledger(monkeypatch, {"trades": [{"trade_id": 9}]})
    handlers.refresh_paper_trades(bus)
    _ledger(monkeypatch, {"trades": {"9": {}}})
    handlers.refresh_paper_trades(bus)
    assert _payload(bus, handlers.CACHE_PAPER_TRADES) == {"trades": [{"trade_id": 9}]}
    assert _degrade.counts() == {"options.contract.paper_trades": 1}


# ---- the ledger's caps book --------------------------------------------------

def _caps(monkeypatch, state):
    monkeypatch.setattr(handlers.compute, "ledger_book_state",
                        lambda trades=None: dict(state))


GOOD_CAPS = {"open": [], "starting_balance": 25000.0, "realized_pnl": 0.0,
             "equity": 25000.0, "limits": {"max_risk_per_trade": 750}}


def test_a_caps_book_with_a_nan_equity_is_not_published(monkeypatch):
    bus = Bus(fake=True)
    _caps(monkeypatch, GOOD_CAPS)
    handlers.refresh_ledger_caps(bus)
    assert _payload(bus, handlers.CACHE_LEDGER_CAPS)["equity"] == 25000.0
    _caps(monkeypatch, dict(GOOD_CAPS, equity=float("nan")))
    handlers.refresh_ledger_caps(bus)
    # The dialog keeps previewing against the last book that was a real one.
    assert _payload(bus, handlers.CACHE_LEDGER_CAPS)["equity"] == 25000.0
    assert _degrade.counts() == {"options.contract.ledger_caps": 1}


# ---- the Paper button's answer ----------------------------------------------

def test_a_valid_paper_create_answer_is_published_with_its_sequence(monkeypatch):
    bus = Bus(fake=True)
    handlers._publish_paper_create(bus, {"status": "refused", "symbol": "SPY",
                                         "rungs": [{"code": "X"}], "message": "No."})
    got = _payload(bus, handlers.CACHE_PAPER_CREATE)
    assert got["status"] == "refused" and got["rungs"] == [{"code": "X"}]
    assert isinstance(got["seq"], int) and got["ts"]


def test_an_unreadable_paper_create_answer_still_answers_the_click(monkeypatch):
    # Publishing nothing would leave the Paper button looking as if it did
    # nothing; the click is answered with an error instead.
    bus = Bus(fake=True)
    handlers._publish_paper_create(bus, {"status": "sure", "rungs": "all",
                                         "symbol": "SPY"})
    got = _payload(bus, handlers.CACHE_PAPER_CREATE)
    assert got["status"] == "error" and got["symbol"] == "SPY"
    assert "Paper Ledger" in got["message"] and isinstance(got["seq"], int)
    assert _degrade.counts() == {"options.contract.paper_create": 1}
