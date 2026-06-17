"""Tests for the portfolio service handlers (Task #20).

The handlers are the Tier-2 → Tier-3 write path: ``rebuild`` orchestrates a full
model build (proxy health → trades → model → baselines) into the shared state and
publishes; ``publish_current`` re-formats the current state and caches+publishes
(used on each throttled stream tick); ``handle_command`` flags a rebuild for the
scheduler. We monkeypatch ``compute`` so nothing touches a live proxy and use a
fakeredis ``Bus``.
"""
import json

from shared.bus import Bus
from shared.contracts.envelope import Command
from shared.contracts.portfolio import PortfolioModel
from services.portfolio_svc import handlers
from services.portfolio_svc.state import PortfolioState


def _model():
    return {
        "holdings": [{"symbol": "AAPL", "asset_type": "EQUITY",
                      "sector": "Technology", "sector_etf": "XLK",
                      "quantity": 10, "avg_price": 100.0, "market_value": 1100.0,
                      "day_pl": 10.0, "total_pl": 100.0, "vs_sector_rs": None,
                      "since_purchase_excess": None}],
        "sectors": [{"sector": "Technology", "weight": 1.0,
                     "benchmark_delta": None, "tailwind": None}],
    }


def test_publish_current_caches_and_publishes():
    bus = Bus(fake=True)
    state = PortfolioState()
    state.raw_model = _model()
    state.proxy_up = True
    state.streaming = True

    sub = bus.subscribe("events:portfolio:positions")
    version = handlers.publish_current(bus, state)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:portfolio:positions")
    assert env is not None and env.version == version == 1
    pm = PortfolioModel.from_json(json.dumps(env.payload))
    assert pm.holdings_rows[0]["symbol"] == "AAPL"
    assert pm.proxy_up is True and pm.streaming is True
    assert msg is not None and "version" in msg


def test_rebuild_proxy_down_publishes_empty(monkeypatch):
    bus = Bus(fake=True)
    state = PortfolioState()
    monkeypatch.setattr(handlers.compute, "make_data", lambda: object())
    monkeypatch.setattr(handlers.compute, "proxy_up", lambda: False)

    handlers.rebuild(bus, state)

    env = bus.cache_get("cache:portfolio:positions")
    pm = PortfolioModel.from_json(json.dumps(env.payload))
    assert pm.proxy_up is False
    assert pm.errors  # carries a proxy-down note
    assert state.proxy_up is False


def test_rebuild_happy(monkeypatch):
    bus = Bus(fake=True)
    state = PortfolioState()
    monkeypatch.setattr(handlers.compute, "make_data", lambda: object())
    monkeypatch.setattr(handlers.compute, "proxy_up", lambda: True)
    monkeypatch.setattr(handlers.compute, "load_trades", lambda data: [])
    monkeypatch.setattr(handlers.compute, "build_raw_model",
                        lambda data, trades: (_model(), []))
    monkeypatch.setattr(handlers.compute, "compute_baselines",
                        lambda data, model, trades: {})

    handlers.rebuild(bus, state)

    assert state.proxy_up is True
    assert state.raw_model["holdings"][0]["symbol"] == "AAPL"
    env = bus.cache_get("cache:portfolio:positions")
    pm = PortfolioModel.from_json(json.dumps(env.payload))
    assert pm.holdings_rows[0]["symbol"] == "AAPL"


def test_handle_command_refresh_sets_flag():
    from services.portfolio_svc import state as state_mod

    state_mod.STATE.rebuild_requested = False
    bus = Bus(fake=True)
    handlers.handle_command(bus, Command(type="refresh"))
    assert state_mod.STATE.rebuild_requested is True

    state_mod.STATE.rebuild_requested = False
    handlers.handle_command(bus, Command(type="bogus"))  # unknown -> no-op
    assert state_mod.STATE.rebuild_requested is False
