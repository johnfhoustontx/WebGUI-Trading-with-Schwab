"""A posted trade idea is booked into the Paper Ledger, and the user's own Ledger book
(caps, equity, generic exits, the expiry pass, the per-row chain reprice) never sees it.

Booking is ``idea_ledger.book`` (its own tests are ``test_idea_ledger.py``); this file
pins the two seams: ``handlers.run_trade_idea`` calls it after a post and never lets it
cost the post, and ``compute`` leaves every idea row out of the user's book.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.options_svc import compute, handlers, idea_ledger, push_notify
from services.options_svc.tests.test_idea_ledger import CFG, STRADDLE_IDEA
from services.options_svc.tests.test_trade_idea import _Bus, _scan

_CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 17, 10, 35, tzinfo=_CT)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A real trades.db under tmp_path with idea booking on."""
    import paper_trader
    import trade_tracker_client
    import trades_db

    from shared import paper_limits
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(trades_db, "_initialised", set())
    monkeypatch.setattr(trade_tracker_client, "track", lambda t: True)
    monkeypatch.setattr(trade_tracker_client, "untrack", lambda tid: True)
    monkeypatch.setattr(paper_limits, "idea_trades", lambda: dict(CFG))
    return paper_trader


@pytest.fixture
def post(monkeypatch):
    """The hourly slot with every outbound send stubbed; ``sent`` flips the result."""
    state = {"sent": True}
    monkeypatch.setattr(push_notify, "trade_idea_config",
                        lambda config=None: {"enabled": True})
    monkeypatch.setattr(push_notify, "send_trade_idea",
                        lambda idea, **kw: state["sent"])
    monkeypatch.setattr(push_notify, "trade_idea_png", lambda idea, **kw: b"PNG")
    monkeypatch.setattr(handlers.x_post, "post", lambda *a, **k: {"ok": True})
    return state


def _manual(trade_id, status="OPEN", **over):
    row = {"trade_id": trade_id, "mode": "PAPER", "status": status, "symbol": "SPY",
           "strategy": "PCS", "expiration": "2026-09-25", "quantity": 1,
           "entry_credit": 1.0, "max_loss_total": 400.0, "short_strike": 495.0,
           "long_strike": 490.0, "entry_time": "2026-09-16T09:00:00"}
    row.update(over)
    return row


def _idea_row(trade_id, status="OPEN", **over):
    return _manual(trade_id, status, source=idea_ledger.SOURCE, symbol="QQQ", **over)


# ── the hook ────────────────────────────────────────────────────────────────
def test_posted_idea_is_booked(ledger, post):
    res = handlers.run_trade_idea(_Bus({"cache:options:scan": _scan()}), "h1035", now=NOW)
    assert res["status"] == "posted" and res["ledger"]["status"] == "booked"
    rows = [t for t in ledger.get_all_trades() if idea_ledger.is_idea(t)]
    assert len(rows) == 1 and rows[0]["trade_id"] == res["ledger"]["trade_id"]
    assert rows[0]["idea_id"] == res["idea"]["id"]
    assert rows[0]["idea_posted"] == NOW.isoformat(timespec="seconds")


def test_unsent_idea_is_not_booked(ledger, post):
    post["sent"] = False
    res = handlers.run_trade_idea(_Bus({"cache:options:scan": _scan()}), "h1035", now=NOW)
    assert res["status"] == "skipped" and "ledger" not in res
    assert ledger.get_all_trades() == []


def test_booking_failure_never_costs_the_post(ledger, post, monkeypatch):
    def boom(idea, now):
        raise RuntimeError("proxy tracker down")
    monkeypatch.setattr(idea_ledger, "book", boom)
    _degrade.reset()
    res = handlers.run_trade_idea(_Bus({"cache:options:scan": _scan()}), "h1035", now=NOW)
    assert res["status"] == "posted" and res["ledger"] == {"status": "failed"}
    assert _degrade.counts().get("options.run_trade_idea.ledger") == 1


# ── the user's book never sees an idea row ──────────────────────────────────
def test_idea_rows_excluded_from_caps_and_equity():
    rows = [_manual("m1"), _idea_row("i1"),
            _idea_row("i2", "CLOSED", realized_pnl=500.0)]
    state = compute.ledger_book_state(trades=rows)
    assert [r["symbol"] for r in state["open"]] == ["SPY"]
    assert state["realized_pnl"] == 0.0 and state["equity"] == state["starting_balance"]


def test_manage_ledger_skips_idea_rows(ledger, monkeypatch):
    import signal_repricer
    ledger.add_trade(_idea_row("i1", direction="DEBIT", entry_credit=-2.5,
                               expiration="2026-10-30"))

    def must_not_run(*a, **k):
        raise AssertionError("an idea row reached the generic exit pass")
    monkeypatch.setattr(signal_repricer, "reprice_legs", must_not_run)
    assert compute.manage_ledger_trades(now_ct=NOW) == 0


def test_expire_ledger_skips_idea_rows(ledger, monkeypatch):
    ledger.add_trade(_idea_row("i1", expiration="2026-09-10"))
    ledger.add_trade(_manual("m1", expiration="2026-09-10"))
    import paper_engine
    monkeypatch.setattr(paper_engine, "settlement_underlying", lambda *a: 100.0)
    assert compute.expire_ledger_trades(now_ct=NOW) == 1
    by_id = {t["trade_id"]: t for t in ledger.get_all_trades()}
    assert by_id["i1"]["status"] == "OPEN" and by_id["m1"]["status"] == "EXPIRED"


def test_reprice_view_uses_last_mark_for_idea_rows(monkeypatch):
    import signal_repricer

    def must_not_fetch(*a, **k):
        raise AssertionError("an idea row was repriced off a chain")
    monkeypatch.setattr(signal_repricer, "reprice_swing", must_not_fetch)
    monkeypatch.setattr(signal_repricer, "reprice_legs", must_not_fetch)
    rows = [_idea_row("i1", quantity=3, last_mark_pnl=-12.5),
            _idea_row("i2", last_mark_pnl=None)]
    compute._reprice_open_pnl(rows)
    assert rows[0]["unrealized_pnl"] == -37.5
    assert "unrealized_pnl" not in rows[1]


# ── Delete closed trades ────────────────────────────────────────────────────
def test_delete_closed_spares_idea_rows(ledger):
    ledger.add_trade(_manual("m1", "CLOSED"))
    ledger.add_trade(_idea_row("i1", "CLOSED"))
    ledger.add_trade(_manual("m2", "OPEN"))
    assert ledger.delete_closed_trades() == 1
    assert sorted(t["trade_id"] for t in ledger.get_all_trades()) == ["i1", "m2"]


def test_the_two_idea_source_strings_agree():
    """options-scanner cannot import services, so ``trades_db`` carries its own copy."""
    import trades_db
    assert trades_db.IDEA_SOURCE == idea_ledger.SOURCE == "trade_idea"


# ── an analysis-only structure is stored as an idea but still refused by the button ──
def test_idea_straddle_row_is_stored_but_button_still_refuses(ledger):
    import paper_trader
    out = idea_ledger.book(STRADDLE_IDEA, NOW)
    assert out["status"] == "booked"
    stored = [t for t in ledger.get_all_trades() if idea_ledger.is_idea(t)]
    assert [t["strategy"] for t in stored] == ["LONG_STRADDLE"]
    with pytest.raises(ValueError, match="not paper-tradeable"):
        paper_trader.create_paper_trade({"type": "LONG_STRADDLE", "symbol": "XYZ"}, 1)
