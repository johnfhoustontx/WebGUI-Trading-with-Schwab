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


def test_refresh_header_skips_unchanged(monkeypatch):
    """An identical per-tick header view must NOT bump the version (no needless
    GUI repaint). Changed data still bumps."""
    bus = Bus(fake=True)
    view = {"prices": {"$SPX": 5400.0}, "vix": 14.2}
    monkeypatch.setattr(handlers.compute, "refresh_header", lambda: view)
    handlers.refresh_header(bus)
    v1 = bus.cache_get("cache:options:header").version
    handlers.refresh_header(bus)  # identical compute output -> skip
    assert bus.cache_get("cache:options:header").version == v1
    monkeypatch.setattr(handlers.compute, "refresh_header", lambda: {"prices": {"$SPX": 5401.0}})
    handlers.refresh_header(bus)  # changed -> bump
    assert bus.cache_get("cache:options:header").version == v1 + 1


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


def test_run_manage_and_refresh_runs_cycle_when_account_present(monkeypatch):
    """The shared auto-manage helper runs the cycle (account present) + refreshes."""
    bus = Bus(fake=True)
    calls = {"manage": 0, "refresh": 0}
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_manage_cycle",
                        lambda: calls.__setitem__("manage", calls["manage"] + 1))
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda b: calls.__setitem__("refresh", calls["refresh"] + 1))

    handlers.run_manage_and_refresh(bus)
    assert calls["manage"] == 1 and calls["refresh"] == 1


def test_run_manage_and_refresh_skips_cycle_when_no_account(monkeypatch):
    bus = Bus(fake=True)
    calls = {"manage": 0, "refresh": 0}
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: False)
    monkeypatch.setattr(handlers.compute, "run_manage_cycle",
                        lambda: calls.__setitem__("manage", calls["manage"] + 1))
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda b: calls.__setitem__("refresh", calls["refresh"] + 1))

    handlers.run_manage_and_refresh(bus)
    assert calls["manage"] == 0 and calls["refresh"] == 1


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


def test_paper_create_command(monkeypatch):
    """paper_create calls compute.create_paper_trade with (signal, qty) then
    refreshes the Paper Trades ledger view (so the new trade shows up)."""
    bus = Bus(fake=True)
    seen = {"create": None}
    view = _fake_trades_view()

    monkeypatch.setattr(handlers.compute, "create_paper_trade",
                        lambda signal, qty: seen.__setitem__("create", (signal, qty)))
    # paper_create refreshes the real ledger view -> stub the underlying compute
    # read so refresh_paper_trades caches a deterministic payload.
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda: view)

    sub = bus.subscribe("events:options:paper_trades")
    signal = {"symbol": "SPY", "type": "PCS", "short_strike": 530}
    handlers.handle_command(bus, Command(
        type="paper_create", args={"signal": signal, "qty": 2}))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    # compute.create_paper_trade got the signal + qty from the command.
    assert seen["create"] == (signal, 2)
    # The ledger view was refreshed (cache written + event published).
    env = bus.cache_get("cache:options:paper_trades")
    assert env is not None
    assert env.payload == view
    assert msg is not None and msg.get("version") == env.version


def test_paper_create_defaults_qty(monkeypatch):
    """paper_create with no qty arg defaults to 1."""
    bus = Bus(fake=True)
    seen = {"create": None}

    monkeypatch.setattr(handlers.compute, "create_paper_trade",
                        lambda signal, qty: seen.__setitem__("create", (signal, qty)))
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda: {"trades": []})

    signal = {"symbol": "QQQ", "type": "CCS"}
    handlers.handle_command(bus, Command(type="paper_create", args={"signal": signal}))
    assert seen["create"] == (signal, 1)


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


# ── Captured signals (Task 2.6c-3) ───────────────────────────────────────────
def _fake_captured_view():
    return {"signals": [
        {"signal_id": "X1", "symbol": "SPY", "status": "OPEN"},
        {"signal_id": "X2", "symbol": "QQQ", "status": "OPEN"},
    ]}


def test_refresh_captured_caches_publishes(monkeypatch):
    bus = Bus(fake=True)
    view = _fake_captured_view()
    monkeypatch.setattr(handlers.compute, "captured_view", lambda: view)

    sub = bus.subscribe("events:options:captured")
    handlers.refresh_captured(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:captured")
    assert env is not None
    assert env.payload == view
    assert msg is not None and msg.get("version") == env.version


def test_captured_reprice_caches_signals_and_flags(monkeypatch):
    """captured_reprice caches BOTH the repriced signal list and the flags list,
    each under its own view, and publishes both events."""
    bus = Bus(fake=True)
    repriced = [{"signal_id": "X1", "symbol": "SPY", "unrealized_pnl": 12.0}]
    flags = [{"symbol": "SPY", "code": "MONEY_STOP"}]
    monkeypatch.setattr(handlers.compute, "reprice_captured",
                        lambda: {"signals": repriced, "flags": flags})

    sub = bus.subscribe("events:options:captured")
    fsub = bus.subscribe("events:options:captured_flags")
    handlers.handle_command(bus, Command(type="captured_reprice"))
    msg = sub.get_message(timeout=1.0)
    fmsg = fsub.get_message(timeout=1.0)
    sub.close()
    fsub.close()

    env = bus.cache_get("cache:options:captured")
    assert env is not None
    assert env.payload == {"signals": repriced}
    assert msg is not None and msg.get("version") == env.version

    fenv = bus.cache_get("cache:options:captured_flags")
    assert fenv is not None
    assert fenv.payload == {"flags": flags}
    assert fmsg is not None and fmsg.get("version") == fenv.version


def test_captured_close_then_refresh(monkeypatch):
    """captured_close calls compute.close_captured with (signal_id, exit_val,
    reason) then refreshes the signals view."""
    bus = Bus(fake=True)
    calls = {"close": None, "refresh": 0}

    monkeypatch.setattr(handlers.compute, "close_captured",
                        lambda sid, ev, rsn: calls.__setitem__("close", (sid, ev, rsn)))

    def _count_refresh(b):
        assert b is bus
        calls["refresh"] += 1

    monkeypatch.setattr(handlers, "refresh_captured", _count_refresh)

    # captured_reload -> just refresh.
    handlers.handle_command(bus, Command(type="captured_reload"))
    assert calls["refresh"] == 1

    # captured_close -> close with (signal_id, exit_val, reason) + republish.
    handlers.handle_command(bus, Command(type="captured_close", args={
        "signal_id": "X1", "exit_val": 0.42, "reason": "TOOK_PROFIT"}))
    assert calls["close"] == ("X1", 0.42, "TOOK_PROFIT")
    # Cache was empty (mock refresh didn't populate it) -> the close republish
    # falls back to a full persisted refresh.
    assert calls["refresh"] == 2


def test_captured_close_preserves_live_marks_and_removes_closed(monkeypatch):
    """After 'Refresh marks (live)', closing a trade must drop ONLY the closed
    signal and KEEP the live marks on the remaining rows — not revert to the
    persisted view.

    Reproduces the reported bug: captured_close republished
    ``compute.captured_view()`` (the persisted view, which has no live marks),
    wiping the just-refreshed marks and snapping the table back to its
    pre-refresh state."""
    bus = Bus(fake=True)
    # The cache AS IT LOOKS right after 'Refresh marks (live)' — live marks merged.
    live = {"signals": [
        {"signal_id": "X1", "symbol": "SPY", "current_value": 0.20,
         "current_score": 68, "recommendation": "CUT"},
        {"signal_id": "X2", "symbol": "QQQ", "current_value": 0.50,
         "current_score": 70, "recommendation": "HOLD"},
    ]}
    bus.cache_set("cache:options:captured", live)
    # The DB flip is covered by signal_db tests; here close is a no-op. The
    # PERSISTED view (if it were wrongly consulted) carries NO live marks.
    monkeypatch.setattr(handlers.compute, "close_captured", lambda *a, **k: None)
    monkeypatch.setattr(handlers.compute, "captured_view",
                        lambda: {"signals": [{"signal_id": "X2", "symbol": "QQQ"}]})

    handlers.handle_command(bus, Command(type="captured_close", args={
        "signal_id": "X1", "exit_val": 0.10, "reason": "MANUAL_CLOSE"}))

    payload = bus.cache_get("cache:options:captured").payload
    assert [s["signal_id"] for s in payload["signals"]] == ["X2"]   # closed row gone
    x2 = payload["signals"][0]
    assert x2.get("current_value") == 0.50                          # live marks KEPT
    assert x2.get("current_score") == 70


def test_captured_close_falls_back_to_persisted_when_cache_cold(monkeypatch):
    """With no cached captured view yet, close falls back to the persisted view so
    the table is still correct (the closed signal is already excluded there)."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "close_captured", lambda *a, **k: None)
    monkeypatch.setattr(handlers.compute, "captured_view",
                        lambda: {"signals": [{"signal_id": "X2", "symbol": "QQQ"}]})

    handlers.handle_command(bus, Command(type="captured_close", args={
        "signal_id": "X1", "exit_val": 0.0, "reason": "MANUAL_CLOSE"}))

    payload = bus.cache_get("cache:options:captured").payload
    assert [s["signal_id"] for s in payload["signals"]] == ["X2"]


# ── Gamma (Task 2.6d) ────────────────────────────────────────────────────────
def _fake_gamma_snapshot():
    return {
        "symbol": "$SPX", "spot": 5400.0, "dte": 0,
        "views": {
            "GEX": {"data": {"spot": 5400.0, "gex": {"5400.0": {"net": 1.0}},
                             "strike_count": 1},
                    "summary": {"spot": 5400.0, "flip": 5399.5, "net_total": 1.0},
                    "walls": [5400.0], "flip": 5399.5, "history": []},
            "DEX": {"data": {"spot": 5400.0, "gex": {}, "strike_count": 0},
                    "summary": {}, "walls": [], "flip": None, "history": [],
                    "hedge": {"net_delta_0dte": 10.0,
                              "projected_net_delta_close": 5.0,
                              "hedge_pressure": -5.0}},
        },
        "term": {"expirations": [], "cells": {}},
    }


def test_refresh_gamma_caches_publishes(monkeypatch):
    bus = Bus(fake=True)
    snap = _fake_gamma_snapshot()
    seen = {"symbol": None}

    def _rec(symbol):
        seen["symbol"] = symbol
        return snap

    monkeypatch.setattr(handlers.compute, "gamma_snapshot", _rec)

    sub = bus.subscribe("events:options:gamma")
    handlers.refresh_gamma(bus, "$SPX")
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["symbol"] == "$SPX"
    env = bus.cache_get("cache:options:gamma")
    assert env is not None
    assert env.payload == snap
    assert msg is not None and msg.get("version") == env.version


def test_refresh_gamma_caches_empty_when_none(monkeypatch):
    """A None snapshot (chain fetch failed) caches a graceful-empty view."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "gamma_snapshot", lambda s: None)

    handlers.refresh_gamma(bus, "QQQ")
    env = bus.cache_get("cache:options:gamma")
    assert env is not None
    assert env.payload["symbol"] == "QQQ"
    assert env.payload["views"] == {}


def test_gamma_refresh_command(monkeypatch):
    bus = Bus(fake=True)
    seen = {"calls": []}

    def _rec(b, symbol="$SPX"):
        assert b is bus
        seen["calls"].append(symbol)

    monkeypatch.setattr(handlers, "refresh_gamma", _rec)

    handlers.handle_command(bus, Command(type="gamma_refresh", args={"symbol": "SPY"}))
    assert seen["calls"] == ["SPY"]

    # No symbol arg -> default $SPX.
    handlers.handle_command(bus, Command(type="gamma_refresh"))
    assert seen["calls"] == ["SPY", "$SPX"]


def test_gamma_explain_command(monkeypatch):
    bus = Bus(fake=True)
    res = {"symbol": "$SPX", "body": "<h2>GEX</h2><p>hi</p>"}
    seen = {"symbol": None}

    def _explain(symbol):
        seen["symbol"] = symbol
        return res

    monkeypatch.setattr(handlers.compute, "gamma_explain", _explain)

    sub = bus.subscribe("events:options:gamma_explain")
    handlers.handle_command(bus, Command(type="gamma_explain", args={"symbol": "$SPX"}))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["symbol"] == "$SPX"
    env = bus.cache_get("cache:options:gamma_explain")
    assert env is not None
    assert env.payload == res
    assert msg is not None and msg.get("version") == env.version


def test_gamma_analyze_command(monkeypatch):
    bus = Bus(fake=True)
    res = {"prompt": "Analyze SPX/SPY/QQQ…"}
    seen = {"called": 0}

    def _analyze():
        seen["called"] += 1
        return res

    monkeypatch.setattr(handlers.compute, "gamma_analyze", _analyze)

    sub = bus.subscribe("events:options:gamma_analyze")
    handlers.handle_command(bus, Command(type="gamma_analyze"))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["called"] == 1
    env = bus.cache_get("cache:options:gamma_analyze")
    assert env is not None
    assert env.payload == res
    assert msg is not None and msg.get("version") == env.version


# ── Simulator (Task 2.6e) ────────────────────────────────────────────────────
def test_sim_fetch_command_caches_meta(monkeypatch):
    bus = Bus(fake=True)
    meta = {"symbol": "SPY", "spot": 450.0, "n_contracts": 3,
            "expiries": ["2026-06-19"],
            "strikes": {"2026-06-19": {"call": [450], "put": [445]}}}
    seen = {"symbol": None}

    def _rec(symbol):
        seen["symbol"] = symbol
        return meta

    monkeypatch.setattr(handlers.compute, "sim_fetch", _rec)

    sub = bus.subscribe("events:options:sim_meta")
    handlers.handle_command(bus, Command(type="sim_fetch", args={"symbol": "SPY"}))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["symbol"] == "SPY"
    env = bus.cache_get("cache:options:sim_meta")
    assert env is not None
    assert env.payload == meta
    assert msg is not None and msg.get("version") == env.version


def test_sim_run_command_caches_result(monkeypatch):
    bus = Bus(fake=True)
    result = {"spot": 450.0,
              "whatif_rows": [{"S": 360.0, "theo_price": -90.0}],
              "ivshock": {"base": {"theo_price": 1.0}, "shock": {"theo_price": 1.5}}}
    seen = {"args": None}

    def _rec(symbol, expiry, kind, strike, direction, dt, mult, legs=None):
        seen["args"] = (symbol, expiry, kind, strike, direction, dt, mult)
        seen["legs"] = legs
        return result

    monkeypatch.setattr(handlers.compute, "sim_run", _rec)

    args = {"symbol": "SPY", "expiry": "2026-06-19", "kind": "call",
            "strike": 450, "direction": "buy", "dt": 5, "mult": 1.5}
    sub = bus.subscribe("events:options:sim_result")
    handlers.handle_command(bus, Command(type="sim_run", args=args))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["args"] == ("SPY", "2026-06-19", "call", 450, "buy", 5, 1.5)
    assert seen["legs"] is None
    env = bus.cache_get("cache:options:sim_result")
    assert env is not None
    assert env.payload == result
    assert msg is not None and msg.get("version") == env.version


def test_sim_run_command_threads_legs(monkeypatch):
    bus = Bus(fake=True)
    seen = {}

    def _rec(symbol, expiry=None, kind=None, strike=None, direction=None,
             dt=5.0, mult=1.5, legs=None):
        seen["legs"] = legs
        seen["symbol"] = symbol
        return {"spot": 100.0, "whatif_rows": [{"S": 80.0}], "ivshock": None}

    monkeypatch.setattr(handlers.compute, "sim_run", _rec)
    legs = [{"kind": "put", "strike": 95, "expiry": "2026-07-17", "side": "short", "qty": 1},
            {"kind": "put", "strike": 90, "expiry": "2026-07-17", "side": "long", "qty": 1}]
    handlers.handle_command(bus, Command(type="sim_run",
                                         args={"symbol": "SPY", "legs": legs, "dt": 3, "mult": 1.5}))
    assert seen["symbol"] == "SPY"
    assert seen["legs"] == legs
    env = bus.cache_get("cache:options:sim_result")
    assert env is not None


# ── Calculator (Task 2.6h) ───────────────────────────────────────────────────
def test_calc_load_command_caches_chain(monkeypatch):
    bus = Bus(fake=True)
    cc = {"symbol": "SPY", "api": "SPY", "price": 450.0,
          "range_lo": 427.5, "range_hi": 472.5,
          "chain": {"callExpDateMap": {"2026-06-19:4": {"450.0": [{"mark": 1.0}]}}}}
    seen = {"symbol": None}

    def _rec(symbol):
        seen["symbol"] = symbol
        return cc

    monkeypatch.setattr(handlers.compute, "calc_load_symbol", _rec)

    sub = bus.subscribe("events:options:calc_chain")
    handlers.handle_command(bus, Command(type="calc_load", args={"symbol": "SPY"}))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["symbol"] == "SPY"
    env = bus.cache_get("cache:options:calc_chain")
    assert env is not None
    assert env.payload == cc
    assert msg is not None and msg.get("version") == env.version


def test_calc_compute_command_caches_result(monkeypatch):
    bus = Bus(fake=True)
    result = {"summary": {"max_loss": 100.0},
              "eval_labels": ["06/18", "06/19"],
              "pnl_data": [{"price": 450.0, "pnl": [10, -5], "pnl_pct": [2.0, -1.0]}]}
    seen = {"args": None}

    def _rec(**args):
        seen["args"] = args
        return result

    monkeypatch.setattr(handlers.compute, "calc_compute", _rec)

    args = {"strategy": "PCS", "spot": 450.0, "iv": 0.18, "rate": 0.045,
            "ivadj": 0.0, "qty": 1, "expiry": "2026-06-19",
            "legs": [{"strike": 445.0, "premium": 0.5, "option_type": "put",
                      "side": "short", "qty": 1}],
            "range_min": 0.0, "range_max": 0.0, "range_pct": 0.05}
    sub = bus.subscribe("events:options:calc_result")
    handlers.handle_command(bus, Command(type="calc_compute", args=args))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["args"] == args  # full params dict splatted into compute.calc_compute
    env = bus.cache_get("cache:options:calc_result")
    assert env is not None
    assert env.payload == result
    assert msg is not None and msg.get("version") == env.version


def test_calc_iv_command_caches_implied_iv(monkeypatch):
    bus = Bus(fake=True)
    seen = {"args": None}

    def _rec(spot, strike, option_type, mark, expiry, rate=0.045):
        seen["args"] = (spot, strike, option_type, mark, expiry, rate)
        return {"iv": 38.3, "strike": strike, "option_type": option_type,
                "mark": mark, "T": 0.0003, "error": None}

    monkeypatch.setattr(handlers.compute, "calc_iv", _rec)

    args = {"spot": 718.82, "strike": 725.0, "option_type": "call",
            "mark": 0.19, "expiry": "2026-06-23", "rate": 0.045}
    sub = bus.subscribe("events:options:calc_iv")
    handlers.handle_command(bus, Command(type="calc_iv", args=args))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["args"] == (718.82, 725.0, "call", 0.19, "2026-06-23", 0.045)
    env = bus.cache_get("cache:options:calc_iv")
    assert env is not None and env.payload["iv"] == 38.3
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


def test_collect_gex_history_calls_compute(monkeypatch):
    """The handler delegates to compute.collect_gex_snapshots (a pure write to
    the on-disk history store; no Redis cache view to publish)."""
    called = {"v": False}
    monkeypatch.setattr(handlers.compute, "collect_gex_snapshots",
                        lambda: called.__setitem__("v", True))
    handlers.collect_gex_history(bus=None)
    assert called["v"] is True


def test_publish_gex_status_caches_and_publishes(monkeypatch):
    """publish_gex_status caches compute.gex_status_view() under
    cache:options:gex_status and publishes a version event."""
    bus = Bus(fake=True)
    sentinel = {"status_label": "OK", "status_color": "green",
                "last_scan": "10:00 AM", "next_scan": "10:05 AM",
                "age_seconds": 120}
    monkeypatch.setattr(handlers.compute, "gex_status_view", lambda: sentinel)

    sub = bus.subscribe("events:options:gex_status")
    handlers.publish_gex_status(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:gex_status")
    assert env is not None
    assert env.payload == sentinel
    assert msg is not None and msg.get("version") == env.version


def test_publish_gex_status_skips_unchanged(monkeypatch):
    """An identical per-tick GEX-status view must NOT bump the version."""
    bus = Bus(fake=True)
    sentinel = {"status_label": "OK", "next_scan": "10:05 AM"}
    monkeypatch.setattr(handlers.compute, "gex_status_view", lambda: sentinel)
    handlers.publish_gex_status(bus)
    v1 = bus.cache_get("cache:options:gex_status").version
    handlers.publish_gex_status(bus)  # identical -> skip
    assert bus.cache_get("cache:options:gex_status").version == v1


def test_publish_gamma_symbols_caches_and_publishes(monkeypatch):
    """publish_gamma_symbols caches cache:options:gamma_symbols ({"symbols":[...]})
    and publishes a version event."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "gamma_symbol_options",
                        lambda: ["$SPX", "SPY", "NVDA"])
    sub = bus.subscribe("events:options:gamma_symbols")
    handlers.publish_gamma_symbols(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:gamma_symbols")
    assert env is not None
    assert env.payload == {"symbols": ["$SPX", "SPY", "NVDA"]}
    assert msg is not None and msg.get("version") == env.version


def test_handle_command_sim_replay(monkeypatch):
    """sim_replay dispatches compute.sim_replay (forwarding lookback) -> caches +
    publishes the view."""
    bus = Bus(fake=True)
    seen = {}

    def _fake(symbol, expiry, kind, strike, direction, lookback="auto", legs=None):
        seen["lookback"] = lookback
        seen["legs"] = legs
        return {"spot": 1.0, "x": [0], "prices": [1.0]}

    monkeypatch.setattr(handlers.compute, "sim_replay", _fake)
    sub = bus.subscribe(handlers.EVENT_SIM_REPLAY)
    handlers.handle_command(bus, Command(type="sim_replay", args={
        "symbol": "SPY", "expiry": "2026-06-26", "kind": "call",
        "strike": 450.0, "direction": "buy", "lookback": "5m_3d"}))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get(handlers.CACHE_SIM_REPLAY)
    assert env is not None
    assert env.payload["spot"] == 1.0
    assert seen["lookback"] == "5m_3d"
    assert seen["legs"] is None
    assert msg is not None and msg.get("version") == env.version
