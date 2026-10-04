"""The paper books under two writers.

Audit AR-02 (2026-10-03). The options service mutates the paper books from two
kinds of thread: the scheduler's (the hourly manage cycle, the 15:05 settle
pass) and the command consumer's (a manual close, a Rescue apply, a Paper
click). Nothing serialized them, a close was three separate commits, and
``close_position`` had no ``status = 'OPEN'`` condition - so a second close of
the same position overwrote the first's realized P&L, released its buying power
a second time and realized its P&L a second time.

Three guards, each tested here on the real store:

* ``paper_lock.BOOK_LOCK`` - one re-entrant lock every book-mutating entry
  point holds;
* a close only closes an OPEN row, and says whether it did;
* a close is ONE transaction: the row, the released buying power and the
  realized P&L land together or not at all.
"""
import threading

import pytest

import paper_account_db as pdb
import paper_adjust
import paper_engine as pe
import paper_lock


def _open(db, *, credit=1.00, risk=400.0, qty=1):
    pdb.reserve_buying_power(db, risk)
    return pdb.insert_position(db, {
        "signal_id": None, "symbol": "SPY", "strategy": "PCS",
        "short_strike": 500.0, "long_strike": 495.0, "call_short": None,
        "call_long": None, "width": 5.0, "expiration": "2026-06-19",
        "dte_at_entry": 14, "quantity": qty, "entry_credit": credit,
        "entry_order_id": None, "max_loss_per": risk / qty,
        "max_loss_total": risk, "entry_ts": "2026-06-05T09:00:00"})


def _position(db, position_id):
    return next(p for p in pdb.fetch_all_positions(db) if p["position_id"] == position_id)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "acct.db")
    pdb.ensure_account(path, 25_000.0, "2026-06-05")
    return path


# ── a close only closes an OPEN row ──────────────────────────────────────────

def test_close_position_reports_that_it_closed_an_open_row(db):
    pid = _open(db)
    assert pdb.close_position(db, pid, exit_debit=0.40, exit_order_id=None,
                              realized_pnl=60.0, exit_reason="TARGET_HIT",
                              exit_ts="t1") is True
    assert _position(db, pid)["status"] == "CLOSED"


def test_a_second_close_changes_nothing_and_says_so(db):
    pid = _open(db)
    pdb.close_position(db, pid, exit_debit=0.40, exit_order_id=None,
                       realized_pnl=60.0, exit_reason="TARGET_HIT", exit_ts="t1")
    assert pdb.close_position(db, pid, exit_debit=3.00, exit_order_id=None,
                              realized_pnl=-200.0, exit_reason="MANUAL_CLOSE",
                              exit_ts="t2") is False
    row = _position(db, pid)
    assert (row["realized_pnl"], row["exit_reason"], row["exit_ts"]) == \
        (60.0, "TARGET_HIT", "t1")


def test_closing_a_position_that_does_not_exist_reports_false(db):
    assert pdb.close_position(db, 999, exit_debit=0.0, exit_order_id=None,
                              realized_pnl=0.0, exit_reason="x", exit_ts="t") is False


# ── the engine's close: once, and all of it or none of it ────────────────────

def _account(db):
    a = pdb.get_account(db)
    return a["cash"], a["buying_power_reserved"], a["realized_pnl"]


def test_the_engines_close_moves_the_account_exactly_once(db):
    pid = _open(db)
    pos = _position(db, pid)
    assert pe._close(db, pos, exit_debit=0.40, exit_order_id=None,
                     realized_pnl=60.0, reason="TARGET_HIT", status="CLOSED") is True
    after_first = _account(db)
    assert after_first == (25_060.0, 0.0, 60.0)
    # A second writer still holding the OPEN row it read a moment ago.
    assert pe._close(db, pos, exit_debit=3.00, exit_order_id=None,
                     realized_pnl=-200.0, reason="MANUAL_CLOSE", status="CLOSED") is False
    assert _account(db) == after_first
    assert _position(db, pid)["realized_pnl"] == 60.0


def test_an_interrupted_close_leaves_the_position_open_and_the_account_untouched(db, monkeypatch):
    """The row, the released buying power and the realized P&L are one
    transaction. As three commits, a failure after the first left a CLOSED
    position whose risk was still reserved and whose P&L was never booked."""
    pid = _open(db)
    pos = _position(db, pid)
    before = _account(db)
    real = pdb._update_account

    def _fail(conn, **fields):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pdb, "_update_account", _fail)
    with pytest.raises(RuntimeError):
        pe._close(db, pos, exit_debit=0.40, exit_order_id=None,
                  realized_pnl=60.0, reason="TARGET_HIT", status="CLOSED")
    monkeypatch.setattr(pdb, "_update_account", real)
    assert _position(db, pid)["status"] == "OPEN"
    assert _account(db) == before


def test_a_rescue_close_of_an_already_closed_position_books_nothing(db):
    pid = _open(db)
    pos = _position(db, pid)                       # the stale OPEN read
    pe._close(db, pos, exit_debit=0.40, exit_order_id=None,
              realized_pnl=60.0, reason="TARGET_HIT", status="CLOSED")
    after_first = _account(db)
    out = paper_adjust.apply_close(db, pos, {"action": "close", "net_cash": -300.0,
                                             "commission": 2.60,
                                             "est_fill_legs": []})
    assert out["ok"] is False
    assert _account(db) == after_first             # not even the commission
    assert pdb.list_adjustments(db, pid) == []


# ── one lock, held by every entry point that mutates a book ──────────────────

ENGINE_ENTRY_POINTS = [
    (pe, "run_entry_cycle"), (pe, "run_manage_cycle"), (pe, "run_settle_cycle"),
    (paper_adjust, "apply_adjustment"), (paper_adjust, "apply_close"),
    (paper_adjust, "apply_partial_close"), (paper_adjust, "apply_narrow"),
    (paper_adjust, "apply_convert_ic"), (paper_adjust, "apply_convert_butterfly"),
    (paper_adjust, "apply_roll"), (paper_adjust, "apply_inverted"),
]


@pytest.mark.parametrize("module,name", ENGINE_ENTRY_POINTS,
                         ids=[f"{m.__name__}.{n}" for m, n in ENGINE_ENTRY_POINTS])
def test_every_engine_entry_point_holds_the_book_lock(module, name):
    assert paper_lock.is_serialized(getattr(module, name)), name


def test_the_lock_is_re_entrant():
    @paper_lock.serialized
    def inner():
        return "in"

    @paper_lock.serialized
    def outer():
        return inner()

    assert outer() == "in"


def test_a_second_writer_waits_for_the_first():
    order, first_inside, release = [], threading.Event(), threading.Event()

    @paper_lock.serialized
    def first():
        first_inside.set()
        release.wait(5)
        order.append("first done")

    @paper_lock.serialized
    def second():
        order.append("second ran")

    t1 = threading.Thread(target=first)
    t1.start()
    assert first_inside.wait(5)
    t2 = threading.Thread(target=second)
    t2.start()
    t2.join(0.3)
    assert t2.is_alive() and order == []           # blocked behind the first
    release.set()
    t1.join(5)
    t2.join(5)
    assert order == ["first done", "second ran"]


def test_a_writer_that_raises_releases_the_lock():
    @paper_lock.serialized
    def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        boom()
    assert paper_lock.BOOK_LOCK.acquire(timeout=1)
    paper_lock.BOOK_LOCK.release()
