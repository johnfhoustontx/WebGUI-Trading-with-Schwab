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
import datetime as _dt

from shared.bus import Bus
from shared.contracts.envelope import Command
from services.options_svc import handlers


def _clear_open_results():
    """R1's surfaced-results list is module-level (single-process service)."""
    handlers._LAST_OPEN_RESULTS.clear()


def _stale_command(cmd_type, args, seconds):
    """Build a command whose enqueue ts is ``seconds`` in the past (R5)."""
    ts = (_dt.datetime.now(_dt.timezone.utc)
          - _dt.timedelta(seconds=seconds)).isoformat()
    return Command(type=cmd_type, args=args, ts=ts)


def _stub_analytics(monkeypatch, payload=None):
    """Keep refresh_driver_paper's analytics call OFF the real driver DB in unit tests."""
    monkeypatch.setattr(handlers.compute, "driver_analytics",
                        lambda: payload if payload is not None
                        else {"equity_curve": [], "postmortem": {}, "excursions": {}})


def test_refresh_driver_paper_publishes_both_views(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(
        handlers.compute, "driver_account_view",
        lambda: {"snapshot": {"session_pnl": 12.0}, "positions": [],
                 "has_account": True})
    monkeypatch.setattr(
        handlers.compute, "driver_account_perf",
        lambda: {"total_trades": 0, "win_rate": 0.0})
    _stub_analytics(monkeypatch)

    handlers.refresh_driver_paper(bus)

    acct = bus.cache_get("cache:options:driver_paper_account")
    perf = bus.cache_get("cache:options:driver_paper_perf")
    assert acct is not None and perf is not None
    assert acct.payload["snapshot"]["session_pnl"] == 12.0
    assert acct.payload["has_account"] is True
    assert perf.payload["total_trades"] == 0


def test_refresh_driver_paper_publishes_analytics_view(monkeypatch):
    """The analytics view (equity curve / posture post-mortem / MAE-MFE) is published
    alongside the account + perf views."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {"total_trades": 0})
    _stub_analytics(monkeypatch, {"equity_curve": [{"date": "d", "equity": 25100.0}],
                                  "postmortem": {"by_stance": {}}, "excursions": {"n": 0}})
    handlers.refresh_driver_paper(bus)
    view = bus.cache_get("cache:options:driver_paper_analytics")
    assert view is not None and view.payload["equity_curve"][0]["equity"] == 25100.0


def test_refresh_driver_paper_publishes_events(monkeypatch):
    """Both views fire their change events with the cache_set version."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf",
                        lambda: {"total_trades": 0})
    _stub_analytics(monkeypatch)

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
    _stub_analytics(monkeypatch)

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
    _stub_analytics(monkeypatch)

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


# ── R1: driver open results are captured, logged, and surfaced ───────────────

def test_r1_rejected_open_is_logged_and_surfaced(monkeypatch, caplog):
    """A rejected open (status != opened) is log.warning'd AND appears in the
    surfaced ``last_open_results`` list on the driver account view."""
    _clear_open_results()
    bus = Bus(fake=True)
    monkeypatch.setattr(
        handlers.compute, "open_driver_position",
        lambda signal, qty, **k: {"status": "rejected", "reason": "LOW_CREDIT"})
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    with caplog.at_level("WARNING"):
        handlers.handle_command(
            bus, Command(type="driver_paper_create",
                         args={"signal": {"symbol": "MU"}, "qty": 1}))

    assert any("did NOT land" in r.message for r in caplog.records)
    view = bus.cache_get("cache:options:driver_paper_account").payload
    results = view["last_open_results"]
    assert len(results) == 1
    assert results[0]["status"] == "rejected"
    assert results[0]["reason"] == "LOW_CREDIT"
    assert results[0]["symbol"] == "MU"


def test_r1_error_open_is_logged_and_surfaced(monkeypatch, caplog):
    """An error open (defensive degradation inside open_driver_position) is
    surfaced too, not swallowed."""
    _clear_open_results()
    bus = Bus(fake=True)
    monkeypatch.setattr(
        handlers.compute, "open_driver_position",
        lambda signal, qty, **k: {"status": "error", "error": "KeyError: 'width'"})
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    with caplog.at_level("WARNING"):
        handlers.handle_command(
            bus, Command(type="driver_paper_create",
                         args={"signal": {"symbol": "NVDA", "id": "s1"}}))

    assert any("did NOT land" in r.message for r in caplog.records)
    results = bus.cache_get("cache:options:driver_paper_account").payload["last_open_results"]
    assert results[-1]["status"] == "error"
    assert "KeyError" in results[-1]["error"]


def test_r1_opened_records_normally(monkeypatch):
    """A successful open records with status=opened (and does not log a warning)."""
    _clear_open_results()
    bus = Bus(fake=True)
    monkeypatch.setattr(
        handlers.compute, "open_driver_position",
        lambda signal, qty, **k: {"status": "opened", "symbol": "SPY",
                                  "qty": 1, "entry_credit": 0.42})
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    handlers.handle_command(
        bus, Command(type="driver_paper_create",
                     args={"signal": {"symbol": "SPY"}, "qty": 1}))

    results = bus.cache_get("cache:options:driver_paper_account").payload["last_open_results"]
    assert results[-1]["status"] == "opened"
    assert results[-1]["entry_credit"] == 0.42


# ── R5: stale trade-opening commands are rejected ────────────────────────────

def test_r5_stale_driver_open_is_rejected(monkeypatch, caplog):
    """A driver_paper_create older than the threshold is REJECTED (open not
    called) with a logged reason + a surfaced stale record."""
    _clear_open_results()
    bus = Bus(fake=True)
    called = []
    monkeypatch.setattr(handlers.compute, "open_driver_position",
                        lambda signal, qty, **k: called.append(1) or {"status": "opened"})
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    cmd = _stale_command("driver_paper_create", {"signal": {"symbol": "MU"}, "qty": 1},
                         seconds=handlers.STALE_OPEN_MAX_AGE_SEC + 60)
    with caplog.at_level("WARNING"):
        handlers.handle_command(bus, cmd)

    assert called == []  # open NEVER attempted on a stale command
    assert any("REJECTED stale driver_paper_create" in r.message for r in caplog.records)
    results = bus.cache_get("cache:options:driver_paper_account").payload["last_open_results"]
    assert results[-1]["status"] == "rejected"
    assert results[-1]["reason"] == "stale_command"


def test_r5_fresh_driver_open_proceeds(monkeypatch):
    """A fresh command (recent ts) opens normally."""
    _clear_open_results()
    bus = Bus(fake=True)
    called = []
    monkeypatch.setattr(handlers.compute, "open_driver_position",
                        lambda signal, qty, **k: called.append(1) or {"status": "opened"})
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    cmd = _stale_command("driver_paper_create", {"signal": {"symbol": "MU"}, "qty": 1},
                         seconds=5)  # 5s old → fresh
    handlers.handle_command(bus, cmd)

    assert called == [1]  # open attempted


def test_r5_missing_ts_treated_as_fresh(monkeypatch):
    """A command with ts=None (a legacy command, or explicit unknown) is treated
    as NOT stale — never reject a legacy command (back-compat)."""
    _clear_open_results()
    bus = Bus(fake=True)
    called = []
    monkeypatch.setattr(handlers.compute, "open_driver_position",
                        lambda signal, qty, **k: called.append(1) or {"status": "opened"})
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})

    cmd = Command(type="driver_paper_create", args={"signal": {"symbol": "MU"}}, ts=None)
    handlers.handle_command(bus, cmd)

    assert called == [1]  # opened despite no ts


def test_r5_stale_manual_paper_create_is_rejected(monkeypatch, caplog):
    """A stale manual paper_create is refused (create not called) + surfaced."""
    _clear_open_results()
    bus = Bus(fake=True)
    called = []
    monkeypatch.setattr(handlers.compute, "create_paper_trade",
                        lambda signal, qty: called.append(1))
    monkeypatch.setattr(handlers.compute, "paper_trades_view",
                        lambda reprice=True: {"trades": []})

    cmd = _stale_command("paper_create", {"signal": {"symbol": "SPY"}, "qty": 1},
                         seconds=handlers.STALE_OPEN_MAX_AGE_SEC + 30)
    with caplog.at_level("WARNING"):
        handlers.handle_command(bus, cmd)

    assert called == []  # create NEVER attempted
    assert any("REJECTED stale paper_create" in r.message for r in caplog.records)
    assert handlers._LAST_OPEN_RESULTS[-1]["reason"] == "stale_command"


def test_r5_idempotent_refresh_not_gated(monkeypatch):
    """A stale ``refresh_paper`` (idempotent) is NOT gated — it still runs."""
    bus = Bus(fake=True)
    called = []
    monkeypatch.setattr(handlers.compute, "paper_account_view",
                        lambda: called.append(1) or {"positions": []})
    monkeypatch.setattr(handlers.compute, "assess_open_positions", lambda: {})

    cmd = _stale_command("refresh_paper", {}, seconds=99999)
    handlers.handle_command(bus, cmd)

    assert called == [1]  # ran despite being old
