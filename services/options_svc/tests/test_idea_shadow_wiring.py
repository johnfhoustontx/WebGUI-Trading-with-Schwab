"""The shadow ideas' seams: ``handlers.run_idea_shadow`` books a pick into the Paper
Ledger as an ``idea_shadow`` row and posts nothing; the scheduler checks once per
published scan; the row is self-managed but never an idea.

The pure decision is ``test_idea_shadow.py``.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute, handlers, idea_ledger, push_notify, scheduler
from services.options_svc.tests.test_idea_ledger import CFG
from services.options_svc.tests.test_idea_shadow import CFG as SHADOW_CFG
from services.options_svc.tests.test_idea_shadow import call, scan
from services.options_svc.tests.test_idea_ledger_wiring import _manual
from services.options_svc.tests.test_trade_idea import _Bus

_CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 17, 10, 38, tzinfo=_CT)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A real trades.db under tmp_path, the shadow on, every send refused."""
    import paper_trader
    import trade_tracker_client
    import trades_db

    from shared import paper_limits
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(trades_db, "_initialised", set())
    monkeypatch.setattr(trade_tracker_client, "track", lambda t: True)
    monkeypatch.setattr(trade_tracker_client, "untrack", lambda tid: True)
    monkeypatch.setattr(paper_limits, "idea_trades", lambda: dict(CFG))
    monkeypatch.setattr(paper_limits, "idea_shadow", lambda: dict(SHADOW_CFG))
    monkeypatch.setattr(push_notify, "trade_idea_config",
                        lambda config=None: {"enabled": True})

    def _no_send(*a, **k):
        raise AssertionError("a shadow pick must never be sent")
    monkeypatch.setattr(push_notify, "send_trade_idea", _no_send)
    monkeypatch.setattr(handlers.x_post, "post", _no_send)
    published = []
    monkeypatch.setattr(handlers, "refresh_paper_trades",
                        lambda bus, **kw: published.append(kw))
    paper_trader.published = published
    return paper_trader


def _bus_after_one_scan(rows_before=()):
    """A bus whose shadow state has already seen one scan holding ``rows_before``."""
    bus = _Bus({"cache:options:scan": scan(at=NOW - dt.timedelta(minutes=21),
                                           directional=rows_before)})
    handlers.run_idea_shadow(bus, now=NOW - dt.timedelta(minutes=15))
    return bus


def _shadow_rows(paper_trader):
    return [t for t in paper_trader.get_all_trades()
            if t.get("source") == idea_ledger.SHADOW_SOURCE]


def test_a_new_strong_idea_is_booked_as_a_shadow_row(ledger):
    bus = _bus_after_one_scan()
    bus.caches["cache:options:scan"] = scan(directional=[call()])
    res = handlers.run_idea_shadow(bus, now=NOW)
    assert res["reason"] == "picked" and res["ledger"]["status"] == "booked"
    rows = _shadow_rows(ledger)
    assert len(rows) == 1 and rows[0]["symbol"] == "NVDA"
    assert rows[0]["notes"] == "Shadow idea"
    assert idea_ledger.is_self_managed(rows[0]) and not idea_ledger.is_idea(rows[0])
    assert ledger.published == [{"reprice": False, "keep_live_pnl": True}]
    cached = bus.caches[handlers.CACHE_IDEA_SHADOW]
    assert cached["state"]["posts"][-1]["kind"] == "shadow"


def test_the_same_scan_seen_again_books_nothing(ledger):
    bus = _bus_after_one_scan()
    bus.caches["cache:options:scan"] = scan(directional=[call()])
    handlers.run_idea_shadow(bus, now=NOW)
    again = handlers.run_idea_shadow(bus, now=NOW + dt.timedelta(seconds=30))
    assert again["reason"] == "scan already checked"
    assert len(_shadow_rows(ledger)) == 1


def test_the_first_scan_of_the_day_books_nothing(ledger):
    bus = _Bus({"cache:options:scan": scan(directional=[call()])})
    res = handlers.run_idea_shadow(bus, now=NOW)
    assert res["reason"] == "first scan today" and _shadow_rows(ledger) == []


def test_mode_off_books_nothing(ledger, monkeypatch):
    from shared import paper_limits
    monkeypatch.setattr(paper_limits, "idea_shadow",
                        lambda: {**SHADOW_CFG, "mode": "off"})
    bus = _bus_after_one_scan()
    bus.caches["cache:options:scan"] = scan(directional=[call()])
    assert handlers.run_idea_shadow(bus, now=NOW)["reason"] == "off"
    assert _shadow_rows(ledger) == []


def test_idea_booking_off_does_not_stop_the_shadow(ledger, monkeypatch):
    """``[idea_trades] enabled`` stops booking POSTED ideas only."""
    from shared import paper_limits
    monkeypatch.setattr(paper_limits, "idea_trades", lambda: {**CFG, "enabled": False})
    bus = _bus_after_one_scan()
    bus.caches["cache:options:scan"] = scan(directional=[call()])
    assert handlers.run_idea_shadow(bus, now=NOW)["ledger"]["status"] == "booked"


def test_the_hourly_post_is_counted_against_the_limits(ledger):
    bus = _bus_after_one_scan()
    bus.caches[handlers.CACHE_TRADE_IDEA] = {
        "status": "posted", "at": (NOW - dt.timedelta(minutes=8)).isoformat(),
        "idea": {"symbol": "MU"}}
    bus.caches["cache:options:scan"] = scan(directional=[call()])
    res = handlers.run_idea_shadow(bus, now=NOW)
    assert res["reason"] == "last post 8 min ago" and _shadow_rows(ledger) == []


def test_a_failed_booking_is_not_counted_as_a_post(ledger, monkeypatch):
    monkeypatch.setattr(idea_ledger, "book",
                        lambda idea, now, source=None: {"status": "failed"})
    bus = _bus_after_one_scan()
    bus.caches["cache:options:scan"] = scan(directional=[call()])
    res = handlers.run_idea_shadow(bus, now=NOW)
    assert res["state"]["posts"] == []
    assert ledger.published == []


def test_a_shadow_row_is_outside_the_users_book():
    rows = [_manual("m1"), _manual("s1", source=idea_ledger.SHADOW_SOURCE, symbol="NVDA"),
            _manual("s2", "CLOSED", source=idea_ledger.SHADOW_SOURCE, realized_pnl=300.0)]
    state = compute.ledger_book_state(trades=rows)
    assert [r["symbol"] for r in state["open"]] == ["SPY"]
    assert state["realized_pnl"] == 0.0


def test_the_mark_cycle_marks_a_shadow_row(ledger, monkeypatch):
    import structure_marks
    seen = []
    monkeypatch.setattr(structure_marks, "reprice",
                        lambda t, client, today: seen.append(t["trade_id"]) or None)
    bus = _bus_after_one_scan()
    bus.caches["cache:options:scan"] = scan(directional=[call()])
    tid = handlers.run_idea_shadow(bus, now=NOW)["ledger"]["trade_id"]
    idea_ledger.mark_cycle(NOW + dt.timedelta(minutes=5), client="C")
    assert seen == [tid]


# ── the scheduler's gate ────────────────────────────────────────────────────
def test_due_when_the_scan_version_moves():
    assert scheduler.idea_shadow_due(7, None)
    assert scheduler.idea_shadow_due(8, 7)
    assert not scheduler.idea_shadow_due(7, 7)
    assert not scheduler.idea_shadow_due(None, None)
