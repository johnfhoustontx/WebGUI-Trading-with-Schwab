"""A daily-condor row rides the trade ideas' book mechanics: the user's own Ledger
passes (caps, equity, the per-row chain reprice, generic exits, the expiry pass,
the action list) never see it, the ideas' 5-minute cycle marks, stops and settles
it, and "Delete all closed" clears it rather than deleting it.

What stays IDEA-only is pinned too: a condor row is not a trade idea, so the weekly
review must never rate it.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute, idea_ledger
from services.options_svc.tests.test_idea_ledger import cycle  # noqa: F401 (fixture)
from services.options_svc.tests.test_idea_ledger_wiring import (  # noqa: F401 (fixtures)
    _manual, ledger, view)

_CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 17, 10, 35, tzinfo=_CT)


def _condor(trade_id, status="OPEN", **over):
    row = _manual(trade_id, status, source=idea_ledger.CONDOR_SOURCE, symbol="QQQ",
                  strategy="IC", call_short=520.0, call_long=525.0)
    row.update(over)
    return row


def test_the_sources():
    assert idea_ledger.CONDOR_SOURCE == "daily_condor"
    assert idea_ledger.SELF_MANAGED_SOURCES == {"trade_idea", "daily_condor"}
    assert idea_ledger.is_self_managed(_condor("c1"))
    assert idea_ledger.is_self_managed({"source": "trade_idea"})
    assert not idea_ledger.is_self_managed(_manual("m1"))
    assert not idea_ledger.is_self_managed(None)


def test_a_condor_row_is_not_a_trade_idea():
    assert not idea_ledger.is_idea(_condor("c1"))


def test_the_two_self_managed_source_sets_agree():
    """options-scanner cannot import services, so ``trades_db`` carries its own copy."""
    import trades_db
    assert set(trades_db.SELF_MANAGED_SOURCES) == set(idea_ledger.SELF_MANAGED_SOURCES)


def test_condor_rows_excluded_from_caps_and_equity():
    rows = [_manual("m1"), _condor("c1"), _condor("c2", "CLOSED", realized_pnl=500.0)]
    state = compute.ledger_book_state(trades=rows)
    assert [r["symbol"] for r in state["open"]] == ["SPY"]
    assert state["realized_pnl"] == 0.0


def test_manage_ledger_skips_condor_rows(ledger, monkeypatch):
    import signal_repricer
    repriced = []
    monkeypatch.setattr(signal_repricer, "reprice_legs",
                        lambda t, client: repriced.append(t["trade_id"]) or {})
    ledger.add_trade(_manual("m1", direction="DEBIT", entry_credit=-2.5,
                             expiration="2026-10-30"))
    ledger.add_trade(_condor("c1", direction="DEBIT", entry_credit=-2.5,
                             expiration="2026-10-30"))
    compute.manage_ledger_trades(now_ct=NOW)
    assert repriced == ["m1"]


def test_expire_ledger_skips_condor_rows(ledger, monkeypatch):
    import paper_engine
    monkeypatch.setattr(paper_engine, "settlement_underlying", lambda *a: 100.0)
    ledger.add_trade(_condor("c1", expiration="2026-09-10"))
    ledger.add_trade(_manual("m1", expiration="2026-09-10"))
    assert compute.expire_ledger_trades(now_ct=NOW) == 1
    by_id = {t["trade_id"]: t for t in ledger.get_all_trades()}
    assert by_id["c1"]["status"] == "OPEN" and by_id["m1"]["status"] == "EXPIRED"


def test_reprice_view_shows_the_condor_mark_and_fetches_no_chain(view, ledger):
    session, repriced = view
    ledger.add_trade(_condor("c1", quantity=2, last_mark_pnl=-40.0))
    got = {t["trade_id"]: t.get("unrealized_pnl")
           for t in compute.paper_trades_view(reprice=True)["trades"]}
    assert got["c1"] == -80.0 and "c1" not in repriced


def test_expiring_condor_row_is_not_the_users_action_item(ledger, monkeypatch):
    monkeypatch.setattr(compute, "reprice_captured", lambda: {"signals": [], "flags": []})
    monkeypatch.setattr(compute, "_load_open_positions", lambda: [])
    ledger.add_trade(_manual("m1", expiration="2026-09-17"))
    ledger.add_trade(_condor("c1", expiration="2026-09-17"))
    out = compute.collect_action_items(now_ct=NOW)
    assert [r["symbol"] for r in out["expiring_today"]] == ["SPY"]


def test_manual_close_of_a_condor_row_is_the_idea_close(ledger):
    ledger.add_trade(_condor("c1"))
    compute.close_paper("c1", 0.40)
    (row,) = ledger.get_all_trades()
    assert row["status"] == "CLOSED" and row["exit_reason"] == "MANUAL"
    assert row["realized_pnl"] == pytest.approx(60.0)


def test_mark_cycle_stops_a_condor_row(ledger, cycle):  # noqa: F811
    ledger.add_trade(_condor("c1", stop_pnl=200.0, target_pnl=None, mae_pnl=0.0,
                             expiration="2026-09-17"))
    cycle["values"]["c1"] = 3.10                                # -2.10 a share: -210
    out = idea_ledger.mark_cycle(NOW)
    assert out["closed"] == 1
    (row,) = ledger.get_all_trades()
    assert row["exit_reason"] == "STOP" and row["realized_pnl"] == pytest.approx(-210.0)


def test_mark_cycle_holds_a_condor_row_short_of_its_stop(ledger, cycle):  # noqa: F811
    ledger.add_trade(_condor("c1", stop_pnl=200.0, target_pnl=None, mae_pnl=0.0,
                             expiration="2026-09-17"))
    cycle["values"]["c1"] = 0.05                                # +95: no target
    assert idea_ledger.mark_cycle(NOW) == {"marked": 1, "closed": 0, "settled": 0,
                                           "skipped": 0}


def test_delete_closed_clears_condor_rows(ledger):
    ledger.add_trade(_manual("m1", "CLOSED"))
    ledger.add_trade(_condor("c1", "CLOSED"))
    ledger.add_trade(_condor("c2", "OPEN"))
    assert ledger.delete_closed_trades() == 1
    kept = {t["trade_id"]: t for t in ledger.get_all_trades()}
    assert sorted(kept) == ["c1", "c2"]
    assert kept["c1"]["cleared_at"] and not kept["c2"].get("cleared_at")


def test_weekly_review_ignores_condor_rows():
    from shared import idea_review
    from services.options_svc.tests.test_idea_ledger import CFG
    rows = [_condor("c1", "CLOSED", realized_pnl=-200.0, exit_time=NOW.isoformat()),
            _condor("c2")]
    got = idea_review.facts(rows, NOW.date(), CFG)
    assert got["open"] == [] and got["closed_week"] == []
