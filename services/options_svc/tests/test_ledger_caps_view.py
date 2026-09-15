import pytest

from shared import book_caps
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from services.options_svc import handlers


def _book(equity=25010.0):
    return {
        "open": [{"symbol": "ORCL", "expiration": "2026-10-17",
                  "max_loss_total": 190.0, "sector": "Information Technology"}],
        "starting_balance": 25000.0, "realized_pnl": equity - 25000.0, "equity": equity,
        "limits": {"max_risk_per_trade": 250.0}}


def test_the_view_carries_limits_equity_open_trades_and_the_sector_table(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "ledger_book_state", lambda: _book())
    handlers.refresh_ledger_caps(bus)
    p = bus.cache_get(handlers.CACHE_LEDGER_CAPS).payload
    assert p["equity"] == 25010.0 and p["limits"]["max_risk_per_trade"] == 250.0
    assert p["open"][0]["sector"] == "Information Technology"
    assert p["sectors"]["XOM"] == "Energy"
    assert p["sectors"]["ORCL"] == "Information Technology"
    assert p["unmapped_prefix"] == book_caps.UNMAPPED_PREFIX
    assert book_caps.sector_bucket(p["sectors"], "zzzq") == "?ZZZQ"


def test_a_failure_is_a_degrade_not_a_raise(monkeypatch):
    reset_fake_bus()
    monkeypatch.setattr(handlers.compute, "ledger_book_state",
                        lambda: (_ for _ in ()).throw(RuntimeError("db")))
    handlers.refresh_ledger_caps(Bus(fake=True))     # must not raise


def test_a_failure_records_a_degrade(monkeypatch):
    reset_fake_bus()
    seen = []
    monkeypatch.setattr(handlers._degrade, "degraded", lambda area, **kw: seen.append(area))
    monkeypatch.setattr(handlers.compute, "ledger_book_state",
                        lambda: (_ for _ in ()).throw(RuntimeError("db")))
    handlers.refresh_ledger_caps(Bus(fake=True))
    assert seen == ["options.ledger_caps"]


def test_a_symbol_not_in_the_table_buckets_alone_via_sector_bucket(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "ledger_book_state", lambda: {
        "open": [{"symbol": "XOM", "expiration": "2026-10-17", "max_loss_total": 50.0,
                  "sector": "Energy"}],
        "starting_balance": 25000.0, "realized_pnl": 0.0, "equity": 25000.0, "limits": {}})
    handlers.refresh_ledger_caps(bus)
    p = bus.cache_get(handlers.CACHE_LEDGER_CAPS).payload
    assert "ZZZQ" not in p["sectors"]
    assert book_caps.sector_bucket(p["sectors"], "zzzq") == "?ZZZQ"
    assert book_caps.sector_bucket(p["sectors"], "xom") == "Energy"


def test_every_ledger_refresh_republishes_the_book(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    calls = []
    monkeypatch.setattr(handlers, "refresh_ledger_caps", lambda b: calls.append(b))
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda reprice=True: {"trades": []})
    handlers.refresh_paper_trades(bus)
    assert calls == [bus]


def test_the_real_book_round_trips(monkeypatch, tmp_path):
    """Real compute.ledger_book_state over a tmp Ledger with one open trade."""
    import paper_trader
    import trade_tracker_client
    import trades_db
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(trades_db, "_initialised", set())
    monkeypatch.setattr(trade_tracker_client, "track", lambda t: True)
    monkeypatch.setattr(trade_tracker_client, "untrack", lambda tid: True)
    paper_trader.add_trade(paper_trader.create_paper_trade(
        {"symbol": "ORCL", "type": "PCS", "expiration": "2026-10-17", "dte": 30,
         "short_strike": 100.0, "long_strike": 97.5, "width": 2.5, "credit": 0.60,
         "max_loss": 1.90}, 1))
    handlers.refresh_ledger_caps(bus)
    p = bus.cache_get(handlers.CACHE_LEDGER_CAPS).payload
    assert p["open"][0]["max_loss_total"] == 190.0
    assert p["limits"]["max_risk_per_trade"] == 750.0
    assert p["equity"] == 25000.0


# --- review fixes -----------------------------------------------------------

def test_an_unchanged_book_bumps_no_version_and_a_changed_one_does(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    book = {"equity": 25010.0}
    monkeypatch.setattr(handlers.compute, "ledger_book_state",
                        lambda: _book(book["equity"]))
    handlers.refresh_ledger_caps(bus)
    v1 = bus.cache_version(handlers.CACHE_LEDGER_CAPS)
    assert v1 is not None
    handlers.refresh_ledger_caps(bus)
    assert bus.cache_version(handlers.CACHE_LEDGER_CAPS) == v1
    book["equity"] = 25200.0
    handlers.refresh_ledger_caps(bus)
    assert bus.cache_version(handlers.CACHE_LEDGER_CAPS) == v1 + 1


def test_the_book_is_republished_even_when_the_trades_view_raises(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    calls = []
    monkeypatch.setattr(handlers, "refresh_ledger_caps",
                        lambda b, **kw: calls.append((b, kw)))

    def boom(reprice=True):
        raise RuntimeError("view")

    monkeypatch.setattr(handlers.compute, "paper_trades_view", boom)
    with pytest.raises(RuntimeError):
        handlers.refresh_paper_trades(bus)
    assert calls == [(bus, {})]


def test_the_trades_just_read_are_forwarded_to_the_book(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    trades = [{"id": 1, "symbol": "ORCL", "status": "OPEN"}]
    calls = []
    monkeypatch.setattr(handlers, "refresh_ledger_caps",
                        lambda b, **kw: calls.append((b, kw)))
    monkeypatch.setattr(handlers.compute, "paper_trades_view",
                        lambda reprice=True: {"trades": trades})
    handlers.refresh_paper_trades(bus)
    assert calls == [(bus, {"trades": trades})]


def test_forwarded_trades_reach_ledger_book_state(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    seen = []

    def state(trades=None):
        seen.append(trades)
        return _book()

    monkeypatch.setattr(handlers.compute, "ledger_book_state", state)
    trades = [{"id": 1}]
    handlers.refresh_ledger_caps(bus, trades=trades)
    handlers.refresh_ledger_caps(bus)
    assert seen == [trades, None]


def test_the_book_is_read_and_written_under_the_lock(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    held = []

    def state():
        held.append(handlers._LEDGER_CAPS_LOCK.locked())
        return _book()

    monkeypatch.setattr(handlers.compute, "ledger_book_state", state)
    real_set = bus.cache_set

    def spy_set(*a, **kw):
        held.append(handlers._LEDGER_CAPS_LOCK.locked())
        return real_set(*a, **kw)

    monkeypatch.setattr(bus, "cache_set", spy_set)
    handlers.refresh_ledger_caps(bus)
    assert held == [True, True]
    assert not handlers._LEDGER_CAPS_LOCK.locked()
