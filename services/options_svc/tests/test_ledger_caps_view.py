from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from services.options_svc import handlers


def test_the_view_carries_limits_equity_open_trades_and_a_sector_map(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "ledger_book_state", lambda: {
        "open": [{"symbol": "ORCL", "expiration": "2026-10-17",
                  "max_loss_total": 190.0, "sector": "Information Technology"}],
        "starting_balance": 25000.0, "realized_pnl": 10.0, "equity": 25010.0,
        "limits": {"max_risk_per_trade": 250.0}})
    monkeypatch.setattr(handlers, "_scan_universe", lambda: ["ORCL", "XOM"])
    handlers.refresh_ledger_caps(bus)
    p = bus.cache_get(handlers.CACHE_LEDGER_CAPS).payload
    assert p["equity"] == 25010.0 and p["limits"]["max_risk_per_trade"] == 250.0
    assert p["open"][0]["sector"] == "Information Technology"
    assert p["sector_of"]["XOM"] == "Energy"
    assert p["sector_of"]["ORCL"] == "Information Technology"


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


def test_an_open_trade_outside_the_watchlist_still_gets_a_sector(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "ledger_book_state", lambda: {
        "open": [{"symbol": "XOM", "expiration": "2026-10-17", "max_loss_total": 50.0,
                  "sector": "Energy"}],
        "starting_balance": 25000.0, "realized_pnl": 0.0, "equity": 25000.0, "limits": {}})
    monkeypatch.setattr(handlers, "_scan_universe", lambda: [])
    handlers.refresh_ledger_caps(bus)
    assert bus.cache_get(handlers.CACHE_LEDGER_CAPS).payload["sector_of"] == {"XOM": "Energy"}


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
    monkeypatch.setattr(handlers, "_scan_universe", lambda: ["ORCL"])
    paper_trader.add_trade(paper_trader.create_paper_trade(
        {"symbol": "ORCL", "type": "PCS", "expiration": "2026-10-17", "dte": 30,
         "short_strike": 100.0, "long_strike": 97.5, "width": 2.5, "credit": 0.60,
         "max_loss": 1.90}, 1))
    handlers.refresh_ledger_caps(bus)
    p = bus.cache_get(handlers.CACHE_LEDGER_CAPS).payload
    assert p["open"][0]["max_loss_total"] == 190.0
    assert p["limits"]["max_risk_per_trade"] == 750.0
    assert p["equity"] == 25000.0
