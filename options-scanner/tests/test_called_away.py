"""Called away: an in-the-money covered call delivers its shares at the strike.

The disposal half of the wheel, and the exact mirror of ``test_assignment.py``.
Until this path existed a lot could be created and never leave: cash permanently
debited, ``equity_at_cost`` permanently inflating ``session_start_equity``, and
three documents promising a disposal the code did not have.

⚠ The invariant that matters here is the CASH one, driven end-to-end from
``run_manage_cycle``: ``cash + buying_power_reserved + equity_at_cost`` must
equal the starting balance plus lifetime ``realized_pnl``. ``_call_away_shares``
moves cash in two pieces — ``credit_cash(basis x shares)`` for the conversion and
``realize_pnl(lot pnl)`` for the gain — because ``realize_pnl`` moves cash too.
Crediting the full ``strike x shares`` AND booking the P&L would credit the gain
twice, and nothing else in this file would look any different: the lot would
still close, at the right price, for the right share count. That double credit is
the disposal-side mirror of the double release the assignment path is guarded
against.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import paper_account_db as pdb
import paper_engine as pe

_CT = ZoneInfo("America/Chicago")

_EXPIRY = "2026-06-03"
START_CASH = 25_000.0


def _expiry_close_ct():
    """15:05 CT on the expiry day — past ``should_settle``'s 15:00 gate."""
    return datetime(2026, 6, 3, 15, 5, tzinfo=_CT)


class _QuoteClient:
    """Minimal client whose get_quotes([sym]) yields a settlement lastPrice.

    The same stub shape ``test_assignment`` uses, rather than a second one.
    """

    def __init__(self, price):
        self._price = price

    def get_quotes(self, syms):
        sym, price = syms[0], self._price

        class _R:
            status_code = 200

            def json(self):
                return {sym: {"quote": {"lastPrice": price}}}

        return _R()


def _no_repricer(monkeypatch):
    """An expired chain prices nothing — settlement falls back to a direct quote."""
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing",
                        lambda trade, client: {
                            "current_value": None, "unrealized_pnl": None,
                            "pnl_pct_of_credit": None, "current_underlying": None,
                            "current_short_delta": None, "error": "expired"})


def _account(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, START_CASH, _EXPIRY)
    return db


def _open_short_put(db, *, strike, credit, qty, symbol="AAPL"):
    """A cash-secured put, opened the way ``compute.open_income_position`` does:
    reserve the FULL strike notional, then insert the row."""
    notional = round(strike * 100 * qty, 2)
    pdb.reserve_buying_power(db, notional)
    return pdb.insert_position(db, {
        "signal_id": f"csp-{strike}", "symbol": symbol, "strategy": "SHORT_PUT",
        "short_strike": strike, "long_strike": None,
        "call_short": None, "call_long": None, "width": None,
        "expiration": _EXPIRY, "dte_at_entry": 35, "quantity": qty,
        "entry_credit": credit, "entry_order_id": None,
        "max_loss_per": strike, "max_loss_total": notional,
        "entry_ts": "2026-04-29T09:31:00"})


def _open_covered_call(db, *, strike, credit, qty, symbol="AAPL"):
    """A covered call, opened the way ``compute.open_income_position`` does.

    ⚠ It reserves NOTHING and stores ``max_loss_total = 0.0``: the shares are the
    collateral and they are already counted in ``equity_at_cost``. The strike is
    written to BOTH strike fields, which is what keeps ``is_cash_secured_put``
    (which requires ``call_short is None``) from ever reading it as an assignable
    put.
    """
    return pdb.insert_position(db, {
        "signal_id": f"cc-{strike}", "symbol": symbol, "strategy": "COVERED_CALL",
        "short_strike": strike, "long_strike": None,
        "call_short": strike, "call_long": None, "width": None,
        "expiration": _EXPIRY, "dte_at_entry": 35, "quantity": qty,
        "entry_credit": credit, "entry_order_id": None,
        "max_loss_per": 0.0, "max_loss_total": 0.0,
        "entry_ts": "2026-04-29T09:31:00"})


def _settled(db, position_id):
    """The closed position row — proof the settlement branch actually ran."""
    return next(p for p in pdb.fetch_all_positions(db)
                if p["position_id"] == position_id)


def _closed_lots(db):
    """CLOSED lot rows. Read here rather than through a helper on the store: no
    production reader wants them (both the account view and the Shares page read
    OPEN lots only), and adding a function for a test to call is dead code."""
    conn = pdb.connect(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM equity_lots WHERE status='CLOSED' ORDER BY lot_id")]
    finally:
        conn.close()


def _book(db):
    """``(cash, reserved, equity_at_cost, realized_pnl)`` — the whole account."""
    a = pdb.get_account(db)
    return (a["cash"], a["buying_power_reserved"], pdb.equity_at_cost(db),
            a["realized_pnl"])


def _assert_coherent(db):
    """Every dollar is somewhere: cash + collateral + stock == start + realized.

    THE assertion of this file. A double credit, a missing credit, a release
    that should have been a no-op — all of them land here and nowhere else.
    """
    cash, reserved, equity, realized = _book(db)
    assert round(cash + reserved + equity, 2) == round(START_CASH + realized, 2), (
        f"the book does not add up: cash {cash} + reserved {reserved} + stock "
        f"{equity} != start {START_CASH} + realized {realized}")


# ── the whole loop ───────────────────────────────────────────────────────────

def test_the_full_wheel_turns(tmp_path, monkeypatch):
    """CSP opened -> assigned -> shares held -> call sold -> called away.

    The account must return to a coherent state with no orphan lot and no
    double-counted cash. Each leg is asserted where it happens, so a break says
    WHICH step moved the wrong dollars rather than only that the total is off.
    """
    db = _account(tmp_path)
    _no_repricer(monkeypatch)

    # ── 1. open the cash-secured put: 100 strike, $2.00 credit, 1 contract ──
    put_id = _open_short_put(db, strike=100.0, credit=2.0, qty=1)
    assert _book(db) == (15_000.0, 10_000.0, 0.0, 0.0)
    _assert_coherent(db)

    # ── 2. it settles at 92: assigned, shares bought AT THE STRIKE ──────────
    pe.run_manage_cycle(_QuoteClient(92.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())
    put_row = _settled(db, put_id)
    assert put_row["exit_reason"] == "ASSIGNED"
    put_pnl = put_row["realized_pnl"]
    assert put_pnl == 199.35        # $200 credit less one leg's opening commission
    lots = pdb.fetch_open_lots(db)
    assert len(lots) == 1 and lots[0]["shares"] == 100
    assert lots[0]["cost_basis"] == 100.0
    # The reservation paid for the stock; the premium stayed as realized P&L.
    assert _book(db) == (round(15_000.0 + put_pnl, 2), 0.0, 10_000.0, put_pnl)
    _assert_coherent(db)

    # ── 3. write a covered call against the lot: 105 strike, $1.50 ──────────
    before_call = _book(db)
    call_id = _open_covered_call(db, strike=105.0, credit=1.50, qty=1)
    # Opening it moves NOTHING: no collateral is reserved (the shares are the
    # collateral) and this book realizes a credit at close, never at open.
    assert _book(db) == before_call
    _assert_coherent(db)

    # ── 4. it settles at 110, above the strike: the shares are called away ──
    pe.run_manage_cycle(_QuoteClient(110.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())
    call_row = _settled(db, call_id)
    assert call_row["status"] == "EXPIRED"
    assert call_row["exit_reason"] == "CALLED_AWAY"
    call_pnl = call_row["realized_pnl"]
    assert call_pnl == 149.35       # $150 credit less one leg's opening commission

    # The lot is gone, closed at the STRIKE, and booked its own gain.
    assert pdb.fetch_open_lots(db) == [], "orphan lot: the shares were delivered"
    closed = _closed_lots(db)
    assert len(closed) == 1
    assert closed[0]["exit_price"] == 105.0
    assert closed[0]["exit_reason"] == "called_away"
    assert closed[0]["realized_pnl"] == 500.0      # (105 - 100) x 100 shares

    # ── the arithmetic, spelled out ────────────────────────────────────────
    # cash 15,199.35 (after assignment)
    #      + 149.35   the call's credit, realized at settlement
    #      + 10,000   the lot's COST returned (credit_cash)
    #      + 500      the lot's gain          (realize_pnl)
    #      = 25,848.70
    cash, reserved, equity, realized = _book(db)
    assert cash == 25_848.70
    assert reserved == 0.0
    assert equity == 0.0, "equity_at_cost must not outlive the lot"
    assert realized == round(put_pnl + call_pnl + 500.0, 2) == 848.70
    _assert_coherent(db)
    assert pdb.reconcile_buying_power(db) == 0.0


def test_the_called_away_credit_is_the_strike_not_the_spot(tmp_path, monkeypatch):
    """The shares go at the STRIKE the call promised, however far the stock ran.

    Settling at 130 against a 105 strike must credit exactly the same cash as
    settling at 106 does. Pricing the disposal off the settlement quote would
    hand the account the whole upside it had sold away — which is the one thing
    writing a covered call gives up.
    """
    runs = {}
    for spot in (106.0, 130.0):
        db = _account(tmp_path / f"spot{spot:g}")
        _no_repricer(monkeypatch)
        pdb.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100,
                                   "cost_basis": 100.0, "source": "assignment"})
        pdb.debit_cash(db, 10_000.0)       # the lot was paid for
        call_id = _open_covered_call(db, strike=105.0, credit=1.50, qty=1)

        pe.run_manage_cycle(_QuoteClient(spot), _EXPIRY, db_path=db,
                            now_ct=_expiry_close_ct())

        assert _settled(db, call_id)["exit_reason"] == "CALLED_AWAY"
        assert pdb.fetch_open_lots(db) == []
        runs[spot] = _book(db)

    assert runs[106.0] == runs[130.0], (
        "the disposal was priced off the settlement quote, not the strike — the "
        "account kept upside the call had sold")
    cash, reserved, equity, realized = runs[130.0]
    # 15,000 (start less the lot) + 149.35 credit + 10,000 basis + 500 gain
    assert cash == 25_649.35
    assert (reserved, equity) == (0.0, 0.0)
    assert realized == 649.35


# ── the ITM boundary ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("spot, called", [
    (104.99, False),   # below the strike: worthless, abandoned
    (105.0, False),    # EXACTLY at the strike: worth nothing, abandoned
    (105.01, True),    # a cent above: exercised
])
def test_a_call_is_called_away_only_STRICTLY_above_its_strike(
        tmp_path, monkeypatch, spot, called):
    """``max(spot - strike, 0)`` is zero AT the strike, so the call is abandoned
    there and the shares stay. The mirror of ``is_assignment``'s strict ``<``,
    and wrong in the same expensive way if relaxed: ``>=`` would deliver stock
    to satisfy a contract that expired worthless.

    ``EXPIRED`` is asserted on every branch so the lot check cannot pass in the
    world where settlement never ran at all.
    """
    db = _account(tmp_path)
    _no_repricer(monkeypatch)
    pdb.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100,
                               "cost_basis": 100.0, "source": "assignment"})
    pdb.debit_cash(db, 10_000.0)
    call_id = _open_covered_call(db, strike=105.0, credit=1.50, qty=1)

    pe.run_manage_cycle(_QuoteClient(spot), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())

    row = _settled(db, call_id)
    assert row["status"] == "EXPIRED", "settlement did not run; the rest is vacuous"
    if called:
        assert row["exit_reason"] == "CALLED_AWAY"
        assert pdb.fetch_open_lots(db) == []
        assert pdb.equity_at_cost(db) == 0.0
    else:
        assert row["exit_reason"] == "EXPIRED"
        lots = pdb.fetch_open_lots(db)
        assert len(lots) == 1, "an abandoned call must leave the shares alone"
        assert lots[0]["cost_basis"] == 100.0
        assert pdb.equity_at_cost(db) == 10_000.0
    _assert_coherent(db)
    assert pdb.reconcile_buying_power(db) == 0.0


def test_an_otm_covered_call_keeps_its_credit_and_the_lot(tmp_path, monkeypatch):
    """The income case: the call expires worthless, the premium is kept, and the
    shares are still there to write another one against next month."""
    db = _account(tmp_path)
    _no_repricer(monkeypatch)
    pdb.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100,
                               "cost_basis": 100.0, "source": "assignment"})
    pdb.debit_cash(db, 10_000.0)
    call_id = _open_covered_call(db, strike=105.0, credit=1.50, qty=1)

    pe.run_manage_cycle(_QuoteClient(101.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())

    row = _settled(db, call_id)
    assert row["status"] == "EXPIRED"
    assert row["exit_reason"] == "EXPIRED"
    assert row["realized_pnl"] == 149.35
    assert len(pdb.fetch_open_lots(db)) == 1
    cash, reserved, equity, realized = _book(db)
    assert cash == round(15_000.0 + 149.35, 2)
    assert (reserved, equity, realized) == (0.0, 10_000.0, 149.35)
    _assert_coherent(db)


# ── the release that must stay a no-op ───────────────────────────────────────

def test_settling_a_covered_call_credits_no_collateral_it_never_took(
        tmp_path, monkeypatch):
    """``_close`` unconditionally calls ``release_buying_power(max_loss_total)``.

    A covered call reserves nothing, so it stores ``max_loss_total = 0.0`` and
    that call is a no-op. Storing a notional there instead would credit cash the
    account never took — this pins that the stored value keeps the release inert.

    Driven from a book with a DIFFERENT position holding real collateral, so a
    release of the wrong amount cannot hide behind a zero total.
    """
    db = _account(tmp_path)
    _no_repricer(monkeypatch)
    pdb.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100,
                               "cost_basis": 100.0, "source": "assignment"})
    pdb.debit_cash(db, 10_000.0)
    # A far-dated spread that will NOT settle this cycle, holding $400 reserved.
    pdb.reserve_buying_power(db, 400.0)
    pdb.insert_position(db, {
        "signal_id": "pcs-far", "symbol": "MSFT", "strategy": "PCS",
        "short_strike": 400.0, "long_strike": 395.0, "call_short": None,
        "call_long": None, "width": 5.0, "expiration": "2027-01-15",
        "dte_at_entry": 40, "quantity": 1, "entry_credit": 1.0,
        "entry_order_id": None, "max_loss_per": 4.0, "max_loss_total": 400.0,
        "entry_ts": "2026-04-29T09:31:00"})
    _open_covered_call(db, strike=105.0, credit=1.50, qty=1)

    before_reserved = pdb.get_account(db)["buying_power_reserved"]
    pe.run_manage_cycle(_QuoteClient(110.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())

    assert pdb.get_account(db)["buying_power_reserved"] == before_reserved == 400.0
    assert pdb.reconcile_buying_power(db) == 0.0, (
        "the covered call's settlement moved reserved buying power")
    _assert_coherent(db)


def test_a_covered_call_is_charged_one_leg_of_commission_not_four(
        tmp_path, monkeypatch):
    """``_position_legs``' fallback reads a call-side strike as an iron condor,
    and a covered call stores its strike in exactly that field.

    Without the ``is_covered_call`` branch this position is charged FOUR legs.
    The difference is visible in realized P&L: $150 credit less 0.65 (one leg,
    half a round trip at expiry) = 149.35, against 147.40 for four.
    """
    db = _account(tmp_path)
    _no_repricer(monkeypatch)
    pdb.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100,
                               "cost_basis": 100.0, "source": "assignment"})
    pdb.debit_cash(db, 10_000.0)
    call_id = _open_covered_call(db, strike=105.0, credit=1.50, qty=1)

    pe.run_manage_cycle(_QuoteClient(110.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())

    assert pe._position_legs({"strategy": "COVERED_CALL", "short_strike": 105.0,
                              "call_short": 105.0}) == 1
    assert _settled(db, call_id)["realized_pnl"] == 149.35


# ── the structural gate ──────────────────────────────────────────────────────

def test_a_naked_short_call_is_not_called_away(tmp_path, monkeypatch):
    """THE money path, and the mirror of ``test_a_single_leg_short_call_is_not
    _assigned``.

    A NAKED short call matches ``is_covered_call``'s STRUCTURE test exactly —
    one call strike, no long leg — so the strategy word is the only thing
    standing between it and a disposal. Getting it wrong closes a lot that was
    never pledged to this contract and credits the account for stock it does not
    hold; here the lot belongs to a DIFFERENT trade entirely.
    """
    db = _account(tmp_path)
    _no_repricer(monkeypatch)
    pdb.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100,
                               "cost_basis": 100.0, "source": "assignment"})
    pdb.debit_cash(db, 10_000.0)
    pos_id = pdb.insert_position(db, {
        "signal_id": "naked-call", "symbol": "AAPL", "strategy": "NAKED_CALL",
        "short_strike": 105.0, "long_strike": None, "call_short": 105.0,
        "call_long": None, "width": None, "expiration": _EXPIRY,
        "dte_at_entry": 35, "quantity": 1, "entry_credit": 1.50,
        "entry_order_id": None, "max_loss_per": 0.0, "max_loss_total": 0.0,
        "entry_ts": "2026-04-29T09:31:00"})

    pe.run_manage_cycle(_QuoteClient(110.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())

    row = _settled(db, pos_id)
    assert row["status"] == "EXPIRED", "settlement did not run; the rest is vacuous"
    assert row["exit_reason"] == "EXPIRED"
    assert len(pdb.fetch_open_lots(db)) == 1, (
        "a naked short call delivered shares it never owned")
    assert pdb.equity_at_cost(db) == 10_000.0
    _assert_coherent(db)


def test_a_settling_spread_never_takes_the_called_away_branch(tmp_path, monkeypatch):
    """A CCS finishing above its short strike is not a covered call, however
    deep. Its two legs settle against each other and no stock moves."""
    db = _account(tmp_path)
    _no_repricer(monkeypatch)
    pdb.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100,
                               "cost_basis": 100.0, "source": "assignment"})
    pdb.debit_cash(db, 10_000.0)
    pdb.reserve_buying_power(db, 400.0)
    pos_id = pdb.insert_position(db, {
        "signal_id": "ccs-1", "symbol": "AAPL", "strategy": "CCS",
        "short_strike": 105.0, "long_strike": 110.0, "call_short": None,
        "call_long": None, "width": 5.0, "expiration": _EXPIRY,
        "dte_at_entry": 35, "quantity": 1, "entry_credit": 1.0,
        "entry_order_id": None, "max_loss_per": 4.0, "max_loss_total": 400.0,
        "entry_ts": "2026-04-29T09:31:00"})

    pe.run_manage_cycle(_QuoteClient(112.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())

    row = _settled(db, pos_id)
    assert row["status"] == "EXPIRED"
    assert row["exit_reason"] == "EXPIRED"
    assert len(pdb.fetch_open_lots(db)) == 1
    assert pdb.reconcile_buying_power(db) == 0.0
    _assert_coherent(db)


# ── the lot lookup ───────────────────────────────────────────────────────────

def test_lot_for_call_away_takes_the_exact_share_match_oldest_first():
    """``close_equity_lot`` disposes of a lot WHOLE, so an inexact match is not a
    lot this can close — it returns None rather than guessing."""
    lots = [
        {"lot_id": 1, "symbol": "MSFT", "shares": 100},
        {"lot_id": 2, "symbol": "AAPL", "shares": 200},
        {"lot_id": 3, "symbol": "AAPL", "shares": 100},
        {"lot_id": 4, "symbol": "AAPL", "shares": 100},
    ]
    assert pe.lot_for_call_away(lots, "AAPL", 100)["lot_id"] == 3
    assert pe.lot_for_call_away(lots, "AAPL", 200)["lot_id"] == 2
    assert pe.lot_for_call_away(lots, "AAPL", 300) is None
    assert pe.lot_for_call_away(lots, "TSLA", 100) is None
    assert pe.lot_for_call_away([], "AAPL", 100) is None


def test_a_call_with_no_matching_lot_settles_the_option_and_says_so(
        tmp_path, monkeypatch, caplog):
    """The degrade path. The option is already settled when the lot lookup runs,
    so refusing to guess leaves the ACCOUNT coherent and only the shares
    untouched — which is the honest outcome, and it is logged rather than
    silent.
    """
    db = _account(tmp_path)
    _no_repricer(monkeypatch)
    # 300 shares against a 1-contract call: no whole lot matches 100.
    pdb.insert_equity_lot(db, {"symbol": "AAPL", "shares": 300,
                               "cost_basis": 100.0, "source": "assignment"})
    pdb.debit_cash(db, 30_000.0)
    call_id = _open_covered_call(db, strike=105.0, credit=1.50, qty=1)

    with caplog.at_level("WARNING", logger="paper_engine"):
        pe.run_manage_cycle(_QuoteClient(110.0), _EXPIRY, db_path=db,
                            now_ct=_expiry_close_ct())

    assert _settled(db, call_id)["exit_reason"] == "CALLED_AWAY"
    assert len(pdb.fetch_open_lots(db)) == 1, "the wrong lot was disposed of"
    assert any("no open lot" in r.message or "no open lot" in r.getMessage()
               for r in caplog.records), "the skipped disposal was silent"
    _assert_coherent(db)
