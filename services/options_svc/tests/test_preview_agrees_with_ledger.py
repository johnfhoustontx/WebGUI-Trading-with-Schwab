"""The Paper dialog's preview and the Ledger decide the same way over the SAME book.

The preview (``webgui/pages/options/book_fit``) reads the published
``cache:options:ledger_caps`` view and the candidate's
``ledger_risk_per_contract`` stamp; the Ledger (``compute.create_paper_trade``)
reads its own trades.db and books the trade ``paper_trader`` builds. Both go
through ``shared.book_caps``, but through different plumbing - the view's
publish, the stamp, the sector table versus ``shared.sectors.group_key`` - and
this test is the one place the two ends meet. A green the service would refuse,
or a refusal of something the service would open, fails here.

Each book is seeded ONCE into a template trades.db through the real
``compute.create_paper_trade``, and its view published once with the REAL
``handlers.refresh_ledger_caps`` and read back off the fake bus. Every case then
runs the Ledger against a FRESH copy of that template, so the service's own
opens never accumulate between comparisons. (Seeding and publishing once per
book rather than per case is for speed only: a fake bus's first connection
does a DNS lookup that costs over a second on Windows.)
"""
import itertools
import pathlib
import sqlite3

import pytest

from services.options_svc import compute, handlers
from shared.bus import Bus
from shared.bus.client import reset_fake_bus

_WEBGUI = pathlib.Path(__file__).resolve().parents[3] / "webgui"


@pytest.fixture
def book_fit(monkeypatch):
    monkeypatch.syspath_prepend(str(_WEBGUI))
    from pages.options import book_fit as mod
    return mod


def _pcs(symbol, expiration, credit, width):
    return {"symbol": symbol, "type": "PCS", "trade_type": "SWING",
            "expiration": expiration, "dte": 30, "short_strike": 100.0,
            "long_strike": 100.0 - width, "width": width, "credit": credit,
            "max_loss": round(width - credit, 2)}


def _r190(sym, exp):
    return _pcs(sym, exp, 0.60, 2.5)       # $190 a contract


def _r400(sym, exp):
    return _pcs(sym, exp, 1.00, 5.0)       # $400 a contract


def _r700(sym, exp):
    return _pcs(sym, exp, 0.50, 7.5)       # $700 a contract


E1, E2 = "2026-10-17", "2026-10-24"

# Each book is a list of (signal, qty) opened through the real Ledger path.
BOOKS = {
    "empty": [],
    # IT: 4 positions / $1,180; E1: 4 positions; ORCL: 2 / $380.
    "mixed": [(_r190("ORCL", E1), 1), (_r190("ORCL", E1), 1),
              (_r400("MSFT", E1), 1), (_r400("NVDA", E2), 1),
              (_r190("XOM", E1), 1)],
    # ORCL holds 3 positions (symbol count full); E1 holds 5 (expiry full).
    "counts_full": [(_r190("ORCL", E1), 1), (_r190("ORCL", E1), 1),
                    (_r190("ORCL", E2), 1), (_r190("XOM", E1), 1),
                    (_r190("JPM", E1), 1), (_r190("KO", E1), 1)],
    # IT holds 5 positions (sector count full) across two expiries.
    "sector_full": [(_r190("ORCL", E2), 1), (_r190("MSFT", E1), 1),
                    (_r190("NVDA", E2), 1), (_r190("AMD", E1), 1),
                    (_r190("AVGO", E2), 1)],
    # IT holds $1,400 in only 2 positions (sector RISK nearly full, count not).
    "sector_risk": [(_r700("MSFT", E2), 1), (_r700("NVDA", E2), 1)],
    # $4,900 of open risk against a $5,000 deployment ceiling.
    "deployed": [(_r700("XOM", E1), 1), (_r700("MSFT", E1), 1),
                 (_r700("JPM", E1), 1), (_r700("KO", E2), 1),
                 (_r700("UNH", E2), 1), (_r700("AAPL", E2), 1),
                 (_r700("CVX", E2), 1)],
}

CANDIDATES = {
    "orcl_190": _r190("ORCL", E1),
    "orcl_400": _r400("ORCL", E1),
    "pcs_900": _pcs("MSFT", E2, 1.00, 10.0),
    "unmapped_190": _r190("ZZZQ", E1),
    "xom_190": _r190("XOM", E2),
}

QTYS = (1, 2, 3, 4, 5)

CASES = list(itertools.product(BOOKS, CANDIDATES, QTYS))


def _point_ledger_at(mp, db_path):
    import trade_tracker_client
    import trades_db

    mp.setattr(trades_db, "DEFAULT_DB_PATH", db_path)
    mp.setattr(trades_db, "_initialised", set())
    mp.setattr(trade_tracker_client, "track", lambda t: True)
    mp.setattr(trade_tracker_client, "untrack", lambda tid: True)


def _published_view():
    reset_fake_bus()
    bus = Bus(fake=True)
    handlers.refresh_ledger_caps(bus)
    env = bus.cache_get(handlers.CACHE_LEDGER_CAPS)
    assert env is not None, "refresh_ledger_caps published nothing"
    return env.payload


@pytest.fixture(scope="module")
def templates(tmp_path_factory):
    """``{book name: (template db path, published view)}``."""
    root = tmp_path_factory.mktemp("ledger_books")
    out = {}
    for name, trades in BOOKS.items():
        db_path = root / f"{name}.db"
        with pytest.MonkeyPatch.context() as mp:
            _point_ledger_at(mp, db_path)
            for sig, qty in trades:
                opened = compute.create_paper_trade(dict(sig), qty)
                assert opened["status"] == "opened", (name, sig["symbol"], opened)
            view = _published_view()
            assert len(view["open"]) == len(trades), (name, view["open"])
        out[name] = (db_path, view)
    reset_fake_bus()
    return out


def _fresh_copy(template, dest):
    src, dst = sqlite3.connect(template), sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def _ledger_outcome(templates, tmp_path, monkeypatch, book_name, cand_name, qty):
    db_path = tmp_path / f"{book_name}-{cand_name}-{qty}.db"
    _fresh_copy(templates[book_name][0], db_path)
    _point_ledger_at(monkeypatch, db_path)
    return compute.create_paper_trade(dict(CANDIDATES[cand_name]), qty)


@pytest.mark.parametrize("book_name,cand_name,qty", CASES)
def test_the_preview_and_the_ledger_agree(book_fit, templates, tmp_path, monkeypatch,
                                          book_name, cand_name, qty):
    view = templates[book_name][1]
    stamped = compute.stamp_candidate(dict(CANDIDATES[cand_name]), trade_type="SWING")
    p = book_fit.preview(stamped, view, qty)
    assert p["available"], (book_name, cand_name, qty)

    outcome = _ledger_outcome(templates, tmp_path, monkeypatch, book_name,
                              cand_name, qty)
    assert outcome["status"] in ("opened", "refused"), outcome

    assert (p["breach"] is None) == (outcome["status"] == "opened"), (
        book_name, cand_name, qty, p["breach"], outcome.get("code"))
    if outcome["status"] == "refused":
        assert p["breach"]["code"] == outcome["code"]
        assert p["max_quantity"] == outcome["max_quantity"]
        assert p["block_text"] == outcome["message"]


def test_the_grid_exercises_every_rung_the_ledger_enforces(templates, tmp_path,
                                                          monkeypatch):
    """Guard against a vacuous grid: across the cases, both an open and a refusal
    on each of the seven rungs must occur, or the agreement proves less than it
    reads."""
    from shared import book_caps

    seen, opened = set(), 0
    for book_name, cand_name, qty in CASES:
        out = _ledger_outcome(templates, tmp_path, monkeypatch, book_name,
                              cand_name, qty)
        if out["status"] == "refused":
            seen.add(out["code"])
        elif out["status"] == "opened":
            opened += 1
    assert opened > 0
    assert seen == set(book_caps.DISPLAY_ORDER), sorted(
        set(book_caps.DISPLAY_ORDER) - seen)
