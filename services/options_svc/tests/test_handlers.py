"""Tests for the options service rescan handler (Task 2.3).

The handler is the service-side analog of ``webgui/pages/options/scanner.py``'s
scan call: it computes via ``compute.run_scan`` (a dict), projects it onto the
``ScanResult`` contract as a validation gate, caches the full validated payload
under ONE key (``cache:options:scan`` — a single scan produces BOTH the 0-DTE
and swing signal lists, so one cache view holds the whole result), and publishes
a change event. We monkeypatch ``handlers.compute.run_scan`` so nothing touches a
live proxy, and use a fakeredis ``Bus(fake=True)``.
"""
from shared.bus import Bus
from shared.contracts.envelope import Command
from services.options_svc import handlers


def _fake_result():
    """A scan result shaped like ``scanner_engine.run_full_scan`` output."""
    return {
        "signals_0dte": [{"symbol": "SPY", "trade_type": "PCS", "score": 8.1}],
        "signals_swing": [{"symbol": "QQQ", "trade_type": "CCS", "score": 7.2}],
        "vix_term_structure": {"vix": 14.2, "vix9d": 13.1, "ratio": 0.92},
        "timestamp": "2026-06-15T13:30:00-04:00",
        "errors": [],
        "warnings": ["watchlist degraded"],
        # Extra engine keys the GUI ignores — must be dropped by the gate.
        "scanned_symbols": ["SPY", "QQQ"],
        "regime": {"state": "risk_on"},
    }


def test_rescan_caches_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    result = _fake_result()
    monkeypatch.setattr(handlers.compute, "run_scan", lambda: result)

    sub = bus.subscribe("events:options:scan")
    handlers.rescan(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:scan")
    assert env is not None
    payload = env.payload
    # Both signal lists live in the one cache view (GUI tabs both read it).
    assert payload["signals_0dte"] == result["signals_0dte"]
    assert payload["signals_swing"] == result["signals_swing"]
    assert payload["vix_term_structure"] == result["vix_term_structure"]
    assert payload["timestamp"] == result["timestamp"]
    assert payload["errors"] == result["errors"]
    assert payload["warnings"] == result["warnings"]
    # The gate projects onto ScanResult fields only — extra keys dropped.
    assert "scanned_symbols" not in payload
    assert "regime" not in payload
    # Event published with the cache_set version.
    assert msg is not None and "version" in msg
    assert msg["version"] == env.version


def test_rescan_gate_rejects_malformed(monkeypatch):
    bus = Bus(fake=True)
    # signals_0dte is a string, not a list -> the ScanResult gate must trip.
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: {"signals_0dte": "not-a-list"})

    import pytest
    with pytest.raises(Exception):
        handlers.rescan(bus)

    # Nothing cached when the gate rejects the shape.
    assert bus.cache_get("cache:options:scan") is None


def test_refresh_header_caches_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    view = {
        "prices": {"$SPX": 5400.0, "SPY": 742.0, "QQQ": 480.0},
        "vix": 14.2,
        "vix_regime": {"label": "Calm", "color": "#1D9E75"},
        "sentiment": {"color": "#EFC347", "label": "Neutral"},
    }
    monkeypatch.setattr(handlers.compute, "refresh_header", lambda: view)

    sub = bus.subscribe("events:options:header")
    handlers.refresh_header(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:header")
    assert env is not None
    assert env.payload == view
    assert msg is not None and msg.get("version") == env.version


def test_handle_command_rescan(monkeypatch):
    bus = Bus(fake=True)
    seen = {"calls": 0}

    def _rec(b):
        assert b is bus
        seen["calls"] += 1

    monkeypatch.setattr(handlers, "rescan", _rec)

    handlers.handle_command(bus, Command(type="rescan"))
    assert seen["calls"] == 1

    handlers.handle_command(bus, Command(type="bogus"))
    assert seen["calls"] == 1  # unknown type -> no-op


# ── Swing scan (on-demand) ───────────────────────────────────────────────────
def _fake_swing_signals():
    return [
        {"id": "SPY_0_PCS_530", "symbol": "SPY", "type": "PCS", "composite_score": 8.4},
        {"id": "SPY_1_CCS_560", "symbol": "SPY", "type": "CCS", "composite_score": 7.1},
    ]


def test_swing_scan_command(monkeypatch):
    bus = Bus(fake=True)
    signals = _fake_swing_signals()
    seen = {"params": None}

    def _rec(**params):
        seen["params"] = params
        return signals

    monkeypatch.setattr(handlers.compute, "swing_scan", _rec)

    args = {
        "symbol": "SPY", "dte_min": 5, "dte_max": 30,
        "put_d_min": -0.20, "put_d_max": -0.10,
        "call_d_min": 0.10, "call_d_max": 0.20,
        "min_cr_fraction": 0.10,
    }
    sub = bus.subscribe("events:options:swing")
    handlers.handle_command(bus, Command(type="swing_scan", args=args))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    # compute called with the args from the command (already a fraction).
    assert seen["params"] == args

    env = bus.cache_get("cache:options:swing")
    assert env is not None
    payload = env.payload
    assert payload["signals"] == signals
    assert payload["symbol"] == "SPY"
    assert payload["params"] == args
    # Event published with the cache_set version.
    assert msg is not None and msg.get("version") == env.version


def test_swing_scan_uses_defaults_for_missing_args(monkeypatch):
    bus = Bus(fake=True)
    seen = {"params": None}

    def _rec(**params):
        seen["params"] = params
        return []

    monkeypatch.setattr(handlers.compute, "swing_scan", _rec)

    # Only a symbol given -> the rest fall back to page-default params.
    handlers.swing_scan(bus, {"symbol": "QQQ"})
    assert seen["params"]["symbol"] == "QQQ"
    assert seen["params"]["dte_min"] == 5
    assert seen["params"]["min_cr_fraction"] == 0.10
    # Even with no signals, the (empty) result is cached + published.
    env = bus.cache_get("cache:options:swing")
    assert env is not None and env.payload["signals"] == []
