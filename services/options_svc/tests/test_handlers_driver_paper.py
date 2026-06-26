"""Tests for the driver isolated-paper-account handlers (Phase 4 / Task 4.1).

The driver gets its OWN paper account (a dedicated DB) so its trades + standalone
P&L are tracked apart from the user's manual paper trades. These handlers expose
``compute.driver_account_view`` / ``driver_account_perf`` / ``open_driver_position``
(Units 1-2) via:
  * ``refresh_driver_paper(bus)``        — publish BOTH the account view AND the perf
                                           scorecard (NO rescue overlay).
  * ``run_driver_manage_and_refresh(bus)`` — manage cycle → refresh.
  * ``driver_paper_create`` / ``driver_paper_manage`` / ``driver_paper_reset``
    command branches in ``handle_command``.

We monkeypatch ``handlers.compute.*`` so nothing touches a live proxy / engine,
and use a fakeredis ``Bus(fake=True)`` (the options_svc test idiom — no fixture).
"""
from shared.bus import Bus
from shared.contracts.envelope import Command
from services.options_svc import handlers


def test_refresh_driver_paper_publishes_both_views(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(
        handlers.compute, "driver_account_view",
        lambda: {"snapshot": {"session_pnl": 12.0}, "positions": [],
                 "has_account": True})
    monkeypatch.setattr(
        handlers.compute, "driver_account_perf",
        lambda: {"total_trades": 0, "win_rate": 0.0})

    handlers.refresh_driver_paper(bus)

    acct = bus.cache_get("cache:options:driver_paper_account")
    perf = bus.cache_get("cache:options:driver_paper_perf")
    assert acct is not None and perf is not None
    assert acct.payload["snapshot"]["session_pnl"] == 12.0
    assert acct.payload["has_account"] is True
    assert perf.payload["total_trades"] == 0


def test_refresh_driver_paper_publishes_events(monkeypatch):
    """Both views fire their change events with the cache_set version."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf",
                        lambda: {"total_trades": 0})

    sub_a = bus.subscribe("events:options:driver_paper_account")
    sub_p = bus.subscribe("events:options:driver_paper_perf")
    handlers.refresh_driver_paper(bus)
    msg_a = sub_a.get_message(timeout=1.0)
    msg_p = sub_p.get_message(timeout=1.0)
    sub_a.close()
    sub_p.close()

    acct = bus.cache_get("cache:options:driver_paper_account")
    perf = bus.cache_get("cache:options:driver_paper_perf")
    assert msg_a is not None and msg_a["version"] == acct.version
    assert msg_p is not None and msg_p["version"] == perf.version


def test_refresh_driver_paper_no_rescue_overlay(monkeypatch):
    """``refresh_driver_paper`` must NOT call the rescue overlay — that reads the
    MANUAL account (``compute.assess_open_positions``) and would tag driver rows
    from the wrong book. The published positions stay exactly as the view returns
    them (no ``rescue_state``/``heat`` injected)."""
    bus = Bus(fake=True)
    monkeypatch.setattr(
        handlers.compute, "driver_account_view",
        lambda: {"snapshot": {"session_pnl": 0.0},
                 "positions": [{"position_id": 7, "symbol": "MU"}],
                 "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    def _boom():
        raise AssertionError("rescue overlay must not run for the driver account")

    monkeypatch.setattr(handlers.compute, "assess_open_positions", _boom)

    handlers.refresh_driver_paper(bus)

    pos = bus.cache_get("cache:options:driver_paper_account").payload["positions"][0]
    assert pos == {"position_id": 7, "symbol": "MU"}
    assert "rescue_state" not in pos and "heat" not in pos


def test_run_driver_manage_and_refresh(monkeypatch):
    """Runs the driver manage cycle THEN republishes both views."""
    bus = Bus(fake=True)
    calls = []
    monkeypatch.setattr(handlers.compute, "run_driver_manage_cycle",
                        lambda: calls.append("manage"))
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {"session_pnl": 5.0}, "positions": [],
                                 "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf",
                        lambda: {"total_trades": 1})

    handlers.run_driver_manage_and_refresh(bus)

    assert calls == ["manage"]
    assert bus.cache_get("cache:options:driver_paper_account").payload[
        "snapshot"]["session_pnl"] == 5.0
    assert bus.cache_get("cache:options:driver_paper_perf").payload[
        "total_trades"] == 1


def test_driver_paper_create_opens_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    opened = {}
    monkeypatch.setattr(
        handlers.compute, "open_driver_position",
        lambda signal, qty, **k: opened.update(signal=signal, qty=qty)
        or {"status": "opened"})
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    handlers.handle_command(
        bus, Command(type="driver_paper_create",
                     args={"signal": {"symbol": "MU"}, "qty": 2}))

    assert opened["signal"]["symbol"] == "MU" and opened["qty"] == 2
    assert bus.cache_get("cache:options:driver_paper_account") is not None
    assert bus.cache_get("cache:options:driver_paper_perf") is not None


def test_driver_paper_create_default_qty(monkeypatch):
    """``qty`` defaults to 1 (and is int-coerced) when absent."""
    bus = Bus(fake=True)
    opened = {}
    monkeypatch.setattr(
        handlers.compute, "open_driver_position",
        lambda signal, qty, **k: opened.update(qty=qty) or {"status": "opened"})
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    handlers.handle_command(
        bus, Command(type="driver_paper_create", args={"signal": {"symbol": "MU"}}))

    assert opened["qty"] == 1


def test_driver_paper_manage_command(monkeypatch):
    bus = Bus(fake=True)
    calls = []
    monkeypatch.setattr(handlers.compute, "run_driver_manage_cycle",
                        lambda: calls.append("manage"))
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    handlers.handle_command(bus, Command(type="driver_paper_manage"))

    assert calls == ["manage"]
    assert bus.cache_get("cache:options:driver_paper_account") is not None


def test_driver_paper_reset_command(monkeypatch):
    bus = Bus(fake=True)
    ensured = {}
    monkeypatch.setattr(handlers.compute, "ensure_driver_account",
                        lambda bal=25000.0: ensured.update(bal=bal))
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    handlers.handle_command(
        bus, Command(type="driver_paper_reset", args={"starting_balance": 30000.0}))

    assert ensured["bal"] == 30000.0
    assert bus.cache_get("cache:options:driver_paper_account") is not None


def test_existing_paper_create_branch_intact(monkeypatch):
    """The additive driver branches must not clobber the MANUAL ``paper_create``."""
    bus = Bus(fake=True)
    created = {}
    monkeypatch.setattr(handlers.compute, "create_paper_trade",
                        lambda signal, qty: created.update(signal=signal, qty=qty))
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda reprice=True: {"trades": []})

    handlers.handle_command(
        bus, Command(type="paper_create",
                     args={"signal": {"symbol": "SPY"}, "qty": 3}))

    assert created["signal"]["symbol"] == "SPY" and created["qty"] == 3
    # The driver account view is NOT published by the manual path.
    assert bus.cache_get("cache:options:driver_paper_account") is None
