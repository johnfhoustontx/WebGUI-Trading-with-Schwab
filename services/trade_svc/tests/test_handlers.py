"""Tests for the trade service analyze handler (Task #25).

We monkeypatch ``handlers.compute.analyze`` so nothing touches a live proxy and
use a fakeredis ``Bus(fake=True)``. The handler must compute → validate against
TradeAnalysis → cache under one view → publish an event, and degrade gracefully
on an empty/failed analysis.
"""
import json

from shared.bus import Bus
from shared.contracts.envelope import Command
from shared.contracts.trade import TradeAnalysis
from services.trade_svc import handlers


def _fake_result(symbol="AAPL"):
    return {
        "symbol": symbol, "description": symbol, "price": 212.4, "volume": 1000,
        "bias": "BULLISH",
        "ema_alignment": {"alignment_percentage": 60.0, "bias": "BULLISH",
                          "timeframes": []},
        "momentum": {"rsi": 55.0, "adx": 22.0, "macd_hist": 0.4,
                     "macd_hist_prev": 0.2, "vwap": 211.0, "relative_volume": 1.3},
        "volume_profile": {"poc": 210.0, "vah": 213.0, "val": 208.0},
        "sector": {"etf": "XLK", "name": "Technology", "strength": {"score": 40}},
        "position_verdict": {"verdict": "BUY", "score": 25, "breakdown": [],
                             "top_reasons": ["ema_alignment (+12)"],
                             "gates_triggered": []},
        "investor_verdict": {"verdict": "HOLD", "score": 0, "breakdown": [],
                             "top_reasons": ["Insufficient fundamental data"],
                             "gates_triggered": ["No fundamentals"]},
        "fundamentals_available": False,
        "timestamp": "2026-06-16T12:00:00Z", "errors": [],
    }


def test_analyze_caches_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "analyze",
                        lambda sym: _fake_result(sym))

    sub = bus.subscribe("events:trade:analysis")
    handlers.analyze(bus, {"symbol": "AAPL"})
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:trade:analysis")
    assert env is not None
    assert env.version == 1
    ta = TradeAnalysis.from_json(json.dumps(env.payload))
    assert ta.symbol == "AAPL"
    assert ta.position_verdict["verdict"] == "BUY"
    assert ta.fundamentals_available is False
    assert msg is not None and "version" in msg


def test_analyze_passes_symbol_through(monkeypatch):
    bus = Bus(fake=True)
    seen = {}

    def _capture(sym):
        seen["sym"] = sym
        return _fake_result(sym)

    monkeypatch.setattr(handlers.compute, "analyze", _capture)

    handlers.analyze(bus, {"symbol": "nvda"})
    assert seen["sym"] == "nvda"


def test_analyze_empty_result_caches_error_payload(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "analyze", lambda sym: None)

    handlers.analyze(bus, {"symbol": "zzz"})

    env = bus.cache_get("cache:trade:analysis")
    assert env is not None
    ta = TradeAnalysis.from_json(json.dumps(env.payload))
    assert ta.symbol == "ZZZ"
    assert ta.errors  # carries an error so the page shows a failed state


def test_analyze_gate_rejects_malformed(monkeypatch):
    """A gross shape drift (momentum not a dict) must raise BEFORE caching."""
    bad = _fake_result()
    bad["momentum"] = "not-a-dict"
    monkeypatch.setattr(handlers.compute, "analyze", lambda sym: bad)

    import pytest
    bus = Bus(fake=True)
    with pytest.raises(Exception):
        handlers.analyze(bus, {"symbol": "AAPL"})
    assert bus.cache_get("cache:trade:analysis") is None


def test_handle_command_analyze(monkeypatch):
    bus = Bus(fake=True)
    seen = {"calls": []}
    monkeypatch.setattr(handlers, "analyze",
                        lambda b, args: seen["calls"].append(args))

    handlers.handle_command(bus, Command(type="analyze", args={"symbol": "AAPL"}))
    assert seen["calls"] == [{"symbol": "AAPL"}]

    handlers.handle_command(bus, Command(type="bogus"))
    assert seen["calls"] == [{"symbol": "AAPL"}]  # unknown type -> no-op
