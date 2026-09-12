"""D3: the ledger exit pass must actually run on the manage tick.

⚠ This audit has now found FIVE "built, tested, never called" surfaces (the
ratchet ladder, the IV-history writer, ``signal_outcomes.settlement_underlying``,
``compute.manual_analytics``, and ``paper_trader.expire_paper_trade`` before
``expire_ledger_trades`` was written for it). A rule engine nothing invokes is
indistinguishable from no rule engine, and the unit tests beside it pass either
way — so the wiring gets its own test.

``run_manage_and_refresh`` is the ONE chokepoint both the 5-minute scheduler tick
and the page's "Run manage cycle" button pass through, which is why the pass needs
no second call site.
"""
from shared.bus import Bus

from services.options_svc import handlers


def _stub_tick(monkeypatch, calls):
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_manage_cycle", lambda **kw: None)
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades",
                        lambda: calls.__setitem__("expire", calls["expire"] + 1) or 0)
    monkeypatch.setattr(handlers.compute, "manage_ledger_trades",
                        lambda: calls.__setitem__("manage", calls["manage"] + 1) or 1)
    monkeypatch.setattr(handlers, "refresh_paper_account", lambda b: None)
    monkeypatch.setattr(handlers, "refresh_paper_trades",
                        lambda b, **k: calls.__setitem__("trades", calls["trades"] + 1))


def test_the_manage_tick_runs_the_ledger_EXIT_pass():
    """The whole point of D3: without this line the rules never execute."""
    import inspect
    src = inspect.getsource(handlers.run_manage_and_refresh)
    assert "manage_ledger_trades" in src


def test_the_tick_calls_it_once_and_still_refreshes(monkeypatch):
    calls = {"expire": 0, "manage": 0, "trades": 0}
    _stub_tick(monkeypatch, calls)
    handlers.run_manage_and_refresh(Bus(fake=True))
    assert calls == {"expire": 1, "manage": 1, "trades": 1}


def test_an_exit_pass_that_RAISES_does_not_cost_the_refreshes(monkeypatch):
    """The ledger view must still republish — a rule-engine hiccup cannot be
    allowed to freeze the page, the same guard ``expire_ledger_trades`` has."""
    calls = {"expire": 0, "manage": 0, "trades": 0}
    _stub_tick(monkeypatch, calls)

    def _boom():
        raise RuntimeError("rules exploded")

    monkeypatch.setattr(handlers.compute, "manage_ledger_trades", _boom)
    handlers.run_manage_and_refresh(Bus(fake=True))
    assert calls["trades"] == 1


def test_the_exit_pass_runs_BEFORE_the_expiry_settlement(monkeypatch):
    """⚠ Order is load-bearing. A position at its target ON its expiration day
    should book the target it reached, not an intrinsic settlement — and
    ``should_settle`` fires from 15:00 CT while the target may have been hit
    hours earlier. Exits first, then settle whatever is left."""
    order = []
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_manage_cycle", lambda **kw: None)
    monkeypatch.setattr(handlers.compute, "manage_ledger_trades",
                        lambda: order.append("manage") or 0)
    monkeypatch.setattr(handlers.compute, "expire_ledger_trades",
                        lambda: order.append("expire") or 0)
    monkeypatch.setattr(handlers, "refresh_paper_account", lambda b: None)
    monkeypatch.setattr(handlers, "refresh_paper_trades", lambda b, **k: None)

    handlers.run_manage_and_refresh(Bus(fake=True))
    assert order == ["manage", "expire"]
