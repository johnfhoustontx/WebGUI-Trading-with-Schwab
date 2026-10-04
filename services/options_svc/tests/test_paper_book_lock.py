"""Every service entry point that mutates a paper book holds the one book lock.

Audit AR-02 (2026-10-03). The engine half - the lock itself, the status-guarded
close, the one-transaction close - is tested on the real store in
``options-scanner/tests/test_paper_book_integrity.py``. This file pins the
SERVICE side: which functions take the lock, and that the Ledger's manual close
cannot re-close a trade.

The scheduler runs the cycles on executor threads while the command consumer
runs the user's clicks on another; these are the functions the two can meet in.
"""
import ast
import pathlib

import pytest

import paper_lock
from services.options_svc import compute, handlers

COMPUTE_ENTRY_POINTS = [
    "open_income_position",       # Account: reserve, then insert
    "reset_paper_account",
    "reconcile_paper_buying_power",
    "create_paper_trade",         # Ledger: read the book, check the caps, insert
    "close_paper",
    "delete_paper",
    "delete_closed_paper",
    "expire_ledger_trades",
    "manage_ledger_trades",
]


@pytest.mark.parametrize("name", COMPUTE_ENTRY_POINTS)
def test_the_compute_entry_point_holds_the_book_lock(name):
    assert paper_lock.is_serialized(getattr(compute, name)), name


def test_the_rescue_apply_reads_and_applies_under_one_hold():
    # It loads the position and then applies to it; unlocked, the manage cycle
    # could close the position between the two.
    assert paper_lock.is_serialized(handlers.run_rescue_apply)


def test_compute_uses_the_engines_lock_not_a_second_one():
    assert compute._paper_lock.BOOK_LOCK is paper_lock.BOOK_LOCK


def test_no_other_compute_function_writes_the_ledger_unlocked():
    """Any function in compute.py that calls a Ledger WRITE on ``paper_trader``
    must be in the list above. A new writer added without the lock fails here."""
    # ``paper_trader.create_paper_trade`` only BUILDS the trade dict; the row is
    # written by ``add_trade``.
    writes = {"add_trade", "update_trade", "delete_trade", "delete_closed_trades"}
    source = pathlib.Path(compute.__file__).read_text(encoding="utf-8")
    writers = set()
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "paper_trader"
                    and node.func.attr in writes):
                writers.add(fn.name)
    assert writers, "the walk found no Ledger writer at all"
    assert writers <= set(COMPUTE_ENTRY_POINTS), writers - set(COMPUTE_ENTRY_POINTS)


# ── the Ledger's manual close ────────────────────────────────────────────────

@pytest.fixture
def ledger(monkeypatch):
    import paper_trader
    store = {"trade": None, "updates": []}
    monkeypatch.setattr(compute, "_find_trade", lambda tid: store["trade"])
    monkeypatch.setattr(paper_trader, "update_trade",
                        lambda tid, row: store["updates"].append((tid, dict(row))))
    return store


def _trade(status):
    return {"trade_id": "T1", "status": status, "symbol": "SPY", "strategy": "PCS",
            "direction": "CREDIT", "expiration": "2026-06-19", "short_strike": 500.0,
            "long_strike": 495.0, "entry_credit": 1.0, "quantity": 1,
            "realized_pnl": 60.0 if status != "OPEN" else None}


def test_closing_an_open_ledger_trade_writes_the_close(ledger):
    ledger["trade"] = _trade("OPEN")
    compute.close_paper("T1", 0.40)
    ((tid, row),) = ledger["updates"]
    assert tid == "T1" and row["status"] == "CLOSED"
    assert row["realized_pnl"] == 60.0             # (1.00 - 0.40) x 100


@pytest.mark.parametrize("status", ["CLOSED", "EXPIRED"])
def test_closing_a_ledger_trade_that_is_already_closed_writes_nothing(ledger, status):
    """The second close used to overwrite the first: the trade's exit price,
    reason and realized P&L became whatever the later click said."""
    ledger["trade"] = _trade(status)
    compute.close_paper("T1", 3.00)
    assert ledger["updates"] == []


def test_closing_a_ledger_trade_that_is_gone_writes_nothing(ledger):
    ledger["trade"] = None
    compute.close_paper("T1", 0.40)
    assert ledger["updates"] == []
