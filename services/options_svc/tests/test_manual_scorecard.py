"""C5: the manual paper book gets the scorecard the driver already had.

Design: docs/plans/2026-09-12-manual-scorecard-design.md.

The manual account is the book that auto-trades every captured signal, and it had
**no track record on screen at all** — no win rate, no profit factor, no
breakdown. ``driver_perf.build_scorecard`` is already pure over
``(positions, snapshot)``, so the only missing pieces were an accessor for the
default DB and a new breakdown axis.

⚠ ``by_exit_reason`` is that axis, and it earned its place: replaying the
profit-lock ladder (C2) turned up that ``MANUAL_CLOSE`` accounts for **+$50,102**
of the captured book's reported P&L against +$11,664 for every other reason
combined, with 130 of its 388 rows booking exactly the full credit. A scorecard
split only by symbol and strategy cannot show that.
"""
import pytest

from services.options_svc import compute, driver_perf


def _pos(symbol="MU", strategy="PCS", pnl=100.0, reason="MANUAL_CLOSE",
         status="CLOSED"):
    return {"symbol": symbol, "strategy": strategy, "realized_pnl": pnl,
            "exit_reason": reason, "status": status}


# ── the new breakdown axis ──────────────────────────────────────────────────

def test_the_scorecard_breaks_pnl_down_by_exit_reason():
    card = driver_perf.build_scorecard(
        [_pos(pnl=500.0, reason="MANUAL_CLOSE"),
         _pos(pnl=-80.0, reason="MONEY_STOP"),
         _pos(pnl=-40.0, reason="MONEY_STOP")], {})
    rows = {r["exit_reason"]: r for r in card["by_exit_reason"]}
    assert rows["MANUAL_CLOSE"]["pnl"] == pytest.approx(500.0)
    assert rows["MANUAL_CLOSE"]["trades"] == 1
    assert rows["MONEY_STOP"]["pnl"] == pytest.approx(-120.0)
    assert rows["MONEY_STOP"]["win_rate"] == pytest.approx(0.0)


def test_exit_reason_rows_sort_by_pnl_like_the_other_breakdowns():
    card = driver_perf.build_scorecard(
        [_pos(pnl=-80.0, reason="MONEY_STOP"),
         _pos(pnl=500.0, reason="MANUAL_CLOSE")], {})
    assert [r["exit_reason"] for r in card["by_exit_reason"]] == \
        ["MANUAL_CLOSE", "MONEY_STOP"]


def test_a_missing_exit_reason_buckets_as_a_question_mark():
    card = driver_perf.build_scorecard([_pos(reason=None)], {})
    assert card["by_exit_reason"][0]["exit_reason"] == "?"


def test_OPEN_positions_are_excluded_from_every_breakdown():
    """The breakdowns are a record of what HAPPENED; an open position has no
    exit reason and no realized P&L to attribute."""
    card = driver_perf.build_scorecard(
        [_pos(status="OPEN", pnl=None, reason=None), _pos(pnl=10.0)], {})
    assert sum(r["trades"] for r in card["by_exit_reason"]) == 1
    assert card["open"] == 1 and card["closed"] == 1


def test_the_existing_breakdowns_are_untouched():
    card = driver_perf.build_scorecard([_pos(symbol="MU", strategy="PCS")], {})
    assert card["by_symbol"][0]["symbol"] == "MU"
    assert card["by_strategy"][0]["strategy"] == "PCS"


# ── the manual book's accessor ───────────────────────────────────────────────

def test_manual_account_perf_scores_the_DEFAULT_book(monkeypatch):
    """``db_path=None`` is the manual account throughout this engine — the same
    convention ``manual_analytics`` and ``paper_account_view`` use."""
    seen = {}

    import paper_account_db
    import paper_engine

    def _all(db_path=None, *a, **k):
        seen["db_path"] = db_path
        return [_pos(pnl=250.0)]

    monkeypatch.setattr(paper_account_db, "fetch_all_positions", _all)
    monkeypatch.setattr(paper_engine, "account_snapshot",
                        lambda *a, **k: {"open_unrealized": -5.0,
                                         "session_pnl": 12.0})
    card = compute.manual_account_perf()
    assert seen["db_path"] is None
    assert card["realized_pnl"] == pytest.approx(250.0)
    assert card["open_unrealized"] == pytest.approx(-5.0)


def test_manual_account_perf_is_NOT_the_driver_book(monkeypatch):
    """The two books are separate files; scoring the wrong one would silently
    report the driver's −46.6% record as the manual account's."""
    import paper_account_db
    import paper_engine
    monkeypatch.setattr(paper_engine, "account_snapshot", lambda *a, **k: {})
    calls = []
    monkeypatch.setattr(paper_account_db, "fetch_all_positions",
                        lambda db_path=None, *a, **k: calls.append(db_path) or [])
    compute.manual_account_perf()
    assert calls == [None]
    assert compute.DRIVER_PAPER_DB not in calls


def test_manual_account_perf_degrades_to_an_empty_card(monkeypatch):
    """It feeds a page card; a failed read must render "no trades", not a traceback."""
    import paper_account_db
    import paper_engine

    def _boom(*a, **k):
        raise RuntimeError("locked")

    monkeypatch.setattr(paper_account_db, "fetch_all_positions", _boom)
    monkeypatch.setattr(paper_engine, "account_snapshot", _boom)
    card = compute.manual_account_perf()
    assert card["total_trades"] == 0


def test_injected_positions_avoid_a_refetch(monkeypatch):
    """``paper_account_view`` already reads the book; the publisher passes it in."""
    import paper_account_db
    import paper_engine
    monkeypatch.setattr(paper_engine, "account_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(paper_account_db, "fetch_all_positions",
                        lambda *a, **k: pytest.fail("should not refetch"))
    card = compute.manual_account_perf(positions=[_pos(pnl=7.0)])
    assert card["realized_pnl"] == pytest.approx(7.0)
