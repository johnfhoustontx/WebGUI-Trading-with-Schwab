"""The driver's OPEN-path capacity gates actually read the book.

``_driver_open_positions`` called ``paper_account_db.list_open_positions`` — a
function that **does not exist**. Its own `except Exception -> []` swallowed the
`AttributeError`, so both gates below measured an always-empty book and could
never refuse anything:

* ``max_concurrent`` — the decision path counts slots per CYCLE, so a direct
  enqueue skipped it entirely and this was the only check standing.
* ``daily_risk_budget`` — the decision path resets it every 30-minute checkpoint
  and never subtracts deployed risk, so counting the OPEN positions here is the
  only thing that makes the budget mean its name.

CLAUDE.md calls this re-check "not redundant, and removing it re-opens a real
hole". A typo had removed it. Measured on prod 2026-09-11: the degrade had fired
**50 times in 30 days**, and it speaks at WARNING with a traceback — nobody read
it. The book was small enough that nothing was breached, so the only visible
symptom was a counter on the Status page.

These tests drive the gate through a real tmp book, so they fail if the read
comes back empty for ANY reason — a wrong name, a wrong DB path, or a swallowed
error.
"""
import paper_account_db
import pytest

from services.options_svc import compute


def _position(**kw):
    base = {"signal_id": "s", "symbol": "MU", "strategy": "PCS",
            "short_strike": 105.0, "long_strike": 103.0, "call_short": None,
            "call_long": None, "width": 2.0, "expiration": "2026-07-10",
            "dte_at_entry": 15, "quantity": 1, "entry_credit": 1.50,
            "entry_order_id": None, "max_loss_per": 50.0, "max_loss_total": 50.0,
            "entry_ts": "t"}
    base.update(kw)
    return base


def _signal(**kw):
    base = {"signal_id": "MU_PCS_x", "symbol": "MU", "strategy": "PCS",
            "short_strike": 105.0, "long_strike": 103.0, "call_short": None,
            "call_long": None, "width": 2.0, "expiration": "2026-07-10",
            "dte_at_entry": 15, "entry_credit": 1.50, "source": "driver"}
    base.update(kw)
    return base


@pytest.fixture
def book(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account(starting_balance=25_000.0)
    return db


def test_the_open_path_can_see_the_positions_already_in_the_book(book):
    """The narrowest statement of the bug: the reader returned [] for a book with
    rows in it."""
    paper_account_db.insert_position(book, _position())
    paper_account_db.insert_position(book, _position(signal_id="s2"))

    assert len(compute._driver_open_positions()) == 2


def test_max_concurrent_refuses_once_the_book_is_full(book, monkeypatch):
    monkeypatch.setattr(compute._driver_limits, "risk",
                        lambda: {"max_concurrent": 2, "daily_risk_budget": 12_000.0,
                                 "per_trade_max_risk": 3_000.0})
    paper_account_db.insert_position(book, _position())
    paper_account_db.insert_position(book, _position(signal_id="s2"))

    assert compute._driver_open_capacity_reason(_signal(), 1) == compute.REJECT_MAX_CONCURRENT


def test_max_concurrent_allows_while_there_is_room(book, monkeypatch):
    """Non-vacuity: the gate must not simply refuse everything."""
    monkeypatch.setattr(compute._driver_limits, "risk",
                        lambda: {"max_concurrent": 2, "daily_risk_budget": 12_000.0,
                                 "per_trade_max_risk": 3_000.0})
    paper_account_db.insert_position(book, _position())

    assert compute._driver_open_capacity_reason(_signal(), 1) is None


def test_the_risk_budget_counts_risk_ALREADY_deployed(book, monkeypatch):
    """The budget's whole point. One open position holding $900 of max loss plus
    an incoming $200 breaches a $1,000 budget — and before the fix the deployed
    $900 was invisible, so the incoming trade was measured against an empty
    book."""
    monkeypatch.setattr(compute._driver_limits, "risk",
                        lambda: {"max_concurrent": 10, "daily_risk_budget": 1_000.0,
                                 "per_trade_max_risk": 3_000.0})
    paper_account_db.insert_position(book, _position(max_loss_total=900.0))

    # width 2.0 at a 1.50 credit -> $50 of max loss a contract, x4 = $200.
    reason = compute._driver_open_capacity_reason(_signal(), 4)

    assert reason == compute.REJECT_RISK_BUDGET


def test_an_empty_book_is_still_allowed_to_open(book, monkeypatch):
    monkeypatch.setattr(compute._driver_limits, "risk",
                        lambda: {"max_concurrent": 10, "daily_risk_budget": 1_000.0,
                                 "per_trade_max_risk": 3_000.0})

    assert compute._driver_open_capacity_reason(_signal(), 1) is None


# --- the class of bug, not just this instance -------------------------------

def test_every_lazily_imported_engine_attribute_compute_uses_really_exists():
    """The guard that matters. ``compute.py`` imports ``paper_account_db``,
    ``paper_engine`` and ``signal_db`` LAZILY inside ~40 functions, so a
    misspelled attribute is not an ImportError at startup — it is an
    ``AttributeError`` inside whichever broad ``except`` happens to wrap that
    call, i.e. a silent degrade on one code path. Pyright's scope is deliberately
    narrow and does not cover this file.

    This walks the source for ``<module>.<attr>`` on those three names and checks
    each against the real module, so the next typo fails here instead of costing
    a month of an inert risk gate.
    """
    import ast
    import pathlib

    import paper_engine
    import signal_db

    modules = {"paper_account_db": paper_account_db, "paper_engine": paper_engine,
               "signal_db": signal_db}
    src = (pathlib.Path(compute.__file__)).read_text(encoding="utf-8")
    missing = []
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in modules
                and not hasattr(modules[node.value.id], node.attr)):
            missing.append(f"compute.py:{node.lineno} {node.value.id}.{node.attr}")
    assert not missing, (
        "these attributes do not exist on the module compute.py calls them on, "
        "and a lazy import means you find out at runtime inside an except:\n  "
        + "\n  ".join(missing))
