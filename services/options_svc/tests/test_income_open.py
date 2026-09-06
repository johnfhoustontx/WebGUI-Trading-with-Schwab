"""``income_open`` — the Income board's route into the paper ACCOUNT.

Nothing opened a cash-secured put into the account before this: ``run_entry_cycle``
sizes every candidate off ``sig["width"]``, which a single-leg short has none of,
so it died in that function's broad ``except``. Everything downstream of a lot —
assignment, the share inventory, the covered-call half of this board, the
called-away disposal — was reachable only from a hand-built test fixture.

The paper store is redirected to ``tmp_path`` in every test here. ``paper_account_db``
resolves ``db_path=None`` at CALL time inside ``connect``, so patching the module
attribute redirects the code under test — the shape ``signal_db`` gets wrong, and
the reason the repo-root conftest also refuses a ``sqlite3.connect`` into a live
data directory.
"""
import pytest

from services.options_svc import compute, handlers
from shared.bus import Bus
from shared.contracts.envelope import Command

START_CASH = 25_000.0
_EXPIRY = "2026-10-16"


@pytest.fixture
def paper_db(tmp_path, monkeypatch):
    """A tmp manual paper account, seeded with cash. Yields its path."""
    import paper_account_db

    db = tmp_path / "paper_account_manual.db"
    monkeypatch.setattr(paper_account_db, "DEFAULT_DB_PATH", db)
    paper_account_db.ensure_account(None, START_CASH, "2026-09-05")
    return db


def _price(mark):
    """Patch the live leg pricer to a fixed per-share mark (None = unquoted)."""
    return lambda symbol: (lambda *a, **kw: mark)


@pytest.fixture(autouse=True)
def _live_mark(monkeypatch):
    """Every test gets a live quote unless it overrides this.

    The real pricer fetches a chain through the proxy; here it is the seam that
    keeps the open path off the network AND lets the stale-price and no-quote
    branches be driven deliberately.
    """
    monkeypatch.setattr(compute, "_make_leg_pricer", _price(2.00))


def _csp_row(**kw):
    """A cash-secured put row as ``income_scan`` emits one.

    ``net_credit`` is PER CONTRACT (200.00 for a $2.00 mark) — the units trap the
    whole income window was rebuilt around, and the one the drift guard divides
    back out.
    """
    row = {"id": "AAPL_SHORT_PUT_1", "type": "SHORT_PUT", "symbol": "AAPL",
           "short_strike": 100.0, "expiration": _EXPIRY, "dte": 41,
           "net_credit": 200.0, "capital": 10_000.0}
    row.update(kw)
    return row


def _cc_row(**kw):
    """A covered-call row, carrying the ``lot_id`` ``_covered_row`` stamps."""
    row = {"id": "AAPL_COVERED_CALL_1", "type": "COVERED_CALL", "symbol": "AAPL",
           "short_strike": 110.0, "expiration": _EXPIRY, "dte": 41,
           "net_credit": 200.0, "quantity": 1, "lot_id": 1,
           "shares": 100, "cost_basis": 100.0}
    row.update(kw)
    return row


def _lot(db, *, shares=100, basis=100.0, symbol="AAPL"):
    import paper_account_db
    return paper_account_db.insert_equity_lot(db, {
        "symbol": symbol, "shares": shares, "cost_basis": basis,
        "source": "assignment"})


def _account(db):
    import paper_account_db
    return paper_account_db.get_account(db)


def _open_positions(db):
    import paper_account_db
    return paper_account_db.fetch_open_positions(db)


# ── the cash-secured put ─────────────────────────────────────────────────────

def test_a_cash_secured_put_reserves_the_full_strike_notional(paper_db):
    """The collateral is the FULL notional — that is what makes it cash-SECURED,
    and it is the reservation ``paper_engine._assign_shares`` relies on already
    being back in cash when the put is assigned."""
    result = compute.open_income_position(_csp_row(), qty=1)

    assert result["status"] == "opened", result
    acct = _account(paper_db)
    assert acct["buying_power_reserved"] == 10_000.0
    assert acct["cash"] == 15_000.0

    pos = _open_positions(paper_db)
    assert len(pos) == 1
    assert pos[0]["strategy"] == "SHORT_PUT"
    assert pos[0]["short_strike"] == 100.0
    assert pos[0]["expiration"] == _EXPIRY
    assert pos[0]["quantity"] == 1
    assert pos[0]["max_loss_total"] == 10_000.0
    # The LIVE mark, not the board's — the board was scanned this morning.
    assert pos[0]["entry_credit"] == 2.00


def test_the_reservation_scales_with_quantity(paper_db):
    """Two contracts is two lots' worth of collateral. A per-contract-only
    reservation would let the book open more risk than it has cash for."""
    assert compute.open_income_position(_csp_row(), qty=2)["status"] == "opened"

    acct = _account(paper_db)
    assert acct["buying_power_reserved"] == 20_000.0
    assert acct["cash"] == 5_000.0
    assert _open_positions(paper_db)[0]["quantity"] == 2


def test_a_short_put_the_account_cannot_secure_is_refused(paper_db):
    """$25k cannot secure three $100 puts. The refusal names both numbers, so a
    reader can tell "the account is short" from "the service is down"."""
    result = compute.open_income_position(_csp_row(), qty=3)

    assert result["status"] == "rejected"
    assert result["reason"] == "insufficient_cash"
    assert "30,000.00" in result["message"] and "25,000.00" in result["message"]
    # The positive twin: nothing was reserved and no position exists, which is
    # trivially true in a world where the function never ran at all.
    assert _account(paper_db)["buying_power_reserved"] == 0.0
    assert _open_positions(paper_db) == []
    assert compute.open_income_position(_csp_row(), qty=2)["status"] == "opened"


def test_an_opened_put_can_actually_be_assigned(paper_db, monkeypatch):
    """THE point of this function. A position it writes must be one
    ``paper_engine`` recognises as a cash-secured put — otherwise the whole
    downstream chain (assignment, the lot, the covered call) stays unreachable,
    which is the state this branch was in.
    """
    import paper_engine

    assert compute.open_income_position(_csp_row(), qty=1)["status"] == "opened"
    pos = _open_positions(paper_db)[0]

    assert paper_engine.is_cash_secured_put(pos) is True
    assert paper_engine.is_assignment(pos, 92.0) is True
    assert paper_engine.is_assignment(pos, 108.0) is False
    # And it is NOT mistaken for the other single-leg structure.
    assert paper_engine.is_covered_call(pos) is False


# ── the covered call ─────────────────────────────────────────────────────────

def test_a_covered_call_reserves_nothing(paper_db):
    """The shares are the collateral and they are already counted in
    ``equity_at_cost``. Reserving against them would count the same capital
    twice — and ``reconcile_buying_power`` would hand it straight back at the
    next service start, so the double-count would also be unstable."""
    import paper_account_db

    _lot(paper_db)
    paper_account_db.debit_cash(paper_db, 10_000.0)      # the lot was paid for
    before = _account(paper_db)["cash"]

    result = compute.open_income_position(_cc_row(), qty=1)

    assert result["status"] == "opened", result
    acct = _account(paper_db)
    assert acct["buying_power_reserved"] == 0.0
    assert acct["cash"] == before, "a covered call must not move cash at open"
    pos = _open_positions(paper_db)[0]
    assert pos["strategy"] == "COVERED_CALL"
    assert pos["max_loss_total"] == 0.0
    # Both strike fields: ``call_short`` is what keeps ``is_cash_secured_put``
    # from ever reading this as an assignable put, and what the Shares page
    # reads first.
    assert pos["call_short"] == 110.0 and pos["short_strike"] == 110.0
    assert paper_account_db.reconcile_buying_power(paper_db) == 0.0


def test_an_opened_covered_call_can_actually_be_called_away(paper_db):
    """The disposal-side twin of the assignment check above."""
    import paper_engine

    _lot(paper_db)
    assert compute.open_income_position(_cc_row(), qty=1)["status"] == "opened"
    pos = _open_positions(paper_db)[0]

    assert paper_engine.is_covered_call(pos) is True
    assert paper_engine.is_called_away(pos, 115.0) is True
    assert paper_engine.is_called_away(pos, 110.0) is False    # AT the strike
    assert paper_engine.is_cash_secured_put(pos) is False


def test_a_covered_call_with_no_lot_is_refused(paper_db):
    """No shares, no covered call. Reserving nothing against nothing would be an
    undefined-risk naked short call in the book, which is the one structure
    ``driver_policy``'s allowlist refuses on principle."""
    result = compute.open_income_position(_cc_row(), qty=1)

    assert result["status"] == "rejected"
    assert result["reason"] == "no_lot"
    assert "AAPL" in result["message"]
    assert _open_positions(paper_db) == []
    # The positive twin: with the lot present the SAME call opens.
    _lot(paper_db)
    assert compute.open_income_position(_cc_row(), qty=1)["status"] == "opened"


def test_a_covered_call_on_a_lot_that_no_longer_exists_is_refused(paper_db):
    """The board is scanned each morning; a lot can be called away in between.
    Matching on ``lot_id`` means a stale row refuses rather than silently
    writing a call against some OTHER lot of the same symbol."""
    _lot(paper_db)                                    # lot_id 1
    result = compute.open_income_position(_cc_row(lot_id=99), qty=1)

    assert result["status"] == "rejected"
    assert result["reason"] == "no_lot"
    assert _open_positions(paper_db) == []


def test_a_covered_call_bigger_than_its_lot_is_refused(paper_db):
    """100 shares support ONE contract. Two would be one covered call and one
    naked one, and the book cannot tell them apart afterwards."""
    _lot(paper_db, shares=100)
    result = compute.open_income_position(_cc_row(), qty=2)

    assert result["status"] == "rejected"
    assert result["reason"] == "partial_lot"
    assert "1 contract" in result["message"]
    assert _open_positions(paper_db) == []
    assert compute.open_income_position(_cc_row(), qty=1)["status"] == "opened"


def test_a_covered_call_smaller_than_its_lot_is_refused_and_named(paper_db):
    """``close_equity_lot`` disposes of a lot WHOLE, so a call covering PART of
    one could never be delivered against it — the shares would be gone from the
    book with no contract to explain where. The refusal names the number that
    would work rather than only saying no."""
    _lot(paper_db, shares=300)
    result = compute.open_income_position(_cc_row(quantity=3), qty=1)

    assert result["status"] == "rejected"
    assert result["reason"] == "partial_lot"
    assert "300" in result["message"] and "3 contracts" in result["message"]
    assert _open_positions(paper_db) == []
    # The positive twin: the WHOLE lot opens fine.
    assert compute.open_income_position(_cc_row(quantity=3), qty=3)["status"] == "opened"


def test_a_lot_already_covered_refuses_a_second_call(paper_db):
    """Coverage is recorded per SYMBOL — ``paper_positions`` holds no link back
    to the shares a call was written against, which is why the Shares page shows
    one call against every lot of a name. A second open call would make that
    display report shares as covered once when they are written twice over."""
    _lot(paper_db)
    assert compute.open_income_position(_cc_row(), qty=1)["status"] == "opened"

    result = compute.open_income_position(
        _cc_row(id="AAPL_COVERED_CALL_2", short_strike=115.0), qty=1)

    assert result["status"] == "rejected"
    assert result["reason"] == "already_covered"
    # Exactly ONE call, not two — the negative assertion's positive twin.
    assert len(_open_positions(paper_db)) == 1


def test_a_call_on_a_DIFFERENT_symbol_is_not_blocked(paper_db):
    """Non-vacuity for the rule above: the conflict is per symbol, not global.
    A version that refused every second covered call would pass that test."""
    _lot(paper_db, symbol="AAPL")
    _lot(paper_db, symbol="MSFT")
    assert compute.open_income_position(_cc_row(lot_id=1), qty=1)["status"] == "opened"

    result = compute.open_income_position(
        _cc_row(id="MSFT_CC", symbol="MSFT", lot_id=2), qty=1)

    assert result["status"] == "opened", result
    assert len(_open_positions(paper_db)) == 2


# ── the price guards ─────────────────────────────────────────────────────────

def test_an_unquoted_contract_is_refused(paper_db, monkeypatch):
    """No live mark, no price to open at. The board is hours old, so filling at
    ITS number would book a fiction."""
    monkeypatch.setattr(compute, "_make_leg_pricer", _price(None))

    result = compute.open_income_position(_csp_row(), qty=1)

    assert result["status"] == "rejected"
    assert result["reason"] == "no_quote"
    assert _open_positions(paper_db) == []
    assert _account(paper_db)["buying_power_reserved"] == 0.0


@pytest.mark.parametrize("mark, expected", [
    (2.00, "opened"),      # exactly the board price
    (2.30, "opened"),      # +15% — AT the tolerance, not past it
    (1.70, "opened"),      # -15% — SYMMETRICALLY at it; see the note below
    (2.31, "rejected"),    # a cent past it
    (1.69, "rejected"),
])
def test_a_price_that_has_moved_too_far_is_refused(paper_db, monkeypatch,
                                                   mark, expected):
    """The board shows $2.00. We open at the LIVE mark, so drift is not
    dangerous in itself — but a fill materially different from the row the
    reader picked is not the trade they chose, so it is refused and both
    numbers are shown. The tolerance is ``paper_adjust``'s, deliberately: the
    Rescue board's Execute already refuses on exactly this rule.

    ⚠ Both ±15% cases are here on purpose. Unrounded, ``abs(1.70-2.00)/2.00``
    is 0.15000000000000002 and ``abs(2.30-2.00)/2.00`` is 0.1499999999999999,
    so the boundary lands on opposite sides for the same drift depending only
    on the sign — a real asymmetry that shipped until this test caught it, and
    the reason ``income_price_drift`` rounds.
    """
    monkeypatch.setattr(compute, "_make_leg_pricer", _price(mark))

    result = compute.open_income_position(_csp_row(), qty=1)

    assert result["status"] == expected, result
    if expected == "rejected":
        assert result["reason"] == "stale_price"
        assert f"${mark:.2f}" in result["message"]
        assert "$2.00" in result["message"]
        assert _open_positions(paper_db) == []
    else:
        assert _open_positions(paper_db)[0]["entry_credit"] == round(mark, 2)


def test_a_row_with_no_board_price_still_opens_at_the_live_mark(paper_db):
    """``income_price_drift`` returns None when the board price is unreadable,
    and a missing comparison must not become a refusal: the live mark is the
    price we actually open at, and it is present."""
    assert compute.income_price_drift(None, 2.0) is None
    assert compute.income_price_drift(0.0, 2.0) is None
    assert compute.income_price_drift(float("nan"), 2.0) is None

    result = compute.open_income_position(_csp_row(net_credit=None), qty=1)

    assert result["status"] == "opened", result
    assert _open_positions(paper_db)[0]["entry_credit"] == 2.00


# ── the other refusals ───────────────────────────────────────────────────────

def test_a_credit_spread_is_refused_from_this_route(paper_db):
    """A spread's route is ``paper_create``, which writes the LEDGER. The two are
    different books and this one holds share lots."""
    result = compute.open_income_position(_csp_row(type="PCS"), qty=1)

    assert result["status"] == "rejected"
    assert result["reason"] == "unsupported_structure"
    assert _open_positions(paper_db) == []


def test_a_halted_account_opens_nothing(paper_db):
    import paper_account_db

    paper_account_db.set_halted(paper_db, True)

    result = compute.open_income_position(_csp_row(), qty=1)

    assert result["status"] == "rejected"
    assert result["reason"] == "halted"
    assert _open_positions(paper_db) == []
    # The positive twin — un-halted, the same row opens.
    paper_account_db.set_halted(paper_db, False)
    assert compute.open_income_position(_csp_row(), qty=1)["status"] == "opened"


@pytest.mark.parametrize("row, reason", [
    ({"type": "SHORT_PUT", "symbol": "", "short_strike": 100.0,
      "expiration": _EXPIRY}, "bad_row"),
    ({"type": "SHORT_PUT", "symbol": "AAPL", "expiration": _EXPIRY}, "bad_row"),
    ({"type": "SHORT_PUT", "symbol": "AAPL", "short_strike": 100.0}, "bad_row"),
    ({"type": "SHORT_PUT", "symbol": "AAPL", "short_strike": 0.0,
      "expiration": _EXPIRY}, "bad_row"),
])
def test_an_unusable_row_is_refused_before_anything_is_reserved(paper_db, row,
                                                                reason):
    result = compute.open_income_position(row, qty=1)
    assert result["status"] == "rejected" and result["reason"] == reason
    assert _account(paper_db)["buying_power_reserved"] == 0.0


def test_a_quantity_below_one_is_refused(paper_db):
    for bad in (0, -1, None, "x"):
        result = compute.open_income_position(_csp_row(), qty=bad)
        assert result["status"] == "rejected", bad
        assert result["reason"] == "bad_quantity"
    assert _open_positions(paper_db) == []


def test_the_open_never_raises(paper_db, monkeypatch):
    """The command consumer must survive a malformed row. A raise here would kill
    the stream consumer for every later command, not just this one."""
    def _boom(symbol):
        raise RuntimeError("chain exploded")

    monkeypatch.setattr(compute, "_make_leg_pricer", _boom)

    result = compute.open_income_position(_csp_row(), qty=1)

    assert result["status"] == "error"
    assert "chain exploded" in result["message"]
    assert _open_positions(paper_db) == []
    assert _account(paper_db)["buying_power_reserved"] == 0.0


def test_every_refusal_carries_a_sentence_for_the_reader(paper_db):
    """A code alone reaches the user as ``insufficient_cash``; a sentence alone
    cannot be asserted on without matching prose. Every refusal carries both."""
    refusals = [
        compute.open_income_position(_csp_row(type="IC"), qty=1),
        compute.open_income_position(_csp_row(), qty=99),
        compute.open_income_position(_cc_row(), qty=1),
        compute.open_income_position({}, qty=1),
    ]
    assert all(r["status"] == "rejected" for r in refusals), refusals
    for r in refusals:
        assert r["reason"] and isinstance(r["reason"], str)
        assert len(r["message"]) > 20 and r["message"].endswith((".", "?"))


# ── the pure helpers ─────────────────────────────────────────────────────────

def test_income_open_strike_reads_the_flat_field_then_the_legs():
    assert compute.income_open_strike({"short_strike": 105.0}) == 105.0
    assert compute.income_open_strike(
        {"legs": [{"side": "short", "strike": 110.0},
                  {"side": "long", "strike": 115.0}]}) == 110.0
    assert compute.income_open_strike({"legs": [{"side": "long",
                                                 "strike": 115.0}]}) is None
    assert compute.income_open_strike({"short_strike": float("nan")}) is None
    assert compute.income_open_strike(None) is None


def test_open_covered_call_conflict_matches_the_structure_not_the_symbol():
    """⚠ An open call CREDIT SPREAD on the same symbol is not a covered call.
    Matching on symbol alone would refuse a legitimate covered call because an
    unrelated spread happens to be open."""
    positions = [
        {"symbol": "AAPL", "strategy": "CCS"},
        {"symbol": "MSFT", "strategy": "COVERED_CALL"},
    ]
    assert compute.open_covered_call_conflict(positions, "AAPL") is None
    assert compute.open_covered_call_conflict(positions, "MSFT") is not None
    assert compute.open_covered_call_conflict(positions, "aapl") is None
    assert compute.open_covered_call_conflict([], "AAPL") is None


# ── the command path ─────────────────────────────────────────────────────────

def test_the_command_opens_and_publishes_the_outcome(paper_db):
    bus = Bus(fake=True)

    handlers.handle_command(bus, Command(
        type="income_open", args={"row": _csp_row(), "qty": 1}))

    assert len(_open_positions(paper_db)) == 1
    env = bus.cache_get(handlers.CACHE_INCOME_OPEN)
    assert env.payload["status"] == "opened"
    assert env.payload["symbol"] == "AAPL"
    assert env.payload["message"]
    # The book changed, so the account view the Paper Account page, the Shares
    # page and the nav badge all read must have been republished.
    assert bus.cache_get(handlers.CACHE_PAPER) is not None


def test_a_refusal_is_published_too(paper_db):
    """A refusal that stayed in the log is a button that does nothing — the user
    cannot tell a rule they broke from a service that is down."""
    bus = Bus(fake=True)

    handlers.handle_command(bus, Command(
        type="income_open", args={"row": _csp_row(), "qty": 99}))

    env = bus.cache_get(handlers.CACHE_INCOME_OPEN)
    assert env.payload["status"] == "rejected"
    assert env.payload["reason"] == "insufficient_cash"
    assert _open_positions(paper_db) == []


def test_two_identical_refusals_are_two_distinguishable_publishes(paper_db):
    """Click Open twice on the same under-funded row. The payload is otherwise
    byte-identical, so without the sequence counter the reader would see one
    toast for two clicks and conclude the second did nothing."""
    bus = Bus(fake=True)
    cmd = Command(type="income_open", args={"row": _csp_row(), "qty": 99})

    handlers.handle_command(bus, cmd)
    first = bus.cache_get(handlers.CACHE_INCOME_OPEN).payload
    v1 = bus.cache_version(handlers.CACHE_INCOME_OPEN)
    handlers.handle_command(bus, cmd)
    second = bus.cache_get(handlers.CACHE_INCOME_OPEN).payload

    assert second["seq"] > first["seq"]
    assert bus.cache_version(handlers.CACHE_INCOME_OPEN) != v1


def test_the_event_the_page_subscribes_to_is_published(paper_db):
    bus = Bus(fake=True)
    sub = bus.subscribe(handlers.EVENT_INCOME_OPEN)

    handlers.handle_command(bus, Command(
        type="income_open", args={"row": _csp_row(), "qty": 1}))

    assert sub.get_message(timeout=1.0) is not None


def test_a_replayed_open_is_refused_and_says_so(paper_db):
    """``income_open`` MUTATES the book — it reserves collateral and writes a
    position. Consumer groups are created at id 0, so a fresh group re-delivers
    the whole backlog; the same age gate ``paper_create`` takes applies here for
    the same reason.
    """
    import datetime as dt

    bus = Bus(fake=True)
    stale = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(seconds=handlers.STALE_OPEN_MAX_AGE_SEC + 60))

    handlers.handle_command(bus, Command(
        type="income_open", args={"row": _csp_row(), "qty": 1},
        ts=stale.isoformat()))

    assert _open_positions(paper_db) == [], "a replayed command opened a position"
    assert _account(paper_db)["buying_power_reserved"] == 0.0
    payload = bus.cache_get(handlers.CACHE_INCOME_OPEN).payload
    assert payload["status"] == "rejected"
    assert payload["reason"] == "stale_command"


def test_a_FRESH_open_is_not_caught_by_the_replay_gate(paper_db):
    """Non-vacuity for the gate above: a version that refused everything would
    pass that test."""
    import datetime as dt

    bus = Bus(fake=True)
    fresh = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)

    handlers.handle_command(bus, Command(
        type="income_open", args={"row": _csp_row(), "qty": 1},
        ts=fresh.isoformat()))

    assert len(_open_positions(paper_db)) == 1
    assert bus.cache_get(handlers.CACHE_INCOME_OPEN).payload["status"] == "opened"


def test_a_refusal_does_not_republish_the_account_view(paper_db):
    """Nothing changed, so repainting three screens to say so would be noise."""
    bus = Bus(fake=True)
    before = bus.cache_version(handlers.CACHE_PAPER)

    handlers.handle_command(bus, Command(
        type="income_open", args={"row": _csp_row(), "qty": 99}))

    assert bus.cache_version(handlers.CACHE_PAPER) == before
