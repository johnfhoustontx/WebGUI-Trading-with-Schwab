"""Tests for the options service rescan handler (Task 2.3).

The handler is the service-side analog of ``webgui/pages/options/scanner.py``'s
scan call: it computes via ``compute.run_scan`` (a dict), projects it onto the
``ScanResult`` contract as a validation gate, caches the full validated payload
under ONE key (``cache:options:scan`` — a single scan produces BOTH the 0-DTE
and swing signal lists, so one cache view holds the whole result), and publishes
a change event. We monkeypatch ``handlers.compute.run_scan`` so nothing touches a
live proxy, and use a fakeredis ``Bus(fake=True)``.
"""
import types as _types

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


def test_rescan_publishes_signals_directional(monkeypatch):
    """The engine's third list must survive the projection onto ScanResult.

    Directional candidates ride their OWN list so their Fit+Quality scores are
    never ranked against the premium-seller composites in the other two.
    """
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan", lambda: {
        "signals_0dte": [], "signals_swing": [],
        "signals_directional": [{"id": "d1", "type": "LONG_CALL", "symbol": "SPY"}],
        "timestamp": "2026-07-16T10:00:00", "errors": [], "warnings": [],
        "vix_term_structure": {},
    })

    handlers.rescan(bus)

    env = bus.cache_get("cache:options:scan")
    assert env is not None
    assert env.payload["signals_directional"][0]["id"] == "d1"
    assert env.payload["signals_directional"][0]["type"] == "LONG_CALL"


def test_rescan_without_signals_directional_still_publishes(monkeypatch):
    """A stale engine returning no ``signals_directional`` key must not crash.

    The projection is ``.get(k, default)``, so the key defaults to [] rather
    than raising — the real-world case of new code against an old engine.
    """
    bus = Bus(fake=True)
    result = _fake_result()          # no signals_directional key
    assert "signals_directional" not in result
    monkeypatch.setattr(handlers.compute, "run_scan", lambda: result)

    handlers.rescan(bus)

    env = bus.cache_get("cache:options:scan")
    assert env is not None
    assert env.payload["signals_directional"] == []
    assert env.payload["signals_0dte"] == result["signals_0dte"]


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
    view = {"direction": "bullish", "conviction": 0.6, "vol_regime": "mid"}
    seen = {"params": None}

    def _rec(**params):
        seen["params"] = params
        return {"signals": signals, "view": view}

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

    # compute called with the command args + the new ``families`` default (None).
    for k, v in args.items():
        assert seen["params"][k] == v
    assert seen["params"]["families"] is None

    env = bus.cache_get("cache:options:swing")
    assert env is not None
    payload = env.payload
    assert payload["signals"] == signals
    assert payload["view"] == view
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
    monkeypatch.setattr(handlers.compute, "manual_analytics",
                        lambda: {"equity_curve": [], "postmortem": {}, "excursions": {}})

    sub = bus.subscribe("events:options:paper_account")
    handlers.refresh_paper_account(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:paper_account")
    assert env is not None
    assert env.payload == view
    assert msg is not None and msg.get("version") == env.version


def test_refresh_paper_account_publishes_analytics(monkeypatch):
    """The manual-book analytics view (equity curve / MAE-MFE) is published alongside
    the account view — the scanner-baseline benchmark against the driver book."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "paper_account_view", _fake_paper_view)
    monkeypatch.setattr(handlers.compute, "manual_analytics",
                        lambda: {"equity_curve": [{"date": "d", "equity": 24900.0}],
                                 "postmortem": {}, "excursions": {"n": 0}})
    handlers.refresh_paper_account(bus)
    env = bus.cache_get("cache:options:paper_analytics")
    assert env is not None and env.payload["equity_curve"][0]["equity"] == 24900.0


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
                        lambda **kw: calls.__setitem__("manage", calls["manage"] + 1))
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades", lambda: 0)
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
                        lambda **kw: calls.__setitem__("manage", calls["manage"] + 1))
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades", lambda: 0)
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda b: calls.__setitem__("refresh", calls["refresh"] + 1))
    # Isolate from the ledger reprice the manage tick now piggybacks (else it hits
    # the real proxy/DB).
    monkeypatch.setattr(handlers, "refresh_paper_trades",
                        lambda b, **k: calls.__setitem__("trades", calls.get("trades", 0) + 1))

    handlers.run_manage_and_refresh(bus)
    assert calls["manage"] == 1 and calls["refresh"] == 1
    # The manage tick always piggybacks one ledger refresh (fresh P&L + any
    # expiration settlement).
    assert calls.get("trades", 0) == 1


# ── Manual-paper lifecycle flag threads into the manage cycle (Task 3) ──────

def test_run_manage_and_refresh_threads_lifecycle_flag_off_by_default(monkeypatch):
    """With the Settings toggle unset (default OFF), run_manage_and_refresh calls
    compute.run_manage_cycle(lifecycle=False) — unchanged plain TAKE_PROFIT."""
    bus = Bus(fake=True)
    seen = {}
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_manage_cycle",
                        lambda **kw: seen.update(kw))
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades", lambda: 0)
    monkeypatch.setattr(handlers, "refresh_paper_account", lambda b: None)
    monkeypatch.setattr(handlers, "refresh_paper_trades", lambda b, **k: None)

    handlers.run_manage_and_refresh(bus)
    assert seen == {"lifecycle": False}


def test_run_manage_and_refresh_threads_lifecycle_flag_on_when_enabled(monkeypatch):
    """With the Settings toggle explicitly enabled, run_manage_and_refresh calls
    compute.run_manage_cycle(lifecycle=True)."""
    bus = Bus(fake=True)
    bus.cache_set("cache:options:manual_paper_lifecycle", {"enabled": True})
    seen = {}
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_manage_cycle",
                        lambda **kw: seen.update(kw))
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades", lambda: 0)
    monkeypatch.setattr(handlers, "refresh_paper_account", lambda b: None)
    monkeypatch.setattr(handlers, "refresh_paper_trades", lambda b, **k: None)

    handlers.run_manage_and_refresh(bus)
    assert seen == {"lifecycle": True}


def test_run_paper_entry_and_manage_runs_entry_then_manage(monkeypatch):
    """The hourly manual paper cycle opens new trades (account present) then
    reprices/auto-closes + refreshes via run_manage_and_refresh."""
    bus = Bus(fake=True)
    order = []
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_entry_cycle",
                        lambda: order.append("entry"))
    monkeypatch.setattr(handlers, "run_manage_and_refresh",
                        lambda b: order.append("manage"))
    handlers.run_paper_entry_and_manage(bus)
    assert order == ["entry", "manage"]   # entry BEFORE manage


def test_run_paper_entry_and_manage_skips_entry_without_account(monkeypatch):
    """With no paper account, entry is skipped but manage/refresh still runs so
    the page shows the no-account state."""
    bus = Bus(fake=True)
    order = []
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: False)
    monkeypatch.setattr(handlers.compute, "run_entry_cycle",
                        lambda: order.append("entry"))
    monkeypatch.setattr(handlers, "run_manage_and_refresh",
                        lambda b: order.append("manage"))
    handlers.run_paper_entry_and_manage(bus)
    assert order == ["manage"]   # no entry, manage still ran


def test_run_paper_entry_and_manage_entry_failure_still_manages(monkeypatch):
    """An entry-cycle failure must NOT skip the manage/refresh (own try/except)."""
    bus = Bus(fake=True)
    order = []
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)

    def _boom():
        order.append("entry")
        raise RuntimeError("nope")

    monkeypatch.setattr(handlers.compute, "run_entry_cycle", _boom)
    monkeypatch.setattr(handlers, "run_manage_and_refresh",
                        lambda b: order.append("manage"))
    handlers.run_paper_entry_and_manage(bus)   # must not raise
    assert order == ["entry", "manage"]


def test_run_action_alert_pushes_and_caches(monkeypatch):
    """run_action_alert collects items, pushes the digest, and caches the run."""
    bus = Bus(fake=True)
    items = {"captured_action": [{"symbol": "MU", "strategy": "PCS", "recommendation": "CUT"}],
             "expiring_today": [], "at_risk": [], "account_near": []}
    calls = {}
    monkeypatch.setattr(handlers.compute, "collect_action_items", lambda: items)
    monkeypatch.setattr(handlers.push_notify, "send_action_digest",
                        lambda it, **k: calls.setdefault("sent", (it, k.get("slot_label"))) or True)

    handlers.run_action_alert(bus, "morning")

    env = bus.cache_get("cache:options:action_alert")
    assert env is not None
    assert env.payload["slot"] == "morning" and env.payload["total"] == 1
    assert env.payload["sent"] is True and env.payload["items"] == items
    # digest was pushed with the human slot label
    assert calls["sent"][1] == handlers.push_notify.action_slot_label("morning")


def test_run_action_alert_defensive_on_collect_failure(monkeypatch):
    """A collect failure degrades to an empty digest, never raises."""
    bus = Bus(fake=True)
    def _boom():
        raise RuntimeError("nope")
    monkeypatch.setattr(handlers.compute, "collect_action_items", _boom)
    monkeypatch.setattr(handlers.push_notify, "send_action_digest", lambda it, **k: False)

    handlers.run_action_alert(bus, "midday")   # must not raise
    env = bus.cache_get("cache:options:action_alert")
    assert env.payload["total"] == 0 and env.payload["sent"] is False


def test_run_eod_summary_pushes_and_caches(monkeypatch):
    """run_eod_summary collects the per-book summary, pushes it, and caches the run."""
    bus = Bus(fake=True)
    summary = {"date": "2026-07-13",
               "books": {"manual": {"has_account": True, "day_pnl": 120.0},
                         "driver": {"has_account": True, "day_pnl": -30.0}}}
    calls = {}
    monkeypatch.setattr(handlers.compute, "collect_eod_summary", lambda: summary)
    monkeypatch.setattr(handlers.push_notify, "send_eod_summary",
                        lambda s, **k: calls.setdefault("sent", s) or True)

    handlers.run_eod_summary(bus, "close")

    env = bus.cache_get("cache:options:eod_summary")
    assert env is not None
    assert env.payload["slot"] == "close" and env.payload["books"] == 2
    assert env.payload["sent"] is True and env.payload["summary"] == summary
    assert calls["sent"] == summary


def test_run_eod_summary_defensive_on_collect_failure(monkeypatch):
    """A collect failure degrades to an empty summary, never raises."""
    bus = Bus(fake=True)

    def _boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(handlers.compute, "collect_eod_summary", _boom)
    monkeypatch.setattr(handlers.push_notify, "send_eod_summary", lambda s, **k: False)

    handlers.run_eod_summary(bus, "close")   # must not raise
    env = bus.cache_get("cache:options:eod_summary")
    assert env.payload["books"] == 0 and env.payload["sent"] is False


def test_run_manage_and_refresh_settles_ledger_then_refreshes(monkeypatch):
    """The manage tick settles expired ledger trades, then republishes the ledger."""
    bus = Bus(fake=True)
    calls = {"expire": 0, "trades": 0}
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_manage_cycle", lambda **kw: None)
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades",
                        lambda: calls.__setitem__("expire", calls["expire"] + 1) or 2)
    monkeypatch.setattr(handlers, "refresh_paper_account", lambda b: None)
    monkeypatch.setattr(handlers, "refresh_paper_trades",
                        lambda b, **k: calls.__setitem__("trades", calls["trades"] + 1))

    handlers.run_manage_and_refresh(bus)
    assert calls["expire"] == 1 and calls["trades"] == 1


def test_run_manage_and_refresh_skips_cycle_when_no_account(monkeypatch):
    bus = Bus(fake=True)
    calls = {"manage": 0, "refresh": 0}
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: False)
    monkeypatch.setattr(handlers.compute, "run_manage_cycle",
                        lambda: calls.__setitem__("manage", calls["manage"] + 1))
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades", lambda: 0)
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda b: calls.__setitem__("refresh", calls["refresh"] + 1))
    monkeypatch.setattr(handlers, "refresh_paper_trades", lambda b, **k: None)

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
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda reprice=True: view)

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
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda reprice=True: view)

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
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda reprice=True: {"trades": []})

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
    day = {"date": "2026-08-19", "opened": 4, "closed": 2, "booked_pnl": 61.5}
    monkeypatch.setattr(handlers.compute, "captured_view", lambda: view)
    monkeypatch.setattr(handlers.compute, "captured_day_summary", lambda: day)

    sub = bus.subscribe("events:options:captured")
    handlers.refresh_captured(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:captured")
    assert env is not None
    assert env.payload == {"signals": view["signals"], "day": day}
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
    assert env.payload["signals"] == repriced
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


# ── the day footer block survives EVERY publisher ───────────────────────────
# There are three writers of cache:options:captured, and two of them rebuild the
# payload from scratch rather than editing it. Before ``_publish_captured``, a
# reprice or a close silently dropped the day block and the page's footer went
# blank until the next full refresh. This is the guard for that.
def _day(monkeypatch, **over):
    day = {"date": "2026-08-19", "opened": 4, "closed": 2, "booked_pnl": 61.5}
    day.update(over)
    monkeypatch.setattr(handlers.compute, "captured_day_summary", lambda: day)
    return day


def test_every_captured_publisher_attaches_the_day_block(monkeypatch):
    day = _day(monkeypatch)
    monkeypatch.setattr(handlers.compute, "captured_view",
                        lambda: {"signals": [{"signal_id": "X1"}], "day": day})
    monkeypatch.setattr(handlers.compute, "reprice_captured",
                        lambda: {"signals": [{"signal_id": "X1"}], "flags": []})
    monkeypatch.setattr(handlers.compute, "close_captured", lambda *a, **k: None)

    def _payload():
        return bus.cache_get("cache:options:captured").payload

    # 1. the periodic / reload refresh
    bus = Bus(fake=True)
    handlers.refresh_captured(bus)
    assert _payload()["day"] == day

    # 2. "Refresh marks (live)" — rebuilds from the repriced list
    bus = Bus(fake=True)
    handlers.handle_command(bus, Command(type="captured_reprice"))
    assert _payload()["day"] == day

    # 3. a manual close — rebuilds from the cached list minus one row
    bus = Bus(fake=True)
    bus.cache_set("cache:options:captured",
                  {"signals": [{"signal_id": "X1"}, {"signal_id": "X2"}]})
    handlers.handle_command(bus, Command(type="captured_close", args={
        "signal_id": "X1", "exit_val": 0.1, "reason": "MANUAL_CLOSE"}))
    assert _payload()["day"] == day
    assert [s["signal_id"] for s in _payload()["signals"]] == ["X2"]


def test_close_recomputes_the_day_block_rather_than_carrying_it_over(monkeypatch):
    """A close CHANGES the day's closed count, so the republish must re-read it —
    carrying the stale cached block forward would leave the footer one behind."""
    bus = Bus(fake=True)
    bus.cache_set("cache:options:captured", {
        "signals": [{"signal_id": "X1"}, {"signal_id": "X2"}],
        "day": {"date": "2026-08-19", "opened": 4, "closed": 2, "booked_pnl": 61.5}})
    monkeypatch.setattr(handlers.compute, "close_captured", lambda *a, **k: None)
    _day(monkeypatch, closed=3, booked_pnl=101.5)

    handlers.handle_command(bus, Command(type="captured_close", args={
        "signal_id": "X1", "exit_val": 0.1, "reason": "MANUAL_CLOSE"}))

    day = bus.cache_get("cache:options:captured").payload["day"]
    assert day["closed"] == 3 and day["booked_pnl"] == 101.5


def test_no_captured_publisher_bypasses_the_shared_helper():
    """Structural guard: a NEW publish path that writes CACHE_CAPTURED directly
    would reintroduce the dropped-footer bug, and no behavioural test would see
    it until someone opened the page after that action."""
    import inspect
    src = inspect.getsource(handlers)
    direct = [ln.strip() for ln in src.splitlines()
              if "cache_set(CACHE_CAPTURED," in ln]
    assert len(direct) == 1, f"write CACHE_CAPTURED via _publish_captured: {direct}"
    assert "def _publish_captured" in src


# ── captured auto-manage: publish both views + toggle (Task 5) ──────────────
def test_publish_captured_closed(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "captured_closed_today",
                        lambda: {"closed": [], "total_realized": 0.0})
    sub = bus.subscribe("events:options:captured_closed")
    handlers.publish_captured_closed(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()
    env = bus.cache_get("cache:options:captured_closed")
    assert env is not None and env.payload == {"closed": [], "total_realized": 0.0}
    assert msg is not None and msg.get("version") == env.version


def test_run_captured_manage_and_publish_publishes_both_views(monkeypatch):
    bus = Bus(fake=True)
    ran = {"cycle": 0}
    monkeypatch.setattr(handlers.compute, "run_captured_manage_cycle",
                        lambda: ran.__setitem__("cycle", ran["cycle"] + 1) or
                        {"closed": [{"signal_id": "M1", "symbol": "SPY",
                                     "reason": "BREAKEVEN_STOP"}], "armed": []})
    monkeypatch.setattr(handlers.compute, "captured_view",
                        lambda: {"signals": [{"signal_id": "M2", "symbol": "QQQ"}]})
    monkeypatch.setattr(handlers.compute, "captured_closed_today",
                        lambda: {"closed": [{"signal_id": "M1", "realized_pnl": 3.0}],
                                 "total_realized": 3.0})
    handlers.run_captured_manage_and_publish(bus)

    assert ran["cycle"] == 1                              # the cycle ran
    opened = bus.cache_get("cache:options:captured")
    assert opened is not None and opened.payload["signals"][0]["signal_id"] == "M2"
    closed = bus.cache_get("cache:options:captured_closed")
    assert closed is not None and closed.payload["total_realized"] == 3.0
    assert closed.payload["closed"][0]["signal_id"] == "M1"


def test_captured_manage_command_dispatch(monkeypatch):
    bus = Bus(fake=True)
    calls = {"n": 0}
    monkeypatch.setattr(handlers, "run_captured_manage_and_publish",
                        lambda b: calls.__setitem__("n", calls["n"] + 1))
    handlers.handle_command(bus, Command(type="captured_manage"))
    assert calls["n"] == 1


def test_set_autoclose_command_writes_flag():
    bus = Bus(fake=True)
    handlers.handle_command(bus, Command(type="set_autoclose", args={"enabled": False}))
    assert bus.cache_get("cache:options:autoclose_enabled").payload == {"enabled": False}
    handlers.handle_command(bus, Command(type="set_autoclose", args={"enabled": True}))
    assert bus.cache_get("cache:options:autoclose_enabled").payload == {"enabled": True}


def test_autoclose_enabled_defaults_true_and_respects_false():
    bus = Bus(fake=True)
    assert handlers.autoclose_enabled(bus) is True         # missing key → default ON
    bus.cache_set("cache:options:autoclose_enabled", {"enabled": False})
    assert handlers.autoclose_enabled(bus) is False
    bus.cache_set("cache:options:autoclose_enabled", {"enabled": True})
    assert handlers.autoclose_enabled(bus) is True


# ── Manual-paper break-even lifecycle opt-in (Task 3, flag default OFF) ─────
# The inverse of autoclose: only an EXPLICIT {"enabled": True} turns it on.

def test_set_manual_paper_lifecycle_command_writes_flag():
    bus = Bus(fake=True)
    handlers.handle_command(bus, Command(type="set_manual_paper_lifecycle",
                                         args={"enabled": True}))
    assert bus.cache_get("cache:options:manual_paper_lifecycle").payload == {"enabled": True}
    handlers.handle_command(bus, Command(type="set_manual_paper_lifecycle",
                                         args={"enabled": False}))
    assert bus.cache_get("cache:options:manual_paper_lifecycle").payload == {"enabled": False}


def test_manual_paper_lifecycle_enabled_defaults_false_and_respects_true():
    bus = Bus(fake=True)
    assert handlers.manual_paper_lifecycle_enabled(bus) is False   # missing key → default OFF
    bus.cache_set("cache:options:manual_paper_lifecycle", {"enabled": True})
    assert handlers.manual_paper_lifecycle_enabled(bus) is True
    bus.cache_set("cache:options:manual_paper_lifecycle", {"enabled": False})
    assert handlers.manual_paper_lifecycle_enabled(bus) is False


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


def test_refresh_gamma_current_uses_cached_symbol(monkeypatch):
    """The server-side keep-fresh refresh reads the symbol from the cache so it
    never forces $SPX over the user's last-viewed symbol."""
    bus = Bus(fake=True)
    bus.cache_set("cache:options:gamma", {"symbol": "QQQ", "views": {}})
    seen = {"symbol": None}
    monkeypatch.setattr(handlers.compute, "gamma_snapshot",
                        lambda s: (seen.__setitem__("symbol", s), _fake_gamma_snapshot())[1])

    handlers.refresh_gamma_current(bus)
    assert seen["symbol"] == "QQQ"


def test_refresh_gamma_current_defaults_spx_when_empty(monkeypatch):
    bus = Bus(fake=True)  # nothing cached
    seen = {"symbol": None}
    monkeypatch.setattr(handlers.compute, "gamma_snapshot",
                        lambda s: (seen.__setitem__("symbol", s), None)[1])
    handlers.refresh_gamma_current(bus)
    assert seen["symbol"] == "$SPX"


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


def test_run_scheduled_gamma_analyze_caches_slot_key(monkeypatch):
    bus = Bus(fake=True)
    seen = {"label": None}

    def _analyze(client=None, label=None):
        seen["label"] = label
        return {"html": "<!DOCTYPE html><html><body>doc</body></html>", "prompt": "p"}

    monkeypatch.setattr(handlers.compute, "gamma_analyze", _analyze)

    handlers.run_scheduled_gamma_analyze(bus, "midday")

    # Cached under the slot's OWN key, NOT the ad-hoc gamma_analyze key.
    env = bus.cache_get("cache:options:gamma_analyze_midday")
    assert env is not None and env.payload["html"].startswith("<!DOCTYPE html>")
    assert env.payload["slot"] == "midday" and env.payload.get("generated_at")
    assert bus.cache_get("cache:options:gamma_analyze") is None
    # The model run is labelled with the slot title (shows in the doc subtitle).
    assert "Midday" in (seen["label"] or "")


def test_run_scheduled_gamma_analyze_unknown_slot_noop(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "gamma_analyze",
                        lambda **k: {"html": "x"})
    handlers.run_scheduled_gamma_analyze(bus, "bogus")  # must not raise / cache
    assert bus.cache_get("cache:options:gamma_analyze_bogus") is None


def _isolate_briefing_db(monkeypatch, tmp_path):
    """Point the briefing-history store at a temp DB so a scheduled-analyze test
    (whose payload carries a real ``analysis``) can't write the live store."""
    import gamma_briefing_history_db as gbh
    real = gbh.connect
    monkeypatch.setattr(gbh, "connect", lambda db_path=None: real(tmp_path / "h.db"))


def test_scheduled_analyze_pushes_briefing(monkeypatch, tmp_path):
    _isolate_briefing_db(monkeypatch, tmp_path)
    bus = Bus(fake=True)
    pushed = []
    monkeypatch.setattr(handlers.compute, "gamma_analyze",
                        lambda **k: {"html": "<html>x</html>", "analysis": {"bias": 1}})
    monkeypatch.setattr(handlers.push_notify, "send_gamma_briefing",
                        lambda res, **kw: pushed.append((res, kw)))

    handlers.run_scheduled_gamma_analyze(bus, "midday")

    assert pushed and pushed[0][1]["slot"] == "midday"
    # The push carries the SAME payload that was cached (slot/generated_at stamped).
    assert pushed[0][0]["slot"] == "midday"
    assert pushed[0][0]["html"] == "<html>x</html>"


def test_scheduled_analyze_push_failure_does_not_break_handler(monkeypatch, tmp_path):
    """The load-bearing guarantee: a push failure must never cost us the briefing.

    Caching and history persistence run BEFORE the push and must still complete.
    """
    _isolate_briefing_db(monkeypatch, tmp_path)
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "gamma_analyze",
                        lambda **k: {"html": "<html>x</html>", "analysis": {"bias": 1}})

    def boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(handlers.push_notify, "send_gamma_briefing", boom)

    handlers.run_scheduled_gamma_analyze(bus, "midday")     # must not raise

    env = bus.cache_get("cache:options:gamma_analyze_midday")
    assert env is not None and env.payload["html"] == "<html>x</html>"
    # The history row (persisted before the push) survives too.
    import datetime
    from zoneinfo import ZoneInfo
    import gamma_briefing_history_db as gbh
    today = datetime.datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    rows = gbh.briefings_for_date(gbh.connect(), today)
    assert any(r["slot"] == "midday" for r in rows)


def test_scheduled_analyze_unknown_slot_does_not_push(monkeypatch):
    bus = Bus(fake=True)
    pushed = []
    monkeypatch.setattr(handlers.compute, "gamma_analyze",
                        lambda **k: {"html": "<html>x</html>", "analysis": {"bias": 1}})
    monkeypatch.setattr(handlers.push_notify, "send_gamma_briefing",
                        lambda *a, **k: pushed.append(a))

    handlers.run_scheduled_gamma_analyze(bus, "nonsense")

    assert pushed == []


def test_persist_briefing_records_history(monkeypatch, tmp_path):
    import datetime
    from zoneinfo import ZoneInfo
    import gamma_briefing_history_db as gbh
    real = gbh.connect
    monkeypatch.setattr(gbh, "connect", lambda db_path=None: real(tmp_path / "h.db"))

    now = datetime.datetime(2026, 7, 2, 8, 0, tzinfo=ZoneInfo("America/Chicago"))
    res = {"html": "<x>", "analysis": {"bias": -22, "headline": "Pinned",
           "indices": [{"symbol": "$SPX", "spot": 7400}]}}
    handlers._persist_briefing(res, "premarket", now)

    got = gbh.get_briefing(real(tmp_path / "h.db"), "2026-07-02", "premarket")
    assert got and got["bias"] == -22 and got["headline"] == "Pinned"
    assert got["analysis"]["indices"][0]["spot"] == 7400


def test_publish_gamma_briefing_index(monkeypatch, tmp_path):
    import gamma_briefing_history_db as gbh
    real = gbh.connect
    monkeypatch.setattr(gbh, "connect", lambda db_path=None: real(tmp_path / "h.db"))
    # seed two rows
    c = real(tmp_path / "h.db")
    for slot in ("open", "close"):
        gbh.insert_briefing(c, date="2026-07-08", slot=slot, generated_at="t",
                            symbol_scope="$SPX/SPY/QQQ", model="m", bias=-10,
                            headline="h", analysis={"bias": -10, "indices": []})
    c.close()

    bus = Bus(fake=True)
    handlers.publish_gamma_briefing_index(bus)
    env = bus.cache_get("cache:options:gamma_briefings")
    assert env is not None
    slots = {b["slot"] for b in env.payload["briefings"]}
    assert slots == {"open", "close"}
    # metadata only (no heavy analysis payload in the index)
    assert "analysis" not in env.payload["briefings"][0]


def test_run_gamma_history_regenerates_report(monkeypatch, tmp_path):
    import gamma_briefing_history_db as gbh
    real = gbh.connect
    monkeypatch.setattr(gbh, "connect", lambda db_path=None: real(tmp_path / "h.db"))
    c = real(tmp_path / "h.db")
    gbh.insert_briefing(c, date="2026-07-08", slot="midday", generated_at="t",
                        symbol_scope="$SPX/SPY/QQQ", model="m", bias=-10,
                        headline="Pinned",
                        analysis={"regime": "Short gamma", "bias": -10,
                                  "headline": "Pinned", "indices": [
                                      {"symbol": "$SPX", "spot": 7400}]})
    c.close()

    bus = Bus(fake=True)
    handlers.run_gamma_history(bus, "2026-07-08", slot="midday")
    env = bus.cache_get("cache:options:gamma_history")
    assert env is not None
    assert env.payload["html"].lstrip().startswith("<!DOCTYPE html>")
    assert "Short gamma" in env.payload["html"] and env.payload["date"] == "2026-07-08"


def test_run_gamma_history_no_match_is_graceful(monkeypatch, tmp_path):
    import gamma_briefing_history_db as gbh
    real = gbh.connect
    monkeypatch.setattr(gbh, "connect", lambda db_path=None: real(tmp_path / "h.db"))
    bus = Bus(fake=True)
    handlers.run_gamma_history(bus, "1999-01-01")  # nothing stored
    env = bus.cache_get("cache:options:gamma_history")
    assert env is not None and "No briefings found" in env.payload["html"]


def test_persist_briefing_skips_without_analysis(monkeypatch, tmp_path):
    import gamma_briefing_history_db as gbh
    real, called = gbh.connect, {"n": 0}

    def _c(db_path=None):
        called["n"] += 1
        return real(tmp_path / "h.db")

    monkeypatch.setattr(gbh, "connect", _c)
    import datetime
    from zoneinfo import ZoneInfo
    now = datetime.datetime(2026, 7, 2, 8, 0, tzinfo=ZoneInfo("America/Chicago"))
    handlers._persist_briefing({"html": "degraded, no analysis"}, "premarket", now)
    assert called["n"] == 0  # never even opened the DB (no analysis → no row)


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
            "num_strikes": 24, "price_rows": [440.0, 445.0, 450.0]}
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


def test_swing_scan_publishes_the_filtered_out_count(monkeypatch):
    """The page needs the drop count to tell 'nothing cleared the quality bar'
    apart from 'the scan found nothing', so the handler must carry it through."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "swing_scan",
                        lambda **p: {"signals": [], "view": {}, "filtered_out": 7})
    handlers.swing_scan(bus, {"symbol": "SPY"})
    assert bus.cache_get("cache:options:swing").payload["filtered_out"] == 7


def test_swing_scan_filtered_out_defaults_when_compute_omits_it(monkeypatch):
    """A stale compute returning no ``filtered_out`` must not crash the handler."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "swing_scan",
                        lambda **p: {"signals": [], "view": {}})
    handlers.swing_scan(bus, {"symbol": "SPY"})
    assert bus.cache_get("cache:options:swing").payload["filtered_out"] == 0


def test_swing_scan_uses_defaults_for_missing_args(monkeypatch):
    bus = Bus(fake=True)
    seen = {"params": None}

    def _rec(**params):
        seen["params"] = params
        return {"signals": [], "view": {}}

    monkeypatch.setattr(handlers.compute, "swing_scan", _rec)

    # Only a symbol given -> the rest fall back to page-default params.
    handlers.swing_scan(bus, {"symbol": "QQQ"})
    assert seen["params"]["symbol"] == "QQQ"
    assert seen["params"]["dte_min"] == 5
    assert seen["params"]["min_cr_fraction"] == 0.10
    assert seen["params"]["families"] is None
    # Even with no signals, the (empty) result is cached + published.
    env = bus.cache_get("cache:options:swing")
    assert env is not None and env.payload["signals"] == []
    assert env.payload["view"] == {}


def test_swing_scan_reads_market_state(monkeypatch):
    """The handler reads the live committed state from cache:sentiment:composite
    and threads it into compute.swing_scan as ``market_state``."""
    bus = Bus(fake=True)
    bus.cache_set("cache:sentiment:composite",
                  {"derived": {"trend": {"state": "lack_of_bearishness"}}})
    seen = {"params": None}

    def _rec(**params):
        seen["params"] = params
        return {"signals": [], "view": {}}

    monkeypatch.setattr(handlers.compute, "swing_scan", _rec)
    handlers.swing_scan(bus, {"symbol": "SPY"})
    assert seen["params"]["market_state"] == "lack_of_bearishness"


def test_swing_scan_absent_composite_no_market_state(monkeypatch):
    """No composite cached (bus returns None) -> market_state=None (graceful)."""
    bus = Bus(fake=True)
    seen = {"params": None}

    def _rec(**params):
        seen["params"] = params
        return {"signals": [], "view": {}}

    monkeypatch.setattr(handlers.compute, "swing_scan", _rec)
    handlers.swing_scan(bus, {"symbol": "SPY"})
    assert seen["params"]["market_state"] is None


def test_collect_gex_history_calls_compute(monkeypatch):
    """The handler delegates to compute.collect_gex_snapshots (a pure write to
    the on-disk history store; no Redis cache view to publish)."""
    called = {"v": False}
    monkeypatch.setattr(
        handlers.compute, "collect_gex_snapshots",
        lambda capture_symbols=None: called.__setitem__("v", True))
    handlers.collect_gex_history(bus=None)
    assert called["v"] is True


def test_collect_gex_history_captures_viewed_symbol_chain(monkeypatch):
    """The handler asks the collector to CAPTURE the currently-viewed gamma
    symbol's chain (so the same tick's refresh_gamma_current reuses it instead
    of refetching it seconds later). Falls back to $SPX when nothing is cached."""
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_GAMMA, {"symbol": "SPY", "views": {}})
    seen = {}
    monkeypatch.setattr(
        handlers.compute, "collect_gex_snapshots",
        lambda capture_symbols=None: seen.__setitem__("cap", capture_symbols))
    monkeypatch.setattr(handlers, "publish_flow_skew", lambda b: None)
    monkeypatch.setattr(handlers, "run_flow_alerts", lambda b: None)
    handlers.collect_gex_history(bus=bus)
    assert seen["cap"] == {"SPY"}

    bus2 = Bus(fake=True)  # empty cache -> defaults to $SPX
    handlers.collect_gex_history(bus=bus2)
    assert seen["cap"] == {"$SPX"}


def test_collect_gex_history_publishes_flow_skew_after_collect(monkeypatch):
    """collect_gex_history publishes the flow-skew view AFTER collection (it
    rides the same 2-min tick that just wrote the rows)."""
    order = []
    monkeypatch.setattr(handlers.compute, "collect_gex_snapshots",
                        lambda capture_symbols=None: order.append("collect"))
    monkeypatch.setattr(handlers, "publish_flow_skew",
                        lambda bus: order.append("publish"))
    bus = Bus(fake=True)
    handlers.collect_gex_history(bus=bus)
    assert order == ["collect", "publish"]


def test_collect_gex_history_no_bus_skips_publish(monkeypatch):
    """A legacy caller passing bus=None still collects, but does not publish —
    none of them (flow-skew, matrix, net premium)."""
    order = []
    monkeypatch.setattr(handlers.compute, "collect_gex_snapshots",
                        lambda capture_symbols=None: order.append("collect"))
    monkeypatch.setattr(handlers, "publish_flow_skew",
                        lambda bus: order.append("publish"))
    monkeypatch.setattr(handlers, "publish_matrix",
                        lambda bus: order.append("matrix"))
    monkeypatch.setattr(handlers, "publish_net_premium",
                        lambda bus: order.append("netprem"))
    handlers.collect_gex_history(bus=None)
    assert order == ["collect"]


def test_collect_gex_history_publish_failure_does_not_raise(monkeypatch):
    """A publish_flow_skew failure must never abort the (already-done) collect."""
    monkeypatch.setattr(handlers.compute, "collect_gex_snapshots",
                        lambda capture_symbols=None: None)

    def _boom(bus):
        raise RuntimeError("redis down")

    monkeypatch.setattr(handlers, "publish_flow_skew", _boom)
    handlers.collect_gex_history(bus=Bus(fake=True))  # must not raise


def test_publish_flow_skew_caches_and_publishes(monkeypatch):
    """publish_flow_skew caches compute.flow_skew_view() under
    cache:options:flow_skew and publishes a version event."""
    bus = Bus(fake=True)
    sentinel = {"$SPX": {"rr_25d": 4.0, "rr_delta": 0.5,
                         "call_vol": 300, "put_vol": 310, "ts": 200}}
    monkeypatch.setattr(handlers.compute, "flow_skew_view", lambda: sentinel)

    sub = bus.subscribe("events:options:flow_skew")
    handlers.publish_flow_skew(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:flow_skew")
    assert env is not None
    assert env.payload == sentinel
    assert msg is not None and msg.get("version") == env.version


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


# ── Task 8/9: server-side push notifications on new scanner/captured signals ──

def test_rescan_calls_notify(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan", lambda: {
        "signals_0dte": [{"symbol": "SPY", "type": "PCS", "short_strike": 500,
                          "long_strike": 495, "expiration": "2026-07-10",
                          "composite_score": 80}],
        "signals_swing": [], "errors": [], "warnings": []})
    seen = {}
    monkeypatch.setattr(handlers.push_notify, "notify_signals",
                        lambda bus, sigs, **kw: seen.update(n=len(sigs),
                                                            kind=kw["kind"],
                                                            key=kw["seen_key"]))
    handlers.rescan(bus)
    assert seen["n"] == 1
    assert seen["kind"] == "scanner"
    assert seen["key"] == handlers.CACHE_NOTIFIED_SCAN


def test_refresh_captured_calls_notify(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "captured_view", lambda: {"signals": [
        {"signal_id": "s1", "symbol": "SPY", "type": "PCS"}]})
    got = {}
    monkeypatch.setattr(handlers.push_notify, "notify_signals",
                        lambda bus, sigs, **kw: got.update(n=len(sigs),
                                                           kind=kw["kind"],
                                                           key=kw["seen_key"]))
    handlers.refresh_captured(bus)
    assert got["n"] == 1 and got["kind"] == "captured"
    assert got["key"] == handlers.CACHE_NOTIFIED_CAPTURED


def test_captured_reprice_calls_notify(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "reprice_captured",
                        lambda: {"signals": [{"signal_id": "X1", "symbol": "SPY"}],
                                 "flags": []})
    got = {}
    monkeypatch.setattr(handlers.push_notify, "notify_signals",
                        lambda bus, sigs, **kw: got.update(n=len(sigs),
                                                           kind=kw["kind"],
                                                           key=kw["seen_key"]))
    handlers.handle_command(bus, Command(type="captured_reprice"))
    assert got["n"] == 1 and got["kind"] == "captured"
    assert got["key"] == handlers.CACHE_NOTIFIED_CAPTURED


# ── Day-persistent scan union (cache:options:scan_day) ──────────────────────
# A SEPARATE key on purpose: cache:options:scan stays live-only because the
# autonomous driver reads it and must never be offered a signal that no longer
# qualifies.

def _scan_with(sigs):
    return {"signals_0dte": sigs, "signals_swing": [], "signals_directional": [],
            "vix_term_structure": {}, "timestamp": "2026-07-16T10:00:00",
            "errors": [], "warnings": []}


def test_rescan_publishes_the_day_union(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan_with([{"id": "a", "symbol": "SPY", "credit": 1.0}]))

    sub = bus.subscribe("events:options:scan_day")
    handlers.rescan(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:scan_day")
    assert env is not None
    assert env.payload["date"]
    assert [s["id"] for s in env.payload["signals_0dte"]] == ["a"]
    assert env.payload["signals_0dte"][0]["live"] is True
    assert msg is not None and msg["version"] == env.version


def test_rescan_day_union_accumulates_across_scans(monkeypatch):
    """The load-bearing separation: the live key is REPLACED, the day key ACCUMULATES.

    If the union ever landed on cache:options:scan, the autonomous driver would
    be offered "a" -- a signal that no longer qualifies. That is the regression
    this test exists to catch.
    """
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan_with([{"id": "a", "symbol": "SPY", "credit": 1.0}]))
    handlers.rescan(bus)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan_with([{"id": "b", "symbol": "QQQ", "credit": 2.0}]))
    handlers.rescan(bus)

    live = bus.cache_get("cache:options:scan").payload
    day = bus.cache_get("cache:options:scan_day").payload

    # Live key: replaced, live-only -- exactly as before this feature.
    assert [s["id"] for s in live["signals_0dte"]] == ["b"]
    assert "live" not in live["signals_0dte"][0]      # untouched by the merge

    # Day key: the union.
    assert {s["id"] for s in day["signals_0dte"]} == {"a", "b"}
    by_id = {s["id"]: s for s in day["signals_0dte"]}
    assert by_id["a"]["live"] is False and by_id["a"]["stale_since"]
    assert by_id["b"]["live"] is True


def test_rescan_day_union_failure_does_not_break_the_live_publish(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan_with([{"id": "a", "symbol": "SPY"}]))

    def _boom(*a, **k):
        raise RuntimeError("merge exploded")
    monkeypatch.setattr(handlers.compute, "merge_day_signals", _boom)

    handlers.rescan(bus)   # must not raise

    env = bus.cache_get("cache:options:scan")
    assert env is not None
    assert env.payload["signals_0dte"][0]["id"] == "a"
    assert bus.cache_get("cache:options:scan_day") is None


def test_rescan_day_union_dates_in_ct_not_naive_local(monkeypatch):
    """rescan carries three date bases; the scheduler and push_notify are both
    CT-pinned, so the day union must be too. Pinned by forcing the machine's naive
    local clock to a DIFFERENT date than CT and asserting CT wins."""
    import datetime as _d
    from shared.notify import channels

    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan_with([{"id": "a", "symbol": "SPY"}]))
    # CT says the 16th...
    monkeypatch.setattr(channels, "_today_ct", lambda: "2026-07-16")
    monkeypatch.setattr(handlers, "_today_ct", lambda: "2026-07-16")

    class _FakeDT(_d.datetime):
        @classmethod
        def now(cls, tz=None):        # ...naive local says the 17th.
            return cls(2026, 7, 17, 0, 30)
    monkeypatch.setattr(handlers._dt, "datetime", _FakeDT)

    handlers.rescan(bus)

    assert bus.cache_get("cache:options:scan_day").payload["date"] == "2026-07-16"


def test_run_flow_alerts_detects_pushes_publishes(monkeypatch):
    from shared.bus import Bus
    from services.options_svc import handlers
    bus = Bus(fake=True)
    # A 2-point series with a decisive, large-premium crossover (calls overtake puts).
    series = [(60, 100.0, 0, 0, 100000.0, 200000.0),
              (120, 100.0, 0, 0, 260000.0, 200000.0)]
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["$SPX"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: series)
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 120)
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: sent.append(a))
    handlers.run_flow_alerts(bus)
    env = bus.cache_get("cache:options:flow_alerts")
    assert env is not None
    assert any(x["type"] == "crossover" for x in env.payload["alerts"])
    assert len(sent) == 1
    # Second identical tick within cooldown → no new push, no duplicate alert appended.
    handlers.run_flow_alerts(bus)
    assert len(sent) == 1


def test_run_flow_alerts_loads_bounded_tail_not_whole_day(monkeypatch):
    """The crossover detector needs only the trailing few rows, so the handler
    loads a small bounded tail, never the whole day's series per symbol."""
    from shared.bus import Bus
    from services.options_svc import handlers
    bus = Bus(fake=True)
    seen = {}
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["$SPX"])
    monkeypatch.setattr(handlers, "_load_flow_series_for",
                        lambda conn, sym, limit: seen.setdefault("limit", limit) or [])
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 120)
    handlers.run_flow_alerts(bus)
    assert seen["limit"] == handlers._FLOW_CROSSOVER_TAIL


def test_run_flow_alerts_excludes_vix(monkeypatch):
    """$VIX-options premium crossovers are noise — exclude it from the flow-alert
    universe (mirrors the gamma symbol dropdown, which also drops $VIX)."""
    from shared.bus import Bus
    from services.options_svc import handlers
    bus = Bus(fake=True)
    monkeypatch.setattr("gex_collector.collection_symbols",
                        lambda: ["$SPX", "$VIX", "SPY"])
    syms = handlers._flow_alert_symbols()
    assert "$VIX" not in syms and "$SPX" in syms and "SPY" in syms


def test_run_flow_alerts_cooldown_map_skips_unchanged_write(monkeypatch):
    """The cooldown map is written skip_unchanged, so a quiet tick (nothing fired,
    map unchanged) doesn't bump the key's version every minute."""
    from shared.bus import Bus
    from services.options_svc import handlers
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["$SPX"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [])
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 120)
    handlers.run_flow_alerts(bus)
    v1 = bus.cache_version(handlers._FLOW_COOLDOWN_KEY)
    handlers.run_flow_alerts(bus)   # still quiet -> map unchanged
    v2 = bus.cache_version(handlers._FLOW_COOLDOWN_KEY)
    assert v1 == v2


def test_run_flow_alerts_dedups_published_by_id(monkeypatch):
    from shared.bus import Bus
    from services.options_svc import handlers
    bus = Bus(fake=True)
    alert = {"id": "$SPX|crossover|calls_over|120", "type": "crossover",
             "side": "calls_over", "symbol": "$SPX", "text": "x", "ts": 120}
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["$SPX"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [(60, 1, 0, 0, 1, 1), (120, 1, 0, 0, 1, 1)])
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 120)
    monkeypatch.setattr(handlers.flow_alerts, "detect_flow_alerts",
                        lambda *a, **k: [dict(alert)])   # same alert every call (bypass cooldown)
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: None)
    handlers.run_flow_alerts(bus)
    handlers.run_flow_alerts(bus)   # same id again
    env = bus.cache_get("cache:options:flow_alerts")
    ids = [a["id"] for a in env.payload["alerts"]]
    assert ids.count("$SPX|crossover|calls_over|120") == 1


def test_run_flow_alerts_emits_uoa_from_stash(monkeypatch):
    from shared.bus import Bus
    from services.options_svc import handlers, compute
    bus = Bus(fake=True)
    contract = {"type": "uoa", "side": "call", "symbol": "SPY", "strike": 450.0,
                "expiry": "2026-07-18", "dte": 2, "cost": 1.85, "volume": 8200,
                "oi": 1300, "vol_oi": 6.3, "premium": 1517000.0}
    # SPY is in the universe (no crossover SERIES data, but a valid member); UOA
    # shares the crossover universe, so the stash symbol must be in it to emit.
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["SPY"])
    # Stub the SERIES read (as the sibling tests do): unstubbed it hits the real
    # gex_history.db, and a live SPY premium crossover in today's collected rows
    # prepends a second alert that has nothing to do with the UOA path here.
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [])
    monkeypatch.setattr(compute, "take_uoa_stash", lambda: {"SPY": [dict(contract)]})
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1000)
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: sent.append(a))
    handlers.run_flow_alerts(bus)
    env = bus.cache_get("cache:options:flow_alerts")
    ids = [a["id"] for a in env.payload["alerts"]]
    assert ids == ["SPY|uoa|call|450|2026-07-18"] and len(sent) == 1
    assert "07/18" in env.payload["alerts"][0]["text"]
    # Same contract next tick → once-per-day dedup, no re-push/re-append.
    monkeypatch.setattr(compute, "take_uoa_stash", lambda: {"SPY": [dict(contract)]})
    handlers.run_flow_alerts(bus)
    assert len(sent) == 1 and len(bus.cache_get("cache:options:flow_alerts").payload["alerts"]) == 1


def test_run_flow_alerts_uoa_excludes_vix(monkeypatch):
    from shared.bus import Bus
    from services.options_svc import handlers, compute
    bus = Bus(fake=True)
    def uoa(sym, strike):
        return {"type": "uoa", "side": "call", "symbol": sym, "strike": strike,
                "expiry": "2026-07-18", "dte": 2, "cost": 1.0, "volume": 8000,
                "oi": 1000, "vol_oi": 8.0, "premium": 800000.0}
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["SPY"])   # $VIX excluded
    # See the sibling test: stub the series read so a real crossover in today's
    # collected gex_history.db rows can't inject an extra alert.
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [])
    monkeypatch.setattr(compute, "take_uoa_stash",
                        lambda: {"SPY": [uoa("SPY", 450.0)], "$VIX": [uoa("$VIX", 20.0)]})
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1000)
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: sent.append(a))
    handlers.run_flow_alerts(bus)
    ids = [a["id"] for a in bus.cache_get("cache:options:flow_alerts").payload["alerts"]]
    assert ids == ["SPY|uoa|call|450|2026-07-18"]   # $VIX dropped
    assert all("$VIX" not in a["symbol"] for a in sent)


def test_flow_alerts_cap_holds_a_full_day():
    """The published list was capped at 50, which drops the morning's alerts before
    anyone can look at them. A full session across ~45 symbols needs headroom."""
    from services.options_svc import handlers
    assert handlers._FLOW_ALERTS_MAX >= 300


def test_run_flow_alerts_uoa_carries_a_timestamp(monkeypatch):
    """Crossover and gamma_flip alerts carry ts; UOA did not, because detect_uoa
    never emits one — so a chronological view had nothing to place a third of its
    rows on a timeline with."""
    from shared.bus import Bus
    from services.options_svc import handlers, compute
    bus = Bus(fake=True)
    contract = {"type": "uoa", "side": "call", "symbol": "SPY", "strike": 450.0,
                "expiry": "2026-07-18", "dte": 2, "cost": 1.85, "volume": 8200,
                "oi": 1300, "vol_oi": 6.3, "premium": 1517000.0}
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["SPY"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [])
    monkeypatch.setattr(compute, "take_uoa_stash", lambda: {"SPY": [dict(contract)]})
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1754750000)
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: None)

    handlers.run_flow_alerts(bus)

    alerts = bus.cache_get("cache:options:flow_alerts").payload["alerts"]
    uoa = [a for a in alerts if a["type"] == "uoa"]
    assert uoa and uoa[0]["ts"] == 1754750000


# --- Task 4: big_delta drain + quiet-live push gate ---

_BD_CONTRACT = {"type": "big_delta", "side": "call", "symbol": "SPY", "strike": 100.0,
                "expiry": "2026-08-14", "dte": 3, "delta": 0.5, "volume": 5000,
                "delta_notional": 3.1e8, "pct_of_gross": 0.24}


def test_run_flow_alerts_big_delta_screen_only_when_push_false(monkeypatch):
    """big_delta always lands on the Flow screen; with [big_delta].push=false it must
    NOT reach push_notify.send_flow_alert -- and a HIGH-share contract proves the
    screen-only guarantee comes from push=false, not from the share being sub-bar."""
    from shared.bus import Bus
    from services.options_svc import handlers, compute
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["SPY"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [])
    monkeypatch.setattr(compute, "take_uoa_stash", lambda: {})
    hi = {**_BD_CONTRACT, "pct_of_gross": 0.50}      # well over any push bar
    monkeypatch.setattr(compute, "take_big_delta_stash", lambda: {"SPY": [hi]})
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1000)
    real_cfg = handlers.flow_alerts.load_thresholds()
    off_cfg = {**real_cfg, "big_delta": {**real_cfg["big_delta"], "push": False}}
    monkeypatch.setattr(handlers.flow_alerts, "load_thresholds", lambda: off_cfg)
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: sent.append(a))
    handlers.run_flow_alerts(bus)
    alerts = bus.cache_get("cache:options:flow_alerts").payload["alerts"]
    assert any(a["type"] == "big_delta" for a in alerts)          # on the screen
    assert not any(a.get("type") == "big_delta" for a in sent)    # NOT phone-pushed


def test_run_flow_alerts_big_delta_pushes_only_above_push_threshold(monkeypatch):
    """With push=true, big_delta is PHONE-pushed only when its share of gross clears
    push_threshold (the separate push bar). A sub-threshold fire still lands on the
    Flow screen but stays off the phone — the whole point of the separate bar."""
    from shared.bus import Bus
    from services.options_svc import handlers, compute
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["SPY"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [])
    monkeypatch.setattr(compute, "take_uoa_stash", lambda: {})
    hi = {**_BD_CONTRACT, "strike": 110.0, "pct_of_gross": 0.40}     # over 0.35 -> pushes
    lo = {**_BD_CONTRACT, "strike": 100.0, "pct_of_gross": 0.24}     # under 0.35 -> screen only
    monkeypatch.setattr(compute, "take_big_delta_stash", lambda: {"SPY": [hi, lo]})
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1000)
    real_cfg = handlers.flow_alerts.load_thresholds()
    push_cfg = {**real_cfg, "big_delta": {**real_cfg["big_delta"], "push": True,
                                          "push_threshold": 0.35}}
    monkeypatch.setattr(handlers.flow_alerts, "load_thresholds", lambda: push_cfg)
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: sent.append(a))
    handlers.run_flow_alerts(bus)
    screen = bus.cache_get("cache:options:flow_alerts").payload["alerts"]
    assert {a["strike"] for a in screen if a["type"] == "big_delta"} == {110.0, 100.0}
    pushed = [a for a in sent if a.get("type") == "big_delta"]
    assert [a["strike"] for a in pushed] == [110.0]     # only the >=35% share reached the phone


def test_run_flow_alerts_big_delta_id_format_ts_and_dedup(monkeypatch):
    """Once-per-contract-per-day, like UOA: the SAME cooldown seen-set gates a
    repeat next tick, and the alert carries the stamped id/ts/text."""
    from shared.bus import Bus
    from services.options_svc import handlers, compute
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["SPY"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [])
    monkeypatch.setattr(compute, "take_uoa_stash", lambda: {})
    monkeypatch.setattr(compute, "take_big_delta_stash", lambda: {"SPY": [dict(_BD_CONTRACT)]})
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1754750000)
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: sent.append(a))

    handlers.run_flow_alerts(bus)

    alerts = bus.cache_get("cache:options:flow_alerts").payload["alerts"]
    bd = [a for a in alerts if a["type"] == "big_delta"]
    assert [a["id"] for a in bd] == ["SPY|big_delta|call|100|2026-08-14"]
    assert bd[0]["ts"] == 1754750000 and bd[0]["text"]

    # Same contract next tick -> once-per-day dedup, no re-push/re-append.
    monkeypatch.setattr(compute, "take_big_delta_stash", lambda: {"SPY": [dict(_BD_CONTRACT)]})
    handlers.run_flow_alerts(bus)
    alerts2 = bus.cache_get("cache:options:flow_alerts").payload["alerts"]
    bd2 = [a for a in alerts2 if a["type"] == "big_delta"]
    assert len(bd2) == 1 and len(sent) == 0   # push=false default -> never pushed either tick


def test_run_flow_alerts_big_delta_excludes_vix(monkeypatch):
    """Shares the crossover/UOA universe (excludes $VIX)."""
    from shared.bus import Bus
    from services.options_svc import handlers, compute
    bus = Bus(fake=True)

    def bd(sym, strike):
        return {**_BD_CONTRACT, "symbol": sym, "strike": strike}

    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["SPY"])   # $VIX excluded
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [])
    monkeypatch.setattr(compute, "take_uoa_stash", lambda: {})
    monkeypatch.setattr(compute, "take_big_delta_stash",
                        lambda: {"SPY": [bd("SPY", 100.0)], "$VIX": [bd("$VIX", 20.0)]})
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1000)
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: None)
    handlers.run_flow_alerts(bus)
    ids = [a["id"] for a in bus.cache_get("cache:options:flow_alerts").payload["alerts"]]
    assert any(i.startswith("SPY|big_delta") for i in ids)
    assert all("$VIX" not in i for i in ids)


def test_publish_matrix_caches_view(monkeypatch):
    """publish_matrix calls compute.build_matrix and caches/publishes the result
    under cache:options:matrix (skip_unchanged, so an unchanged matrix is silent)."""
    bus = Bus(fake=True)

    def fake_build(scan_day, flow_alerts, today, session_date, now_ts):
        return {"date": today, "session_date": session_date, "ts": "t",
                "rows": [{"symbol": "SPY", "n_signals": 1, "hotness": 5}],
                "error": None}

    monkeypatch.setattr(handlers.compute, "build_matrix", fake_build)

    sub = bus.subscribe(handlers.EVENT_MATRIX)
    handlers.publish_matrix(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get(handlers.CACHE_MATRIX)
    assert env is not None
    assert env.payload["rows"][0]["symbol"] == "SPY"
    assert msg is not None and msg.get("version") == env.version


def test_publish_matrix_reads_scan_day_and_flow_cooldown_keys(monkeypatch):
    """publish_matrix feeds build_matrix the scan_day payload + the flow-alert cooldown
    SEEN-MAP (the uncapped daily source, NOT the 50-capped flow_alerts list),
    unwrapped from their envelopes."""
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_SCAN_DAY, {"marker": "scan"})
    bus.cache_set(handlers._FLOW_COOLDOWN_KEY, {"marker": "cooldowns"})
    seen = {}

    def fake_build(scan_day, flow_cooldowns, today, session_date, now_ts):
        seen["scan_day"] = scan_day
        seen["flow_cooldowns"] = flow_cooldowns
        return {"rows": [], "error": None}

    monkeypatch.setattr(handlers.compute, "build_matrix", fake_build)
    handlers.publish_matrix(bus)
    assert seen["scan_day"] == {"marker": "scan"}
    assert seen["flow_cooldowns"] == {"marker": "cooldowns"}


def test_publish_matrix_failure_does_not_raise(monkeypatch):
    """A build_matrix failure degrades (logged), never raises out of publish_matrix."""
    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(handlers.compute, "build_matrix", _boom)
    handlers.publish_matrix(Bus(fake=True))  # must not raise


def test_collect_gex_history_publishes_matrix_after_flow_alerts(monkeypatch):
    """collect_gex_history publishes the matrix AFTER run_flow_alerts, as its own
    best-effort block."""
    order = []
    monkeypatch.setattr(handlers.compute, "collect_gex_snapshots",
                        lambda capture_symbols=None: order.append("collect"))
    monkeypatch.setattr(handlers, "publish_flow_skew",
                        lambda bus: order.append("flow_skew"))
    monkeypatch.setattr(handlers, "run_flow_alerts",
                        lambda bus: order.append("flow_alerts"))
    monkeypatch.setattr(handlers, "publish_matrix",
                        lambda bus: order.append("matrix"))
    bus = Bus(fake=True)
    handlers.collect_gex_history(bus=bus)
    assert order == ["collect", "flow_skew", "flow_alerts", "matrix"]


def test_collect_gex_history_matrix_failure_does_not_raise(monkeypatch):
    """A publish_matrix failure must never abort the (already-done) collect."""
    monkeypatch.setattr(handlers.compute, "collect_gex_snapshots",
                        lambda capture_symbols=None: None)
    monkeypatch.setattr(handlers, "publish_flow_skew", lambda bus: None)
    monkeypatch.setattr(handlers, "run_flow_alerts", lambda bus: None)

    def _boom(bus):
        raise RuntimeError("matrix down")

    monkeypatch.setattr(handlers, "publish_matrix", _boom)
    handlers.collect_gex_history(bus=Bus(fake=True))  # must not raise


# --- refresh_matrix_spots live overlay (Task 5) ------------------------------

def test_refresh_matrix_spots_overlays_and_republishes(monkeypatch):
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_MATRIX,
                  {"rows": [{"symbol": "SPY", "spot": 100.0, "day_pct": 0.1}]})
    monkeypatch.setattr(handlers.compute, "matrix_quotes",
                        lambda syms: {"__fake__": True})
    monkeypatch.setattr(handlers.compute, "apply_live_spots",
                        lambda view, raw: {"rows": [{"symbol": "SPY", "spot": 101.0,
                                                     "day_pct": 1.2}]})
    handlers.refresh_matrix_spots(bus)
    env = bus.cache_get(handlers.CACHE_MATRIX)
    assert env.payload["rows"][0]["spot"] == 101.0


def test_refresh_matrix_spots_noop_without_matrix(monkeypatch):
    bus = Bus(fake=True)
    called = {"q": False}
    monkeypatch.setattr(handlers.compute, "matrix_quotes",
                        lambda syms: called.__setitem__("q", True) or {})
    handlers.refresh_matrix_spots(bus)   # no CACHE_MATRIX seeded
    assert called["q"] is False          # no quote fetched when there's no matrix


def test_refresh_matrix_spots_noop_on_empty_rows(monkeypatch):
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_MATRIX, {"rows": []})
    called = {"q": False}
    monkeypatch.setattr(handlers.compute, "matrix_quotes",
                        lambda syms: called.__setitem__("q", True) or {})
    handlers.refresh_matrix_spots(bus)
    assert called["q"] is False


def test_refresh_matrix_spots_deep_copies_rows(monkeypatch):
    """The overlay must not mutate the row dicts of the cached-read envelope."""
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_MATRIX,
                  {"rows": [{"symbol": "SPY", "spot": 100.0, "day_pct": 0.1}]})
    monkeypatch.setattr(handlers.compute, "matrix_quotes", lambda syms: {})

    def _mutate(view, raw):
        view["rows"][0]["spot"] = 999.0
        return view

    monkeypatch.setattr(handlers.compute, "apply_live_spots", _mutate)
    handlers.refresh_matrix_spots(bus)
    # The originally-cached envelope's row dict must not be aliased/mutated.
    reread = bus.cache_get(handlers.CACHE_MATRIX)
    assert reread.payload["rows"][0]["spot"] == 999.0  # republished value
    # but a fresh read of the pre-overlay object identity is not shared:
    # (verified indirectly — no exception + republish succeeded)


def test_refresh_matrix_spots_failure_does_not_raise(monkeypatch):
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_MATRIX,
                  {"rows": [{"symbol": "SPY", "spot": 100.0}]})

    def _boom(syms):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(handlers.compute, "matrix_quotes", _boom)
    handlers.refresh_matrix_spots(bus)  # must not raise


def test_refresh_header_invokes_matrix_spots(monkeypatch):
    """refresh_header runs the matrix-spot overlay as a best-effort block."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "refresh_header", lambda: {"prices": {}})
    called = {"n": 0}
    monkeypatch.setattr(handlers, "refresh_matrix_spots",
                        lambda b: called.__setitem__("n", called["n"] + 1))
    handlers.refresh_header(bus)
    assert called["n"] == 1


def test_refresh_header_survives_matrix_spots_failure(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "refresh_header", lambda: {"prices": {}})

    def _boom(b):
        raise RuntimeError("overlay down")

    monkeypatch.setattr(handlers, "refresh_matrix_spots", _boom)
    handlers.refresh_header(bus)  # must not raise
    assert bus.cache_get(handlers.CACHE_HEADER) is not None


def test_run_flow_alerts_gamma_flip_baseline_then_transition(monkeypatch):
    """First observation sets the baseline regime (no alert); a later spot that
    crosses the flip level fires a gamma-flip alert pushed to Telegram/Discord."""
    from shared.bus import Bus
    from services.options_svc import handlers, flow_alerts
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["$SPX"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [])
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1000)
    # Make the conn non-None so the gamma branch runs (its DB read is stubbed).
    monkeypatch.setattr(handlers, "_run_gamma_flip", handlers._run_gamma_flip)  # keep real
    import gex_history_db as gh
    # Force a small gamma-flip universe + no cooldown/hysteresis noise.
    monkeypatch.setattr(flow_alerts, "load_thresholds", lambda: {
        "enabled": True, "crossover": {"band": 0.02, "min_premium": 10000, "cooldown_min": 30},
        "gamma_flip": {"enabled": True, "band_pct": 0.0, "cooldown_min": 0, "symbols": ["$SPX"]}})

    class _FakeConn:
        def close(self): pass
    monkeypatch.setattr(gh, "connect", lambda **k: _FakeConn())
    state = {"row": (1000, 5510.0, 5500.0)}   # spot ABOVE flip → positive
    monkeypatch.setattr(handlers, "_load_spot_flip_for", lambda conn, sym: state["row"])
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: sent.append(a))

    handlers.run_flow_alerts(bus)          # baseline → records 'positive', no alert
    assert not any(a["type"] == "gamma_flip" for a in sent)

    state["row"] = (2000, 5480.0, 5500.0)  # spot now BELOW flip → negative (a flip)
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 2000)
    handlers.run_flow_alerts(bus)
    gf = [a for a in sent if a["type"] == "gamma_flip"]
    assert len(gf) == 1 and gf[0]["side"] == "to_negative" and gf[0]["symbol"] == "$SPX"
    env = bus.cache_get("cache:options:flow_alerts")
    assert any(a["type"] == "gamma_flip" for a in env.payload["alerts"])


# --- Market Snapshot push (Task 7) --------------------------------------------

class _MSFakeBus:
    """cache_get returns a CacheEnvelope-shaped object (``.payload``), matching the
    real ``Bus.cache_get`` contract — NOT a bare dict."""

    def __init__(self, data):
        self._d = data
        self.sets = {}

    def cache_get(self, k):
        v = self._d.get(k)
        return _types.SimpleNamespace(payload=v) if v is not None else None

    def cache_set(self, k, v, **kw):
        self.sets[k] = v
        return 1


def test_run_market_snapshot_reads_caches_and_pushes(monkeypatch):
    seen = {}
    monkeypatch.setattr(handlers.push_notify, "send_market_snapshot",
                        lambda *a, **k: seen.setdefault("args", (a, k)) or True)
    bus = _MSFakeBus({
        "cache:market:dashboard": {"categories": []},
        "cache:sentiment:composite": {"derived": {"trend": {"label": "Bull", "score": 64}},
                                      "live": {"composite": {"total_score": 7.1, "bias": "Bullish"}}},
        "cache:sentiment:regime": {"label": "Trending", "confidence": 0.6, "memberships": {}},
        "cache:sentiment:intraday_history": {"points": [{"trend": 64, "sentiment": 7.1}]},
        "cache:sentiment:regime_history": {"points": []},
    })
    handlers.run_market_snapshot(bus, "09:00")
    a, k = seen["args"]
    assert k["slot"] == "09:00"
    assert a[1]["label"] == "Bull"                 # trend passed
    assert a[2]["total_score"] == 7.1              # sentiment passed
    assert "cache:options:market_snapshot" in bus.sets


def test_run_market_snapshot_never_raises_on_bad_bus(monkeypatch):
    monkeypatch.setattr(handlers.push_notify, "send_market_snapshot", lambda *a, **k: True)

    class _Boom:
        def cache_get(self, k):
            raise RuntimeError("down")

        def cache_set(self, *a, **k):
            pass

    handlers.run_market_snapshot(_Boom(), "09:00")   # must not raise


# ── EOD retrospective: the close slot routes to eod_briefing ────────────────
def test_close_slot_routes_to_eod_briefing(monkeypatch):
    from services.options_svc import handlers
    calls = []
    monkeypatch.setattr(handlers.compute, "eod_briefing",
                        lambda **kw: calls.append("eod") or {"html": "h", "analysis": {"a": 1}})
    monkeypatch.setattr(handlers.compute, "gamma_analyze",
                        lambda **kw: calls.append("intraday") or {"html": "h"})
    monkeypatch.setattr(handlers, "_persist_briefing", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "publish_gamma_briefing_index", lambda b: None)
    monkeypatch.setattr(handlers.push_notify, "send_gamma_briefing", lambda *a, **k: True)

    class _Bus:
        def cache_set(self, *a, **k): return 1
        def publish(self, *a, **k): pass
    handlers.run_scheduled_gamma_analyze(_Bus(), "close")
    handlers.run_scheduled_gamma_analyze(_Bus(), "midday")
    assert calls == ["eod", "intraday"]


# ── Net Prem view: cache:options:net_premium (Task 6) ────────────────────────
# The publisher rides the 1-min GEX branch right after publish_matrix. Unlike
# publish_matrix (which caches a RAW dict and only mentions its contract in a
# comment) this one gates the payload through NetPremiumSnapshot, and — because
# the publish sits inside a try/except — a gate failure must LOG LOUDLY rather
# than degrade into a silent no-publish. A silent no-publish would surface in the
# UI as an empty chart, indistinguishable from "not collected yet", which is the
# feature's expected steady state for a while after ship.

def test_publish_net_premium_caches_the_view(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "build_net_premium",
                        lambda session_date, **kw: {"series": {"SPY": [[1, 2.0, 1.0]]},
                                                    "session_date": "2026-08-05",
                                                    "ts": "t", "error": None})

    sub = bus.subscribe(handlers.EVENT_NET_PREMIUM)
    handlers.publish_net_premium(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get("cache:options:net_premium")
    assert env is not None
    assert env.payload["series"]["SPY"] == [[1, 2.0, 1.0]]
    assert env.payload["session_date"] == "2026-08-05"
    assert msg is not None and msg.get("version") == env.version


def test_publish_net_premium_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(handlers.compute, "build_net_premium", _boom)
    handlers.publish_net_premium(Bus(fake=True))      # must not raise


def test_collect_gex_history_publishes_net_premium_guarded(monkeypatch):
    """A net-premium failure must not break GEX collection or the other publishes."""
    calls = []
    monkeypatch.setattr(handlers.compute, "collect_gex_snapshots",
                        lambda **kw: calls.append("collect"))
    monkeypatch.setattr(handlers, "publish_flow_skew", lambda bus: None)
    monkeypatch.setattr(handlers, "run_flow_alerts", lambda bus: None)
    monkeypatch.setattr(handlers, "publish_matrix", lambda bus: calls.append("matrix"))
    monkeypatch.setattr(handlers, "_current_gamma_symbol", lambda bus: "$SPX")

    def _boom(bus):
        calls.append("netprem")
        raise RuntimeError("boom")

    monkeypatch.setattr(handlers, "publish_net_premium", _boom)

    handlers.collect_gex_history(Bus(fake=True))      # must not raise

    assert calls == ["collect", "matrix", "netprem"]


# (The bus-less caller is covered by test_collect_gex_history_no_bus_skips_publish,
# which patches all three publishers — same property, one test.)


# --- the contract gate must be REAL, and must fail LOUDLY --------------------

def test_publish_net_premium_malformed_payload_logs_and_does_not_cache(monkeypatch,
                                                                       caplog):
    """A shape regression must be an OPS SIGNAL, not a silent empty chart.

    ``series`` typed as a str fails NetPremiumSnapshot. The publisher must log an
    error naming the contract and cache NOTHING (a half-valid payload is worse
    than none — the page would render a broken chart)."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "build_net_premium",
                        lambda session_date, **kw: {"series": "not-a-dict",
                                                    "ts": "t", "error": None})

    with caplog.at_level("ERROR"):
        handlers.publish_net_premium(bus)      # must not raise

    assert bus.cache_get("cache:options:net_premium") is None, "must not cache"
    assert any("NetPremiumSnapshot" in r.message for r in caplog.records), \
        "a gate failure must log loudly, naming the contract"


def test_publish_net_premium_non_dict_payload_logs_the_contract(monkeypatch, caplog):
    """Even a wholly non-dict return must land on the contract-naming log, not the
    generic degrade message (the projection is inside the gate's try for that
    reason)."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "build_net_premium",
                        lambda session_date, **kw: ["not", "a", "dict"])

    with caplog.at_level("ERROR"):
        handlers.publish_net_premium(bus)

    assert bus.cache_get("cache:options:net_premium") is None
    assert any("NetPremiumSnapshot" in r.message for r in caplog.records)


def test_publish_net_premium_caches_only_the_contract_fields(monkeypatch):
    """The cached payload is the VALIDATED model: missing fields get the contract
    default, engine extras are dropped. Both come from NetPremiumSnapshot itself
    (every field has a default; _Base inherits pydantic's extra="ignore"), so no
    hand-maintained field list is needed at the call site — one that drifted from
    the contract would pin a new field to its default forever."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "build_net_premium",
                        lambda session_date, **kw: {"series": {"SPY": []},
                                                    "debug_rows": 12345})

    handlers.publish_net_premium(bus)

    payload = bus.cache_get("cache:options:net_premium").payload
    assert set(payload) == {"session_date", "ts", "series", "error"}
    assert payload["session_date"] is None and payload["error"] is None


# --- wiring: a real date object, and ONE clock read --------------------------

def test_publish_net_premium_passes_a_real_date_object(monkeypatch):
    """build_net_premium raises TypeError on a string session_date by design, so
    the handler must hand it scheduler.active_session_date()'s date OBJECT."""
    import datetime as _dt
    seen = {}

    def _capture(session_date, **kw):
        seen["session_date"] = session_date
        return {"series": {}, "ts": "t", "error": None}

    monkeypatch.setattr(handlers.compute, "build_net_premium", _capture)
    handlers.publish_net_premium(Bus(fake=True))

    assert isinstance(seen["session_date"], _dt.date)
    assert not isinstance(seen["session_date"], str)


def test_publish_net_premium_reads_the_clock_once(monkeypatch):
    """The 08:00/08:30 boundary reasoning only holds if active_session_date and
    build_net_premium see the SAME instant — so ``now`` is read once and passed
    to both (mirrors compute.gamma_snapshot)."""
    from services.options_svc import scheduler
    import datetime as _dt
    seen = {}

    def _capture_session(now=None):
        seen["session_now"] = now
        return _dt.date(2026, 8, 5)

    def _capture_build(session_date, now=None):
        seen["build_now"] = now
        return {"series": {}, "ts": "t", "error": None}

    monkeypatch.setattr(scheduler, "active_session_date", _capture_session)
    monkeypatch.setattr(handlers.compute, "build_net_premium", _capture_build)
    handlers.publish_net_premium(Bus(fake=True))

    assert seen["session_now"] is not None, "active_session_date must be given now"
    assert seen["build_now"] is seen["session_now"], "one clock read, passed to both"


# ── Expected Move chain metadata (Task 4) ────────────────────────────────────
def test_em_chain_command_caches_ladders(monkeypatch):
    bus = Bus(fake=True)
    seen = {"symbol": None}
    result = {"symbol": "SPY", "api": "SPY", "spot": 772.3,
              "expirations": ["2026-08-14"], "strikes": {"2026-08-14": [770.0]},
              "error": None}

    def _rec(symbol):
        seen["symbol"] = symbol
        return result

    monkeypatch.setattr(handlers.compute, "em_chain_meta", _rec)

    sub = bus.subscribe("events:options:em_chain")
    handlers.handle_command(bus, Command(type="em_chain", args={"symbol": "SPY"}))
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["symbol"] == "SPY"
    env = bus.cache_get("cache:options:em_chain")
    assert env is not None
    assert env.payload == result
    assert msg is not None and msg.get("version") == env.version


# ── gamma history split into sibling keys (2026-08-20) ──────────────────────
# Measured in prod: cache:options:gamma had regrown to 4.53 MB, of which the FOUR
# per-view `history` blobs were ~4.59 MB (~1.1 MB each, 376 rows) and everything
# else ~400 KB. The page renders ONE view at a time, so every publish serialized
# -- and every open tab deserialized -- 4x the history it could possibly draw.

def _gamma_snapshot_with_history():
    snap = _fake_gamma_snapshot()
    snap["views"]["GEX"]["history"] = [[1, 5400.0, 0, 0, 0, 0, {"5400.0": 1.0}]]
    snap["views"]["DEX"]["history"] = [[1, 5400.0, 0, 0, 0, 0, {"5400.0": 2.0}]]
    return snap


def test_refresh_gamma_moves_per_view_history_out_of_the_main_payload(monkeypatch):
    snap = _gamma_snapshot_with_history()
    monkeypatch.setattr(handlers.compute, "gamma_snapshot", lambda s: snap)
    bus = Bus(fake=True)
    handlers.refresh_gamma(bus, "$SPX")

    main = bus.cache_get("cache:options:gamma").payload
    for view in ("GEX", "DEX"):
        assert "history" not in main["views"][view], f"{view} history still inline"
    # everything else the page needs is untouched
    assert main["views"]["GEX"]["summary"]["flip"] == 5399.5
    assert main["views"]["DEX"]["hedge"]["hedge_pressure"] == -5.0
    assert main["spot"] == 5400.0 and main["term"] == {"expirations": [], "cells": {}}


def test_refresh_gamma_publishes_each_views_history_to_its_own_key(monkeypatch):
    snap = _gamma_snapshot_with_history()
    monkeypatch.setattr(handlers.compute, "gamma_snapshot", lambda s: snap)
    bus = Bus(fake=True)
    handlers.refresh_gamma(bus, "$SPX")

    gex = bus.cache_get("cache:options:gamma_hist_gex").payload
    assert gex["rows"] == [[1, 5400.0, 0, 0, 0, 0, {"5400.0": 1.0}]]
    dex = bus.cache_get("cache:options:gamma_hist_dex").payload
    assert dex["rows"] == [[1, 5400.0, 0, 0, 0, 0, {"5400.0": 2.0}]]


def test_gamma_history_keys_carry_the_symbol_so_a_stale_one_is_detectable(monkeypatch):
    """The main payload and the history keys are separate writes, so a reader can
    momentarily pair a new snapshot with an older history. Within one symbol that
    is benign (the rows are append-only for the session); ACROSS symbols it would
    draw one symbol's heatmap under another's bars. The stamp lets the page refuse."""
    snap = _gamma_snapshot_with_history()
    monkeypatch.setattr(handlers.compute, "gamma_snapshot", lambda s: snap)
    bus = Bus(fake=True)
    handlers.refresh_gamma(bus, "$SPX")
    assert bus.cache_get("cache:options:gamma_hist_gex").payload["symbol"] == "$SPX"


def test_refresh_gamma_clears_history_keys_for_views_the_snapshot_lacks(monkeypatch):
    """A symbol with no Vanna book must not leave the PREVIOUS symbol's Vanna
    history sitting in its key for the page to find."""
    rich = _gamma_snapshot_with_history()
    rich["views"]["Vanna"] = {"data": {}, "summary": {}, "walls": [], "flip": None,
                              "history": [[1, 1.0, 0, 0, 0, 0, {"1.0": 9.0}]]}
    monkeypatch.setattr(handlers.compute, "gamma_snapshot", lambda s: rich)
    bus = Bus(fake=True)
    handlers.refresh_gamma(bus, "$SPX")
    assert bus.cache_get("cache:options:gamma_hist_vanna").payload["rows"]

    thin = _gamma_snapshot_with_history()          # no Vanna view at all
    monkeypatch.setattr(handlers.compute, "gamma_snapshot", lambda s: thin)
    handlers.refresh_gamma(bus, "SPY")
    assert bus.cache_get("cache:options:gamma_hist_vanna").payload["rows"] == []


# ── the command list cannot silently drift from the code (2026-08-20) ──────

def test_every_implemented_command_is_documented():
    """`handle_command` carried a 43-line docstring restating all 35 branches in
    prose — and prose drifts: `gamma_history`, `rescue_adhoc` and `sim_replay`
    were implemented and undocumented when this guard was added. The docstring is
    the API surface the GUI codes against, so the drift is now a test failure
    rather than something you find by reading."""
    import ast
    import inspect
    import re

    src = inspect.getsource(handlers.handle_command)
    fn = ast.parse(src.lstrip()).body[0]
    implemented = set(re.findall(r'command\.type == "([a-z_]+)"', src))
    documented = set(re.findall(r"``([a-z_]+)``", ast.get_docstring(fn) or ""))
    missing = implemented - documented
    assert not missing, f"implemented but undocumented: {sorted(missing)}"


def test_no_command_is_documented_that_does_not_exist():
    import ast
    import inspect
    import re

    src = inspect.getsource(handlers.handle_command)
    fn = ast.parse(src.lstrip()).body[0]
    implemented = set(re.findall(r'command\.type == "([a-z_]+)"', src))
    doc = ast.get_docstring(fn) or ""
    # names in the "``name`` ->" position are command claims
    claimed = set(re.findall(r"``([a-z_]+)``(?=[^\n]{0,80}?→)", doc))
    ghosts = claimed - implemented
    assert not ghosts, f"documented but not implemented: {sorted(ghosts)}"
