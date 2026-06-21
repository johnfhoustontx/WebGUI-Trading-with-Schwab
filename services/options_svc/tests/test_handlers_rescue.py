"""Tests for the manage-cycle rescue overlay + rescue_summary (Task 6.2).

The 5-min manage cycle (``run_manage_and_refresh``) now (a) tags each paper-account
view row with ``rescue_state`` + ``heat`` from ``compute.assess_open_positions``
(cheap — stored marks only), and (b) publishes a small ``rescue_summary`` cache
view for the nav badge. We monkeypatch ``compute.assess_open_positions`` +
``compute.paper_account_view`` so nothing touches a live proxy, and use a
fakeredis ``Bus(fake=True)`` (same style as ``test_handlers.py``).
"""
from shared.bus import Bus
from shared.contracts.envelope import Command
from services.options_svc import handlers


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
