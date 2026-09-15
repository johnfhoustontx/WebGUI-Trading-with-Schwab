"""Every paper_create outcome reaches the screen."""
import datetime as _dt

import pytest

from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command
from services.options_svc import handlers


def _stub(monkeypatch, outcome):
    monkeypatch.setattr(handlers.compute, "create_paper_trade", lambda s, q: outcome)
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda reprice=True: {"trades": []})


def test_a_refusal_is_published_with_its_reason(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, {"status": "refused", "code": "TRADE_RISK_CAP",
                        "message": "Risks $400, over the $250 per-trade limit",
                        "max_quantity": 0, "symbol": "MU", "rungs": []})
    handlers.handle_command(bus, Command(type="paper_create",
                                         args={"signal": {"symbol": "MU"}, "qty": 1}))
    env = bus.cache_get(handlers.CACHE_PAPER_CREATE)
    assert env is not None
    assert env.payload["status"] == "refused"
    assert env.payload["code"] == "TRADE_RISK_CAP"
    assert env.payload["seq"] >= 1 and env.payload["ts"]


def test_two_identical_refusals_are_two_distinct_publishes(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, {"status": "refused", "code": "X", "message": "m", "rungs": []})
    cmd = Command(type="paper_create", args={"signal": {"symbol": "MU"}, "qty": 1})
    handlers.handle_command(bus, cmd)
    first = bus.cache_get(handlers.CACHE_PAPER_CREATE)
    handlers.handle_command(bus, cmd)
    second = bus.cache_get(handlers.CACHE_PAPER_CREATE)
    assert second.payload["seq"] == first.payload["seq"] + 1
    assert second.version > first.version


def test_an_opened_trade_is_published_without_the_full_trade_dict(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, {"status": "opened", "trade_id": "ab12", "symbol": "SPY",
                        "trade": {"trade_id": "ab12"}, "rungs": []})
    handlers.handle_command(bus, Command(type="paper_create",
                                         args={"signal": {"symbol": "SPY"}, "qty": 1}))
    payload = bus.cache_get(handlers.CACHE_PAPER_CREATE).payload
    assert payload["status"] == "opened" and payload["trade_id"] == "ab12"
    assert "trade" not in payload


def test_a_stale_command_is_published_too(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, {"status": "opened"})
    old = (_dt.datetime.now(_dt.timezone.utc)
           - _dt.timedelta(seconds=handlers.STALE_OPEN_MAX_AGE_SEC + 60)).isoformat()
    handlers.handle_command(bus, Command(type="paper_create", ts=old,
                                         args={"signal": {"symbol": "SPY"}, "qty": 1}))
    payload = bus.cache_get(handlers.CACHE_PAPER_CREATE).payload
    assert payload["status"] == "stale" and payload["symbol"] == "SPY"


def test_a_stub_returning_nothing_publishes_an_error_not_a_crash(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, None)
    handlers.handle_command(bus, Command(type="paper_create",
                                         args={"signal": {"symbol": "SPY"}, "qty": 1}))
    assert bus.cache_get(handlers.CACHE_PAPER_CREATE).payload["status"] == "error"


def test_the_published_view_expires(monkeypatch):
    """A TTL, so an old answer is never read as fresh after a restart."""
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, {"status": "refused", "code": "X", "message": "m", "rungs": []})
    handlers.handle_command(bus, Command(type="paper_create",
                                         args={"signal": {"symbol": "MU"}, "qty": 1}))
    ttl = bus._r.ttl(handlers.CACHE_PAPER_CREATE)
    assert 0 < ttl <= handlers.PAPER_CREATE_TTL_SEC


def test_the_real_outcome_round_trips_through_the_bus(monkeypatch, tmp_path):
    """Drive the REAL compute.create_paper_trade (not a stub) into a tmp Ledger and
    read the published payload back - the producer, not an invented dict."""
    import config_paper
    reset_fake_bus()
    bus = Bus(fake=True)
    _real_ledger(monkeypatch, tmp_path)
    cap = config_paper.LEDGER_MAX_RISK_PER_TRADE
    # Risk sized to sit $150 over whatever the cap is, so the refusal stays real
    # if the cap moves (at $750 this is the 10-wide $1.00-credit, $900 spread).
    risk = cap + 150
    width = risk / 100 + 1.00
    assert risk > cap
    sig = {"symbol": "ORCL", "type": "PCS", "trade_type": "SWING",
           "expiration": "2026-10-17", "dte": 30, "short_strike": 100.0,
           "long_strike": 100.0 - width, "width": width, "credit": 1.00,
           "max_loss": width - 1.00}
    handlers.handle_command(bus, Command(type="paper_create", args={"signal": sig, "qty": 1}))
    payload = bus.cache_get(handlers.CACHE_PAPER_CREATE).payload
    assert payload["status"] == "refused" and payload["code"] == "TRADE_RISK_CAP"
    assert payload["message"] == f"Risks ${risk:,.0f}, over the ${cap:,.0f} per-trade limit"
    assert payload["max_quantity"] == 0
    assert [r["code"] for r in payload["rungs"]][0] == "TRADE_RISK_CAP"


def _real_ledger(monkeypatch, tmp_path):
    """Point the REAL Ledger at a tmp trades.db and silence the tracker, so a
    test drives compute.create_paper_trade itself rather than a stub."""
    import paper_trader  # noqa: F401  (ensures options-scanner is importable)
    import trade_tracker_client
    import trades_db
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(trades_db, "_initialised", set())
    monkeypatch.setattr(trade_tracker_client, "track", lambda t: True)
    monkeypatch.setattr(trade_tracker_client, "untrack", lambda tid: True)
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda reprice=True: {"trades": []})


def test_a_raise_still_publishes_an_error_then_propagates(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)

    def _boom(s, q):
        raise RuntimeError("db locked")

    monkeypatch.setattr(handlers.compute, "create_paper_trade", _boom)
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda reprice=True: {"trades": []})
    with pytest.raises(RuntimeError):
        handlers.handle_command(bus, Command(type="paper_create",
                                             args={"signal": {"symbol": "SPY"}, "qty": 1}))
    payload = bus.cache_get(handlers.CACHE_PAPER_CREATE).payload
    assert payload["status"] == "error"
    assert payload["message"] == "The paper ledger could not process the request."


def test_a_non_dict_signal_publishes_an_error_not_a_crash(monkeypatch, tmp_path):
    reset_fake_bus()
    bus = Bus(fake=True)
    _real_ledger(monkeypatch, tmp_path)
    handlers.handle_command(bus, Command(type="paper_create",
                                         args={"signal": "junk", "qty": 1}))
    assert bus.cache_get(handlers.CACHE_PAPER_CREATE).payload["status"] == "error"
