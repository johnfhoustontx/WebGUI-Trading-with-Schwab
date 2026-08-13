"""The environment gate on the autonomous trading cycle (Task 6).

Deliberately redundant with the scheduler skip of Task 5, and the redundancy is
the point. ``cycle`` is not only a scheduled tick — it is also a COMMAND on
``cmd:driver``, and the driver's arm/disarm state lives in Redis at
``cache:driver:control``. The snapshot tool copies prod's Redis into dev; if a
snapshot were ever taken while the driver was armed, an enabled control key would
land in dev, and anything that enqueued a ``cycle`` command would start a dev
checkout paper-trading against a book it shares nothing with. The scheduler guard
cannot see that path; this one can.

``ENV_FLAGS["autonomous_trading"]`` is False in a suppressed environment, and
``run_autonomous_cycle`` then returns BEFORE its first side effect — before even
the ``read_control`` bus read, so there is no market fetch, no decider call and
no ``driver_paper_create`` enqueued.

Every test here sets the flag EXPLICITLY, overriding the suite-wide autouse
fixture in ``conftest.py`` that re-permits autonomous trading for the rest of the
driver suite (see that fixture's docstring for why it exists).
"""
from services.driver_svc import handlers


def _boom_market(*_a, **_k):
    raise AssertionError(
        "the market context was fetched — the autonomous_trading guard did not "
        "short-circuit")


def _boom_control(*_a, **_k):
    raise AssertionError(
        "the control key was read — the autonomous_trading guard is not the FIRST "
        "statement of run_autonomous_cycle")


def test_autonomous_cycle_is_inert_before_any_side_effect(fake_bus, monkeypatch):
    """Suppressed + armed control key → not one side effect, bus read included.

    Both downstream steps are booby-trapped rather than merely counted, so a
    deleted guard fails loudly here instead of quietly somewhere else.
    """
    handlers.set_control(fake_bus, enabled=True, halted=False)
    monkeypatch.setitem(handlers.ENV_FLAGS, "autonomous_trading", False)
    monkeypatch.setattr(handlers, "read_control", _boom_control)
    monkeypatch.setattr(handlers.compute, "fetch_market_context", _boom_market)
    monkeypatch.setattr(handlers.compute, "run_cycle", _boom_market)

    handlers.run_autonomous_cycle(fake_bus)   # must not raise

    assert fake_bus._r.xrange("cmd:options") == []
    assert fake_bus.cache_get(handlers.CACHE_AUTONOMOUS) is None


def test_snapshot_of_an_armed_prod_still_trades_nothing(fake_bus, monkeypatch):
    """THE scenario this task exists for.

    ``cache:driver:control`` arrives from a snapshot of a prod stack that was
    armed — ``enabled=True, halted=False``, exactly what the control gate is
    looking for — and the real ``read_control`` reads it. A suppressed environment
    must still open nothing: no command on ``cmd:options``, no monitor view, and
    the control key left exactly as the snapshot delivered it.
    """
    handlers.set_control(fake_bus, enabled=True, halted=False)
    armed = handlers.read_control(fake_bus)
    assert armed["enabled"] is True and armed["halted"] is False   # sanity
    monkeypatch.setitem(handlers.ENV_FLAGS, "autonomous_trading", False)
    monkeypatch.setattr(handlers.compute, "fetch_market_context", _boom_market)

    handlers.run_autonomous_cycle(fake_bus)

    assert fake_bus._r.xrange("cmd:options") == []
    assert fake_bus.cache_get(handlers.CACHE_AUTONOMOUS) is None
    assert handlers.read_control(fake_bus)["enabled"] is True   # untouched, not disarmed


def test_cycle_command_is_inert_when_suppressed(fake_bus, monkeypatch):
    """The command path specifically — ``cycle`` on ``cmd:driver`` is the entry the
    scheduler guard cannot cover, so it is pinned end-to-end through dispatch."""
    handlers.set_control(fake_bus, enabled=True, halted=False)
    monkeypatch.setitem(handlers.ENV_FLAGS, "autonomous_trading", False)
    monkeypatch.setattr(handlers.compute, "fetch_market_context", _boom_market)

    handlers.handle_command(fake_bus, type("C", (), {"type": "cycle", "args": {}})())

    assert fake_bus._r.xrange("cmd:options") == []
    assert fake_bus.cache_get(handlers.CACHE_AUTONOMOUS) is None


def test_autonomous_cycle_proceeds_when_permitted(fake_bus, monkeypatch):
    """Non-vacuity partner: with the flag True the guard is inert and the cycle runs.

    It asserts pre-existing behavior, so by construction it CANNOT fail if the
    guard is deleted. Its job is to prove the three suppression tests above mean
    "suppressed" rather than "run_autonomous_cycle is broken for every input".
    """
    handlers.set_control(fake_bus, enabled=True, halted=False)
    monkeypatch.setitem(handlers.ENV_FLAGS, "autonomous_trading", True)
    fake_bus.cache_set("cache:options:scan", {"signals_0dte": [], "signals_swing": []})
    fake_bus.cache_set("cache:options:driver_paper_account",
                       {"snapshot": {"session_pnl": 0.0}, "positions": []})
    seen = []
    monkeypatch.setattr(handlers.compute, "fetch_market_context",
                        lambda: seen.append("market") or {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", lambda *a, **k: {
        "decision": {"stand_down": True, "day_thesis": "t", "trades": []},
        "executable": [], "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})

    handlers.run_autonomous_cycle(fake_bus)

    assert seen == ["market"]
    assert fake_bus.cache_get(handlers.CACHE_AUTONOMOUS) is not None


def test_guard_defaults_permissive_when_the_flag_is_absent(fake_bus, monkeypatch):
    """A flags dict with no ``autonomous_trading`` key must READ AS PERMITTED.

    The repo's invariant: every environment guard defaults to the pre-environment
    behavior, so a checkout with no marker — or a profile missing the key — trades
    exactly as it did before environments existed.
    """
    handlers.set_control(fake_bus, enabled=True, halted=False)
    monkeypatch.delitem(handlers.ENV_FLAGS, "autonomous_trading", raising=False)
    fake_bus.cache_set("cache:options:scan", {"signals_0dte": [], "signals_swing": []})
    fake_bus.cache_set("cache:options:driver_paper_account",
                       {"snapshot": {"session_pnl": 0.0}, "positions": []})
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", lambda *a, **k: {
        "decision": {"stand_down": True, "day_thesis": "t", "trades": []},
        "executable": [], "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})

    handlers.run_autonomous_cycle(fake_bus)

    assert fake_bus.cache_get(handlers.CACHE_AUTONOMOUS) is not None


def test_guard_is_the_first_statement(fake_bus):
    """Placement, not just effect.

    The value of this guard is that it precedes EVERY side effect — including the
    ``read_control`` bus read. A later-placed guard would still make the function
    inert and still pass the tests above, so assert the position directly. Matched
    on the guard STATEMENT, not the bare flag name, so a docstring mention can
    never stand in for the code.
    """
    import inspect
    body = [ln.strip() for ln in
            inspect.getsource(handlers.run_autonomous_cycle).splitlines()]
    first = next(i for i, ln in enumerate(body) if ln.startswith("control ="))
    guard = next(i for i, ln in enumerate(body)
                 if ln.startswith('if not ENV_FLAGS.get("autonomous_trading"'))
    assert guard < first, "the environment guard must precede read_control"
