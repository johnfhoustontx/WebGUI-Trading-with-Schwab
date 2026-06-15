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


# ── Paper account (Task 2.6c-1) ──────────────────────────────────────────────
def _fake_paper_view():
    return {
        "snapshot": {"equity": 25100.0, "cash": 24000.0, "open_count": 2,
                     "halted": False},
        "positions": [{"position_id": 1, "symbol": "SPY"}],
        "orders": [{"order_id": 10, "symbol": "SPY", "status": "FILLED"}],
        "has_account": True,
    }


def test_refresh_paper_account_caches_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    view = _fake_paper_view()
    monkeypatch.setattr(handlers.compute, "paper_account_view", lambda: view)

    sub = bus.subscribe("events:options:paper_account")
    handlers.refresh_paper_account(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:paper_account")
    assert env is not None
    assert env.payload == view
    assert msg is not None and msg.get("version") == env.version


def test_paper_command_dispatch(monkeypatch):
    """Each paper command calls the right compute fn + refreshes the cache view."""
    bus = Bus(fake=True)
    calls = {"refresh": 0, "entry": 0, "manage": 0, "reset": None,
             "has_account": True}

    monkeypatch.setattr(handlers.compute, "paper_account_view", _fake_paper_view)
    monkeypatch.setattr(handlers.compute, "has_paper_account",
                        lambda: calls["has_account"])
    monkeypatch.setattr(handlers.compute, "run_entry_cycle",
                        lambda: calls.__setitem__("entry", calls["entry"] + 1))
    monkeypatch.setattr(handlers.compute, "run_manage_cycle",
                        lambda: calls.__setitem__("manage", calls["manage"] + 1))
    monkeypatch.setattr(handlers.compute, "reset_paper_account",
                        lambda bal: calls.__setitem__("reset", bal))

    def _count_refresh(b):
        assert b is bus
        calls["refresh"] += 1

    monkeypatch.setattr(handlers, "refresh_paper_account", _count_refresh)

    # refresh_paper -> just refresh.
    handlers.handle_command(bus, Command(type="refresh_paper"))
    assert calls["refresh"] == 1

    # paper_entry (account present) -> entry cycle + refresh.
    handlers.handle_command(bus, Command(type="paper_entry"))
    assert calls["entry"] == 1 and calls["refresh"] == 2

    # paper_manage (account present) -> manage cycle + refresh.
    handlers.handle_command(bus, Command(type="paper_manage"))
    assert calls["manage"] == 1 and calls["refresh"] == 3

    # paper_reset -> reset with the given balance + refresh.
    handlers.handle_command(bus, Command(type="paper_reset",
                                         args={"starting_balance": 50000.0}))
    assert calls["reset"] == 50000.0 and calls["refresh"] == 4

    # paper_reset with no args -> default balance.
    handlers.handle_command(bus, Command(type="paper_reset"))
    assert calls["reset"] == 25000.0 and calls["refresh"] == 5


def test_paper_entry_manage_short_circuit_when_no_account(monkeypatch):
    """With no account, entry/manage do NOT run the cycle — they just refresh."""
    bus = Bus(fake=True)
    calls = {"entry": 0, "manage": 0, "refresh": 0}

    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: False)
    monkeypatch.setattr(handlers.compute, "run_entry_cycle",
                        lambda: calls.__setitem__("entry", calls["entry"] + 1))
    monkeypatch.setattr(handlers.compute, "run_manage_cycle",
                        lambda: calls.__setitem__("manage", calls["manage"] + 1))
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda b: calls.__setitem__("refresh", calls["refresh"] + 1))

    handlers.handle_command(bus, Command(type="paper_entry"))
    handlers.handle_command(bus, Command(type="paper_manage"))

    assert calls["entry"] == 0 and calls["manage"] == 0
    assert calls["refresh"] == 2  # both still refresh so the page shows no-account


# ── Paper trades ledger (Task 2.6c-2) ────────────────────────────────────────
def _fake_trades_view():
    return {"trades": [
        {"trade_id": "T1", "symbol": "SPY", "status": "OPEN"},
        {"trade_id": "T2", "symbol": "QQQ", "status": "CLOSED"},
    ]}


def test_refresh_paper_trades_caches_publishes(monkeypatch):
    bus = Bus(fake=True)
    view = _fake_trades_view()
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda: view)

    sub = bus.subscribe("events:options:paper_trades")
    handlers.refresh_paper_trades(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:paper_trades")
    assert env is not None
    assert env.payload == view
    assert msg is not None and msg.get("version") == env.version


def test_paper_lifecycle_commands(monkeypatch):
    """paper_close/paper_delete/paper_delete_closed call the right compute fn +
    refresh the ledger view."""
    bus = Bus(fake=True)
    calls = {"close": None, "delete": None, "delete_closed": 0, "refresh": 0}

    monkeypatch.setattr(handlers.compute, "close_paper",
                        lambda tid, debit: calls.__setitem__("close", (tid, debit)))
    monkeypatch.setattr(handlers.compute, "delete_paper",
                        lambda tid: calls.__setitem__("delete", tid))
    monkeypatch.setattr(handlers.compute, "delete_closed_paper",
                        lambda: calls.__setitem__("delete_closed",
                                                  calls["delete_closed"] + 1))

    def _count_refresh(b):
        assert b is bus
        calls["refresh"] += 1

    monkeypatch.setattr(handlers, "refresh_paper_trades", _count_refresh)

    # paper_reload -> just refresh.
    handlers.handle_command(bus, Command(type="paper_reload"))
    assert calls["refresh"] == 1

    # paper_close -> close with (trade_id, debit) + refresh.
    handlers.handle_command(bus, Command(
        type="paper_close", args={"trade_id": "T1", "debit": 0.45}))
    assert calls["close"] == ("T1", 0.45) and calls["refresh"] == 2

    # paper_delete -> delete by id + refresh.
    handlers.handle_command(bus, Command(type="paper_delete", args={"trade_id": "T2"}))
    assert calls["delete"] == "T2" and calls["refresh"] == 3

    # paper_delete_closed -> delete-all-closed + refresh.
    handlers.handle_command(bus, Command(type="paper_delete_closed"))
    assert calls["delete_closed"] == 1 and calls["refresh"] == 4


def test_paper_analyze_caches_result(monkeypatch):
    """paper_analyze caches cache:options:paper_analyze + publishes (no ledger refresh)."""
    bus = Bus(fake=True)
    res = {"trade_id": "T1", "symbol": "SPY", "action": "HOLD"}
    seen = {"tid": None}

    def _analyze(tid):
        seen["tid"] = tid
        return res

    monkeypatch.setattr(handlers.compute, "analyze_paper", _analyze)

    sub = bus.subscribe("events:options:paper_analyze")
    handlers.handle_command(bus, Command(type="paper_analyze", args={"trade_id": "T1"}))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["tid"] == "T1"
    env = bus.cache_get("cache:options:paper_analyze")
    assert env is not None
    assert env.payload == res
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
