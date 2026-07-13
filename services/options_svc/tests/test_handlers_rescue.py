"""Tests for the manage-cycle rescue overlay + rescue_summary (Task 6.2).

The 5-min manage cycle (``run_manage_and_refresh``) now (a) tags each paper-account
view row with ``rescue_state`` + ``heat`` from ``compute.assess_open_positions``
(cheap — stored marks only), and (b) publishes a small ``rescue_summary`` cache
view for the nav badge. We monkeypatch ``compute.assess_open_positions`` +
``compute.paper_account_view`` so nothing touches a live proxy, and use a
fakeredis ``Bus(fake=True)`` (same style as ``test_handlers.py``).
"""
import types

from shared.bus import Bus
from shared.contracts.envelope import Command
from services.options_svc import handlers


def _stub_paper_adjust(monkeypatch, apply_fn):
    """Install a fake ``paper_adjust`` module exposing ``apply_adjustment``.

    ``handlers.paper_adjust`` is lazily imported (None until first real use), so
    tests inject a stub module + reset the cached attr afterward."""
    monkeypatch.setattr(handlers, "paper_adjust",
                        types.SimpleNamespace(apply_adjustment=apply_fn))


def test_refresh_paper_adds_rescue_overlay(monkeypatch):
    """refresh_paper_account merges rescue_state/heat onto each position row by
    position_id (int/str coerced) and the tagged view reaches CACHE_PAPER."""
    bus = Bus(fake=True)
    # Paper view rows: one int id, one str id (the overlay must match both).
    view = {
        "snapshot": {"equity": 25000.0},
        "positions": [
            {"position_id": 1, "symbol": "SPY"},
            {"position_id": "2", "symbol": "QQQ"},
            {"position_id": 3, "symbol": "IWM"},  # no overlay entry -> defaults
        ],
        "orders": [],
        "has_account": True,
    }
    # per_position keyed by int position_id (as assess_open_positions returns).
    assessed = {
        "per_position": {
            1: {"state": "critical", "heat": 0.92},
            2: {"state": "tested", "heat": 0.55},
        },
        "summary": {"n_tested": 2, "n_critical": 1, "position_ids": [1, 2]},
    }
    monkeypatch.setattr(handlers.compute, "paper_account_view", lambda: view)
    monkeypatch.setattr(handlers.compute, "assess_open_positions", lambda: assessed)

    handlers.refresh_paper_account(bus)

    env = bus.cache_get(handlers.CACHE_PAPER)
    assert env is not None
    rows = {r["symbol"]: r for r in env.payload["positions"]}
    # int row id matched.
    assert rows["SPY"]["rescue_state"] == "critical"
    assert rows["SPY"]["heat"] == 0.92
    # str row id matched against int overlay key (coerced).
    assert rows["QQQ"]["rescue_state"] == "tested"
    assert rows["QQQ"]["heat"] == 0.55
    # No overlay entry -> safe defaults, never crashes.
    assert rows["IWM"]["rescue_state"] == "ok"
    assert rows["IWM"]["heat"] == 0.0


def test_refresh_paper_overlay_failure_still_publishes(monkeypatch):
    """If assess_open_positions blows up, the core paper-account view still
    publishes (overlay is best-effort)."""
    bus = Bus(fake=True)
    view = {"positions": [{"position_id": 1, "symbol": "SPY"}], "has_account": True}
    monkeypatch.setattr(handlers.compute, "paper_account_view", lambda: view)

    def _boom():
        raise RuntimeError("assess failed")

    monkeypatch.setattr(handlers.compute, "assess_open_positions", _boom)

    handlers.refresh_paper_account(bus)
    env = bus.cache_get(handlers.CACHE_PAPER)
    assert env is not None
    # The row is still there (untagged) — the publish was not blocked.
    assert env.payload["positions"][0]["symbol"] == "SPY"


def test_publish_rescue_summary(monkeypatch):
    """publish_rescue_summary caches the summary (n_tested/n_critical/position_ids)
    under CACHE_RESCUE_SUMMARY and publishes a version event."""
    bus = Bus(fake=True)
    assessed = {
        "per_position": {1: {"state": "tested", "heat": 0.5}},
        "summary": {"n_tested": 1, "n_critical": 0, "position_ids": [1]},
    }
    monkeypatch.setattr(handlers.compute, "assess_open_positions", lambda: assessed)

    sub = bus.subscribe(handlers.EVENT_RESCUE_SUMMARY)
    handlers.publish_rescue_summary(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get(handlers.CACHE_RESCUE_SUMMARY)
    assert env is not None
    assert env.payload == {"n_tested": 1, "n_critical": 0, "position_ids": [1]}
    assert msg is not None and msg.get("version") == env.version


def test_publish_rescue_summary_skips_unchanged(monkeypatch):
    """An identical summary on a later manage tick must NOT bump the version."""
    bus = Bus(fake=True)
    assessed = {"summary": {"n_tested": 0, "n_critical": 0, "position_ids": []}}
    monkeypatch.setattr(handlers.compute, "assess_open_positions", lambda: assessed)

    handlers.publish_rescue_summary(bus)
    v1 = bus.cache_get(handlers.CACHE_RESCUE_SUMMARY).version
    handlers.publish_rescue_summary(bus)  # identical -> skip
    assert bus.cache_get(handlers.CACHE_RESCUE_SUMMARY).version == v1


def test_run_manage_and_refresh_publishes_summary(monkeypatch):
    """The manage tick refreshes the paper view AND publishes the rescue summary
    (no new cadence — it piggybacks the existing 5-min manage cycle)."""
    bus = Bus(fake=True)
    calls = {"manage": 0, "refresh": 0, "summary": 0}
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: True)
    monkeypatch.setattr(handlers.compute, "run_manage_cycle",
                        lambda: calls.__setitem__("manage", calls["manage"] + 1))
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda b: calls.__setitem__("refresh", calls["refresh"] + 1))
    monkeypatch.setattr(handlers, "publish_rescue_summary",
                        lambda b: calls.__setitem__("summary", calls["summary"] + 1))

    handlers.run_manage_and_refresh(bus)
    assert calls == {"manage": 1, "refresh": 1, "summary": 1}


def test_run_manage_and_refresh_summary_failure_non_fatal(monkeypatch):
    """A failing rescue-summary publish must not prevent the manage/refresh path."""
    bus = Bus(fake=True)
    calls = {"refresh": 0}
    monkeypatch.setattr(handlers.compute, "has_paper_account", lambda: False)
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda b: calls.__setitem__("refresh", calls["refresh"] + 1))

    def _boom(b):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(handlers, "publish_rescue_summary", _boom)

    handlers.run_manage_and_refresh(bus)  # must not raise
    assert calls["refresh"] == 1


# ── rescue / rescue_apply command handlers (Task 6.3) ───────────────────────

_SAMPLE_ADVISORY = {
    "position_id": 5,
    "symbol": "SPY",
    "strategy": "PCS",
    "state": "tested",
    "heat": 0.6,
    "mark": {"underlying": 500.0, "current_value": 1.2,
             "unrealized_pnl": -30.0, "short_delta": -0.4, "dte": 3},
    "context": [],
    "candidates": [],
    "ts": "2026-06-21T00:00:00+00:00",
}


def test_run_rescue_caches_advisory(monkeypatch):
    """run_rescue caches compute_rescue's advisory under cache:options:rescue:<id>
    and publishes a version+position_id event."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "compute_rescue",
                        lambda pid, source="paper": dict(_SAMPLE_ADVISORY, position_id=pid))

    sub = bus.subscribe(handlers.EVENT_RESCUE)
    handlers.run_rescue(bus, 5)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    env = bus.cache_get(f"{handlers.CACHE_RESCUE}:5")
    assert env is not None
    assert env.payload["position_id"] == 5
    assert env.payload["symbol"] == "SPY"
    assert msg is not None
    assert msg.get("version") == env.version
    assert msg.get("position_id") == 5


def test_run_rescue_passes_source_and_caches_str_id(monkeypatch):
    """run_rescue forwards the source arg to compute_rescue and caches under the
    string signal_id key for a captured advisory."""
    bus = Bus(fake=True)
    seen = {}
    monkeypatch.setattr(handlers.compute, "compute_rescue",
                        lambda pid, source="paper": seen.update(pid=pid, source=source)
                        or dict(_SAMPLE_ADVISORY, position_id=pid, source=source))

    handlers.run_rescue(bus, "SIG1", source="captured")
    assert seen == {"pid": "SIG1", "source": "captured"}
    env = bus.cache_get(f"{handlers.CACHE_RESCUE}:SIG1")
    assert env is not None
    assert env.payload["position_id"] == "SIG1"
    assert env.payload["source"] == "captured"


def test_rescue_apply_success_refreshes(monkeypatch):
    """A successful apply refreshes the paper view and re-caches the advisory with
    apply_result.ok True attached."""
    bus = Bus(fake=True)
    calls = {"refresh": 0}
    monkeypatch.setattr(handlers.compute, "_load_position",
                        lambda pid: {"position_id": pid, "symbol": "SPY",
                                     "status": "OPEN"})
    monkeypatch.setattr(handlers.compute, "_make_leg_pricer",
                        lambda sym: (lambda *a, **k: 1.0))
    monkeypatch.setattr(handlers.compute, "compute_rescue",
                        lambda pid: dict(_SAMPLE_ADVISORY, position_id=pid))
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda b: calls.__setitem__("refresh", calls["refresh"] + 1))
    _stub_paper_adjust(monkeypatch,
                       lambda db, pos, cand, **kw: {"ok": True, "action": "convert_ic",
                                                    "position_id": 5})

    handlers.run_rescue_apply(bus, 5, {"action": "convert_ic"})

    assert calls["refresh"] == 1
    env = bus.cache_get(f"{handlers.CACHE_RESCUE}:5")
    assert env is not None
    assert env.payload["apply_result"]["ok"] is True
    assert env.payload["apply_result"]["action"] == "convert_ic"


def test_rescue_apply_stale_surfaces(monkeypatch):
    """A stale apply does NOT refresh the paper view; the cached advisory carries
    the stale apply_result so the GUI sees why it didn't apply."""
    bus = Bus(fake=True)
    calls = {"refresh": 0}
    monkeypatch.setattr(handlers.compute, "_load_position",
                        lambda pid: {"position_id": pid, "symbol": "SPY",
                                     "status": "OPEN"})
    monkeypatch.setattr(handlers.compute, "_make_leg_pricer",
                        lambda sym: (lambda *a, **k: 1.0))
    monkeypatch.setattr(handlers.compute, "compute_rescue",
                        lambda pid: dict(_SAMPLE_ADVISORY, position_id=pid))
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda b: calls.__setitem__("refresh", calls["refresh"] + 1))
    _stub_paper_adjust(monkeypatch,
                       lambda db, pos, cand, **kw: {"ok": False, "stale": True,
                                                    "error": "prices moved",
                                                    "position_id": 5})

    handlers.run_rescue_apply(bus, 5, {"action": "narrow"})

    assert calls["refresh"] == 0  # no mutation/refresh on a stale abort
    env = bus.cache_get(f"{handlers.CACHE_RESCUE}:5")
    assert env is not None
    assert env.payload["apply_result"]["stale"] is True
    assert env.payload["apply_result"]["ok"] is False


def test_rescue_apply_refuses_non_paper_position(monkeypatch):
    """A non-paper / captured-signal / bogus id (not in paper_account_db ->
    _load_position returns None) is refused cleanly with an error advisory and no
    apply/refresh."""
    bus = Bus(fake=True)
    calls = {"refresh": 0, "apply": 0}
    monkeypatch.setattr(handlers.compute, "_load_position", lambda pid: None)
    monkeypatch.setattr(handlers, "refresh_paper_account",
                        lambda b: calls.__setitem__("refresh", calls["refresh"] + 1))
    _stub_paper_adjust(monkeypatch,
                       lambda *a, **k: calls.__setitem__("apply", calls["apply"] + 1))

    handlers.run_rescue_apply(bus, 999, {"action": "narrow"})

    assert calls == {"refresh": 0, "apply": 0}  # nothing mutated
    env = bus.cache_get(f"{handlers.CACHE_RESCUE}:999")
    assert env is not None
    assert env.payload["error"]
    assert env.payload["apply_result"]["ok"] is False


def test_rescue_apply_closed_position_minimal_advisory(monkeypatch):
    """When the apply closes/rolls the position, the recomputed advisory returns
    position-not-found; the handler caches a minimal advisory still carrying the
    apply_result."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "_load_position",
                        lambda pid: {"position_id": pid, "symbol": "SPY",
                                     "status": "OPEN"})
    monkeypatch.setattr(handlers.compute, "_make_leg_pricer",
                        lambda sym: (lambda *a, **k: 1.0))
    monkeypatch.setattr(handlers, "refresh_paper_account", lambda b: None)
    # After a successful close, compute_rescue can't find the position anymore.
    monkeypatch.setattr(handlers.compute, "compute_rescue",
                        lambda pid: {"error": "position not found"})
    _stub_paper_adjust(monkeypatch,
                       lambda db, pos, cand, **kw: {"ok": True, "action": "close",
                                                    "position_id": 5, "realized": 42.0})

    handlers.run_rescue_apply(bus, 5, {"action": "close"})

    env = bus.cache_get(f"{handlers.CACHE_RESCUE}:5")
    assert env is not None
    assert env.payload["position_id"] == 5
    assert env.payload["apply_result"]["ok"] is True
    assert env.payload["apply_result"]["realized"] == 42.0


def test_handle_command_dispatches_rescue(monkeypatch):
    """handle_command routes a 'rescue' command (paper: int-coerced position_id) to
    run_rescue with source='paper'."""
    bus = Bus(fake=True)
    seen = {}
    monkeypatch.setattr(handlers, "run_rescue",
                        lambda b, pid, source="paper": seen.update(pid=pid, source=source))
    handlers.handle_command(bus, Command(type="rescue", args={"position_id": "7"}))
    assert seen == {"pid": 7, "source": "paper"}  # coerced to int, default source


def test_handle_command_dispatches_rescue_captured(monkeypatch):
    """A captured 'rescue' command keeps the string signal_id and routes
    source='captured' through to run_rescue (no int coercion)."""
    bus = Bus(fake=True)
    seen = {}
    monkeypatch.setattr(handlers, "run_rescue",
                        lambda b, pid, source="paper": seen.update(pid=pid, source=source))
    handlers.handle_command(bus, Command(
        type="rescue",
        args={"position_id": "AAPL_0_PCS_500", "source": "captured"}))
    assert seen == {"pid": "AAPL_0_PCS_500", "source": "captured"}


def test_run_rescue_adhoc_caches_advisory(monkeypatch):
    """run_rescue_adhoc caches compute_rescue_adhoc's advisory under
    cache:options:rescue:adhoc and publishes a version+position_id event."""
    bus = Bus(fake=True)
    seen = {}
    monkeypatch.setattr(handlers.compute, "compute_rescue_adhoc",
                        lambda spec: seen.update(spec=spec)
                        or dict(_SAMPLE_ADVISORY, position_id="adhoc", source="adhoc"))

    sub = bus.subscribe(handlers.EVENT_RESCUE)
    spec = {"symbol": "SPY", "strategy": "PCS", "short_strike": 500.0,
            "long_strike": 495.0, "expiration": "2099-07-31"}
    handlers.run_rescue_adhoc(bus, spec)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    assert seen["spec"] == spec
    env = bus.cache_get(f"{handlers.CACHE_RESCUE}:adhoc")
    assert env is not None
    assert env.payload["position_id"] == "adhoc"
    assert env.payload["source"] == "adhoc"
    assert msg is not None
    assert msg.get("version") == env.version
    assert msg.get("position_id") == "adhoc"


def test_handle_command_dispatches_rescue_adhoc(monkeypatch):
    """handle_command routes a 'rescue_adhoc' command (args.spec) to
    run_rescue_adhoc."""
    bus = Bus(fake=True)
    seen = {}
    monkeypatch.setattr(handlers, "run_rescue_adhoc",
                        lambda b, spec: seen.update(spec=spec))
    spec = {"symbol": "SPY", "strategy": "PCS", "short_strike": 500.0,
            "long_strike": 495.0, "expiration": "2099-07-31"}
    handlers.handle_command(bus, Command(type="rescue_adhoc", args={"spec": spec}))
    assert seen == {"spec": spec}


def test_handle_command_rescue_adhoc_spec_as_args_fallback(monkeypatch):
    """When the spec IS the args dict (no 'spec' key), handle_command still
    forwards it to run_rescue_adhoc."""
    bus = Bus(fake=True)
    seen = {}
    monkeypatch.setattr(handlers, "run_rescue_adhoc",
                        lambda b, spec: seen.update(spec=spec))
    args = {"symbol": "SPY", "strategy": "PCS", "short_strike": 500.0,
            "long_strike": 495.0, "expiration": "2099-07-31"}
    handlers.handle_command(bus, Command(type="rescue_adhoc", args=args))
    assert seen == {"spec": args}


def test_handle_command_dispatches_rescue_apply(monkeypatch):
    """handle_command routes a 'rescue_apply' command to run_rescue_apply with the
    int position_id + candidate dict."""
    bus = Bus(fake=True)
    seen = {}
    monkeypatch.setattr(handlers, "run_rescue_apply",
                        lambda b, pid, cand: seen.update(pid=pid, cand=cand))
    handlers.handle_command(bus, Command(
        type="rescue_apply",
        args={"position_id": "5", "candidate": {"action": "narrow"}}))
    assert seen == {"pid": 5, "cand": {"action": "narrow"}}
