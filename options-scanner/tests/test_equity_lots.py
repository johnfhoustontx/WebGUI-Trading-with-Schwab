"""Share inventory in the manual paper account — the `equity_lots` table.

An equity lot holds **cash converted into shares**, never reserved buying power.
That is the whole reason this is a separate table rather than a `kind` column on
`paper_positions`: `reconcile_buying_power` recomputes
`buying_power_reserved` as `Σ OPEN paper_positions.max_loss_total` and corrects
any drift against `cash`, so anything reserving buying power outside that sum is
silently zeroed at the next service start.
`test_a_lot_does_not_disturb_reconcile_buying_power` is the guard on that.
"""
import sqlite3

import paper_account_db as pad


def _row(db, lot_id):
    """Read a lot back straight from SQLite.

    Deliberately not a new `fetch_lots` helper: the closed side has exactly one
    consumer so far (this test), and public API added ahead of a caller is API
    nobody has agreed the shape of.
    """
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM equity_lots WHERE lot_id=?",
                                 (lot_id,)).fetchone())
    finally:
        conn.close()


def test_insert_and_fetch_an_open_lot(tmp_path):
    db = tmp_path / "acct.db"
    pad.init_db(db)
    lot_id = pad.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100,
                                        "cost_basis": 95.0, "source": "assignment"})
    lots = pad.fetch_open_lots(db)
    assert [l["symbol"] for l in lots] == ["AAPL"]
    assert lots[0]["lot_id"] == lot_id
    assert lots[0]["shares"] == 100
    assert lots[0]["cost_basis"] == 95.0
    assert lots[0]["source"] == "assignment"
    assert lots[0]["status"] == "OPEN"
    # opened_ts is stamped for the caller when it does not supply one, so a lot
    # can always be aged (the covered-call screen wants holding period).
    assert lots[0]["opened_ts"]


def test_a_closed_lot_leaves_the_open_set(tmp_path):
    """`fetch_open_lots` is what every consumer reads for 'what stock do I hold' —
    a called-away lot must drop out of it, not linger as phantom inventory."""
    db = tmp_path / "acct.db"
    pad.init_db(db)
    lot_id = pad.insert_equity_lot(db, {"symbol": "MSFT", "shares": 100,
                                        "cost_basis": 400.0})
    assert len(pad.fetch_open_lots(db)) == 1

    pad.close_equity_lot(db, lot_id, exit_price=410.0, reason="called_away")

    assert pad.fetch_open_lots(db) == []
    closed = _row(db, lot_id)
    assert closed["status"] == "CLOSED"
    assert closed["exit_price"] == 410.0
    assert closed["exit_reason"] == "called_away"
    # One lot per assignment, closed whole (partial disposal is deliberately not
    # built), so realized P&L is simply the whole-lot move off the basis.
    assert closed["realized_pnl"] == 1000.0
    assert closed["closed_ts"]


def test_equity_at_cost_sums_open_lots_only(tmp_path):
    db = tmp_path / "acct.db"
    pad.init_db(db)
    pad.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100, "cost_basis": 95.0})
    closed = pad.insert_equity_lot(db, {"symbol": "MSFT", "shares": 100, "cost_basis": 400.0})
    pad.close_equity_lot(db, closed, exit_price=410.0, reason="called_away")
    assert pad.equity_at_cost(db) == 9500.0


def test_equity_at_cost_is_zero_with_no_lots(tmp_path):
    """A cold table must read 0.0, never None — this term is added to cash in
    `roll_session_if_needed`, and a None there would raise on the roll."""
    db = tmp_path / "acct.db"
    pad.init_db(db)
    assert pad.equity_at_cost(db) == 0.0


def test_a_lot_does_not_disturb_reconcile_buying_power(tmp_path):
    """THE invariant. reconcile recomputes reserved from OPEN paper_positions
    alone; a lot holds cash-converted-to-shares, never a reservation. If this
    ever fails, the lot model has drifted and reconcile will silently zero
    whatever it reserved."""
    db = tmp_path / "acct.db"
    pad.init_db(db)
    pad.ensure_account(db, starting_balance=25_000.0, session_date="2026-09-08")
    pad.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100, "cost_basis": 95.0})
    assert pad.reconcile_buying_power(db) == 0.0
    # And the account itself is untouched — a lot is not a reservation, so
    # neither cash nor reserved may have moved.
    a = pad.get_account(db)
    assert a["buying_power_reserved"] == 0.0
    assert a["cash"] == 25_000.0


def test_session_start_equity_includes_shares_at_cost(tmp_path):
    """Shares held at cost basis are committed capital by the same definition
    that puts buying_power_reserved in this sum. Omitting them understates
    equity for any session that opens holding stock.

    ⚠ Measured 2026-09-05: nothing reads `session_start_equity` yet — the live
    drawdown guard is `should_halt` against the absolute-dollar
    `config_paper.MAX_SESSION_DRAWDOWN`. This pins the stored value for the
    first reader; it is not a fix to a guard that is loose today.
    """
    db = tmp_path / "acct.db"
    pad.init_db(db)
    pad.ensure_account(db, starting_balance=25_000.0, session_date="2026-09-07")
    pad.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100, "cost_basis": 95.0})

    pad.roll_session_if_needed(db, "2026-09-08")

    assert pad.get_account(db)["session_start_equity"] == 25_000.0 + 9_500.0


def test_session_start_equity_excludes_a_closed_lot(tmp_path):
    """A called-away lot is cash again — counting it here would double it, since
    the disposal proceeds already landed in `cash`."""
    db = tmp_path / "acct.db"
    pad.init_db(db)
    pad.ensure_account(db, starting_balance=25_000.0, session_date="2026-09-07")
    lot_id = pad.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100,
                                        "cost_basis": 95.0})
    pad.close_equity_lot(db, lot_id, exit_price=99.0, reason="called_away")

    pad.roll_session_if_needed(db, "2026-09-08")

    assert pad.get_account(db)["session_start_equity"] == 25_000.0


def test_reconcile_would_zero_a_lot_that_reserved(tmp_path):
    """The negative half of the invariant: proves the guard above is not vacuous.

    Reserving against a lot the way an option position does is exactly what the
    separate table forbids — and here is what it costs. reconcile sees no OPEN
    `paper_positions` row backing the reservation, calls the whole thing drift,
    and silently returns it to cash. Written as a demonstration rather than a
    behaviour we ship: nothing in the lot API reserves, and nothing may."""
    db = tmp_path / "acct.db"
    pad.init_db(db)
    pad.ensure_account(db, starting_balance=25_000.0, session_date="2026-09-08")
    pad.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100, "cost_basis": 95.0})
    pad.reserve_buying_power(db, 9_500.0)          # the mistake, made explicitly

    assert pad.reconcile_buying_power(db) == 9_500.0        # zeroed, not honoured
    assert pad.get_account(db)["buying_power_reserved"] == 0.0
