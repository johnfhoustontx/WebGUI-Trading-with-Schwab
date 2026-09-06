"""Assignment: an in-the-money cash-secured short put becomes shares at expiry.

This is the wheel's closing move — the short put is settled, the reservation that
secured it pays for the stock, and an ``equity_lots`` row records the shares at
the STRIKE.

⚠ The invariant that matters here is `reconcile_buying_power(db) == 0.0` after a
real assignment, driven end-to-end from ``run_manage_cycle`` rather than asserted
on a hand-built payload. ``paper_engine._close`` already releases the position's
reserved buying power; an assignment step that released it a second time would
credit the strike notional to cash twice, and NOTHING else in this file — not the
lot, not the share count, not the cost basis — would look any different.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import paper_account_db as pdb
import paper_engine as pe

_CT = ZoneInfo("America/Chicago")

_EXPIRY = "2026-06-03"
START_CASH = 25_000.0


def _expiry_close_ct():
    """15:05 CT on the expiry day — past ``should_settle``'s 15:00 gate."""
    return datetime(2026, 6, 3, 15, 5, tzinfo=_CT)


class _QuoteClient:
    """Minimal client whose get_quotes([sym]) yields a settlement lastPrice."""

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
    """An expired chain prices nothing — settlement falls back to a direct quote.

    Mirrors ``test_paper_engine.test_manage_cycle_settles_at_close_with_fetched_underlying``
    rather than inventing a second stub shape.
    """
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing",
                        lambda trade, client: {
                            "current_value": None, "unrealized_pnl": None,
                            "pnl_pct_of_credit": None, "current_underlying": None,
                            "current_short_delta": None, "error": "expired"})


def _account(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, START_CASH, _EXPIRY)
    return db


def _open_short_put(db, *, strike, credit, qty, strategy="SHORT_PUT"):
    """Open a cash-secured short put the way the naked-single open path does.

    The strike notional is the reservation (``options_svc.compute._adhoc_single``
    stores ``max_loss_total = strike * 100 * qty`` for a NAKED_PUT), reserved
    before the row is inserted — the same two-step ``run_entry_cycle`` makes for a
    spread. ``run_entry_cycle`` itself cannot be used: it sizes off ``width``,
    which a single-leg short has none of.
    """
    notional = round(strike * 100 * qty, 2)
    pdb.reserve_buying_power(db, notional)
    return pdb.insert_position(db, {
        "signal_id": f"csp-{strike}", "symbol": "AAPL", "strategy": strategy,
        "short_strike": strike, "long_strike": None,
        "call_short": None, "call_long": None, "width": None,
        "expiration": _EXPIRY, "dte_at_entry": 35, "quantity": qty,
        "entry_credit": credit, "entry_order_id": None,
        "max_loss_per": strike, "max_loss_total": notional,
        "entry_ts": "2026-04-29T09:31:00"})


def _open_put_spread(db, *, short_strike, long_strike, credit, qty):
    """A PCS — the negative case. Two legs settle against each other; no shares."""
    width = round(short_strike - long_strike, 2)
    notional = round((width - credit) * 100 * qty, 2)
    pdb.reserve_buying_power(db, notional)
    return pdb.insert_position(db, {
        "signal_id": "pcs-1", "symbol": "AAPL", "strategy": "PCS",
        "short_strike": short_strike, "long_strike": long_strike,
        "call_short": None, "call_long": None, "width": width,
        "expiration": _EXPIRY, "dte_at_entry": 35, "quantity": qty,
        "entry_credit": credit, "entry_order_id": None,
        "max_loss_per": round(width - credit, 2), "max_loss_total": notional,
        "entry_ts": "2026-04-29T09:31:00"})


def _settled(db, position_id):
    """The closed position row — proof the settlement branch actually ran."""
    return next(p for p in pdb.fetch_all_positions(db)
                if p["position_id"] == position_id)


def test_an_itm_short_put_becomes_shares_at_the_strike(tmp_path, monkeypatch):
    """The wheel's whole loop. Premium is kept; cash buys the stock at the
    STRIKE, not the settlement price."""
    db = _account(tmp_path)
    pos_id = _open_short_put(db, strike=100.0, credit=2.0, qty=1)
    _no_repricer(monkeypatch)

    pe.run_manage_cycle(_QuoteClient(92.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())

    lots = pdb.fetch_open_lots(db)
    assert len(lots) == 1
    assert lots[0]["shares"] == 100
    assert lots[0]["cost_basis"] == 100.0          # the strike, NOT the 92.0 settlement
    assert lots[0]["source"] == "assignment"
    assert lots[0]["source_position_id"] == pos_id

    row = _settled(db, pos_id)
    assert row["status"] == "EXPIRED"
    assert row["exit_reason"] == "ASSIGNED"
    # The credit is KEPT: the assignment's loss lives in the share basis (bought
    # at 100, worth 92), never in the option's realized P&L.
    assert row["realized_pnl"] > 0

    # Cash: the strike notional bought the shares; the premium stayed.
    assert pdb.get_account(db)["cash"] == round(
        START_CASH + row["realized_pnl"] - 10_000.0, 2)
    assert pdb.equity_at_cost(db) == 10_000.0


def test_assignment_leaves_buying_power_reconciled(tmp_path, monkeypatch):
    """The invariant, driven end-to-end from the PRODUCER rather than asserted
    on a hand-built payload. A double release shows up here immediately."""
    db = _account(tmp_path)
    _open_short_put(db, strike=100.0, credit=2.0, qty=1)
    _no_repricer(monkeypatch)

    pe.run_manage_cycle(_QuoteClient(92.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())

    # Not decoration: reconciliation is trivially 0.0 when nothing was assigned,
    # so without this the test would pass in exactly the world it exists to rule
    # out — an assignment step that never ran.
    assert pdb.fetch_open_lots(db), "nothing was assigned; the check below is vacuous"
    assert pdb.reconcile_buying_power(db) == 0.0
    assert pdb.get_account(db)["buying_power_reserved"] == 0.0


def test_an_otm_short_put_expires_worthless_and_makes_no_lot(tmp_path, monkeypatch):
    """Above the strike the put is abandoned. Asserting EXPIRED as well keeps the
    empty-lot check honest — 'no lot' is trivially true if settlement never ran."""
    db = _account(tmp_path)
    pos_id = _open_short_put(db, strike=100.0, credit=2.0, qty=1)
    _no_repricer(monkeypatch)

    pe.run_manage_cycle(_QuoteClient(108.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())

    row = _settled(db, pos_id)
    assert row["status"] == "EXPIRED"
    assert row["exit_reason"] == "EXPIRED"
    assert pdb.fetch_open_lots(db) == []
    assert pdb.reconcile_buying_power(db) == 0.0


def test_an_expiring_spread_does_not_produce_shares(tmp_path, monkeypatch):
    """A defined-risk spread that finishes ITM settles its two legs against each
    other. It does not buy stock, however deep in the money the short leg is."""
    db = _account(tmp_path)
    pos_id = _open_put_spread(db, short_strike=100.0, long_strike=95.0,
                              credit=1.5, qty=1)
    _no_repricer(monkeypatch)

    pe.run_manage_cycle(_QuoteClient(92.0), _EXPIRY, db_path=db,
                        now_ct=_expiry_close_ct())

    row = _settled(db, pos_id)
    assert row["status"] == "EXPIRED"
    assert row["exit_reason"] == "EXPIRED"
    assert pdb.fetch_open_lots(db) == []
    assert pdb.equity_at_cost(db) == 0.0
    assert pdb.reconcile_buying_power(db) == 0.0
