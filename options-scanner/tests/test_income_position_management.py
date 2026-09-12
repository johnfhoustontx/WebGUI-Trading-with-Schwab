"""Live management of the Income Window's single-leg positions.

Before 2026-09-11 these were unmanaged: ``signal_repricer.reprice_swing`` knew
only PCS/CCS/IC, so ``run_manage_cycle`` hit ``per_contract is None`` and skipped
the position entirely - no mark, no rule, no way to close it, and an error in the
journal every cycle. The repricer now prices them, which makes a second thing
load-bearing: the cycle must hand the STRATEGY to the rule engine, because the
credit-spread stops are wrong for these two structures. Which rules each
structure gets is now ``[structures.*]`` in config/trade_mgmt.toml (gap
assessment B1), read per position through
``shared.trade_mgmt.structure_rules``.

These tests drive the real recommender - only the repricer is stubbed, since it
has its own tests - so they fail if either half is missing.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import paper_account_db as pdb
import paper_engine as pe

_CT = ZoneInfo("America/Chicago")

_EXPIRY = "2026-06-03"          # the position's expiry — DTE 14, inside manage_dte
_EXPIRY_FAR = "2026-06-25"      # DTE 36 — outside it, so the target rules alone
_TODAY = "2026-05-20"           # two weeks before the near one, so nothing settles
_NOW_CT = datetime(2026, 5, 20, 10, 0, tzinfo=_CT)


class _FakeBroker:
    """Same shape as ``test_paper_engine._FakeBroker``: FILLED at a fixed price,
    recording the orders. The engine reads ``price`` off the response."""

    def __init__(self, price=0.10):
        self.price = price
        self.calls = []

    def submit_order(self, order, client):
        self.calls.append(order)
        return {"orderId": 1, "status": "FILLED", "orderType": "NET_DEBIT",
                "quantity": order["quantity"], "filledQuantity": order["quantity"],
                "price": self.price, "enteredTime": "t", "closeTime": "t",
                "orderStrategyType": "SINGLE", "complexOrderStrategyType": "SINGLE",
                "orderLegCollection": [], "statusDescription": None}


def _account(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, _TODAY)
    return db


def _open_short_put(db, *, strike=100.0, credit=2.0, qty=1, strategy="SHORT_PUT",
                    expiration=_EXPIRY):
    """Mirrors ``test_assignment._open_short_put``: the strike notional is the
    reservation, reserved before the row is inserted. ``run_entry_cycle`` cannot
    open one - it sizes off ``width``, which a single-leg short has none of."""
    notional = round(strike * 100 * qty, 2)
    pdb.reserve_buying_power(db, notional)
    return pdb.insert_position(db, {
        "signal_id": f"csp-{strike}", "symbol": "AAPL", "strategy": strategy,
        "short_strike": strike, "long_strike": None,
        "call_short": None, "call_long": None, "width": None,
        "expiration": expiration, "dte_at_entry": 35, "quantity": qty,
        "entry_credit": credit, "entry_order_id": None,
        "max_loss_per": strike, "max_loss_total": notional,
        "entry_ts": "2026-04-29T09:31:00"})


def _open_covered_call(db, *, strike=110.0, credit=0.80, qty=1):
    """A covered call reserves NOTHING and stores ``max_loss_total = 0.0`` - the
    shares are the collateral and ``equity_at_cost`` already counts them."""
    return pdb.insert_position(db, {
        "signal_id": f"cc-{strike}", "symbol": "AAPL", "strategy": "COVERED_CALL",
        "short_strike": strike, "long_strike": None,
        "call_short": None, "call_long": None, "width": None,
        "expiration": _EXPIRY, "dte_at_entry": 35, "quantity": qty,
        "entry_credit": credit, "entry_order_id": None,
        "max_loss_per": 0.0, "max_loss_total": 0.0,
        "entry_ts": "2026-04-29T09:31:00"})


def _mark(monkeypatch, *, value, pnl, delta=-0.55, underlying=95.0):
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing",
                        lambda trade, client: {
                            "current_value": value, "unrealized_pnl": pnl,
                            "pnl_pct_of_credit": 0.0,
                            "current_underlying": underlying,
                            "current_short_delta": delta, "error": None})


def _position(db, position_id):
    return next(p for p in pdb.fetch_all_positions(db)
                if p["position_id"] == position_id)


def test_a_cash_secured_put_gets_a_mark_and_is_not_money_stopped(tmp_path, monkeypatch):
    """The behaviour that was missing outright. A $2.00 credit down to $8.00 is a
    -3x credit loss, which would money-stop a spread; the wheel rides it to
    assignment instead."""
    db = _account(tmp_path)
    pos_id = _open_short_put(db, strike=100.0, credit=2.0, qty=1)
    _mark(monkeypatch, value=8.0, pnl=-600.0)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(), db, now_ct=_NOW_CT)

    row = _position(db, pos_id)
    assert row["status"] == "OPEN"                    # not cut
    assert row["current_value"] == 8.0                # and it IS marked now
    assert row["unrealized_pnl"] == -600.0


def test_the_manage_cycle_hands_the_strategy_to_the_rule_engine(tmp_path, monkeypatch):
    """The plumbing the exemption depends on. Without the strategy in the ctx the
    rule engine cannot tell a cash-secured put from a put credit spread, and the
    spread's stops would run on it."""
    db = _account(tmp_path)
    _open_short_put(db, strategy="NAKED_PUT")
    _mark(monkeypatch, value=2.1, pnl=-10.0)
    seen = {}

    def _spy(ctx):
        seen.update(ctx)
        return {"action": "HOLD", "reason": "spy", "code": "HOLD"}

    monkeypatch.setattr(pe.signal_recommender, "recommend", _spy)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(), db, now_ct=_NOW_CT)

    assert seen.get("strategy") == "NAKED_PUT"


def test_a_covered_call_still_closes_at_the_profit_target(tmp_path, monkeypatch):
    """Guard, not a gap: the target must keep acting on these structures, which is
    the whole of their management until a per-structure rule table exists."""
    db = _account(tmp_path)
    pos_id = _open_covered_call(db, strike=110.0, credit=0.80, qty=1)
    _mark(monkeypatch, value=0.20, pnl=60.0, delta=0.15, underlying=104.0)   # +75%

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(0.20), db, now_ct=_NOW_CT)

    row = _position(db, pos_id)
    assert row["status"] == "CLOSED"
    assert row["exit_reason"] == "TARGET_HIT"


def test_a_cash_secured_put_is_not_delta_stopped(tmp_path, monkeypatch):
    """A short put's delta rises as assignment approaches, which is the plan -
    not a reason to cut."""
    db = _account(tmp_path)
    pos_id = _open_short_put(db, strike=100.0, credit=2.0, qty=1)
    _mark(monkeypatch, value=3.0, pnl=-100.0, delta=-0.72, underlying=98.0)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(), db, now_ct=_NOW_CT)

    assert _position(db, pos_id)["status"] == "OPEN"


# ── manage_dte, end to end (gap assessment B1) ───────────────────────────────


def test_a_profitable_cash_secured_put_is_closed_inside_the_manage_window(
        tmp_path, monkeypatch):
    """DTE 14, +30% of the credit: short of the profit target, so before B1 this
    position ground on through the highest-gamma stretch of its life for the last
    few percent."""
    db = _account(tmp_path)
    pos_id = _open_short_put(db, strike=100.0, credit=2.0, qty=1)
    _mark(monkeypatch, value=1.40, pnl=60.0, delta=-0.12, underlying=107.0)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(1.40), db, now_ct=_NOW_CT)

    row = _position(db, pos_id)
    assert row["status"] == "CLOSED"
    assert row["exit_reason"] == "MANAGE_DTE"


def test_an_underwater_cash_secured_put_rides_through_the_manage_window(
        tmp_path, monkeypatch):
    """The other half of the same rule, and the reason it is not just a time stop
    with a new name: the wheel takes the shares."""
    db = _account(tmp_path)
    pos_id = _open_short_put(db, strike=100.0, credit=2.0, qty=1)
    _mark(monkeypatch, value=2.60, pnl=-60.0, delta=-0.44, underlying=99.0)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(2.60), db, now_ct=_NOW_CT)

    assert _position(db, pos_id)["status"] == "OPEN"


def test_the_break_even_arm_follows_the_structures_own_profit_target(
        tmp_path, monkeypatch):
    """``run_manage_cycle`` decides whether to ARM break-even from its own
    threshold read, separately from ``recommend()``'s. A per-structure ``tp_frac``
    makes those two reads capable of disagreeing — the cycle arming at 50% while
    the rule engine holds out for 90%, which then hands rule 3's break-even stop a
    position the target has not reached. Both must read the same table."""
    from shared import trade_mgmt

    real = trade_mgmt.structure_rules
    monkeypatch.setattr(trade_mgmt, "structure_rules",
                        lambda s: ({**real(s), "tp_frac": 0.90}
                                   if s == "SHORT_PUT" else real(s)))
    db = _account(tmp_path)
    pos_id = _open_short_put(db, strike=100.0, credit=2.0, expiration=_EXPIRY_FAR)
    _mark(monkeypatch, value=0.80, pnl=120.0, delta=-0.10, underlying=110.0)  # +60%

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(0.80), db, now_ct=_NOW_CT,
                        lifecycle=True, be_level_fn=lambda pos: 0.0)

    row = _position(db, pos_id)
    assert row["status"] == "OPEN"
    assert not row["be_armed"]


def test_the_break_even_arm_still_fires_at_the_shipped_target(tmp_path, monkeypatch):
    """The control for the test above: at the shipped 0.50 the same +60% mark
    arms and holds, so that test is about the table and not about the mark."""
    db = _account(tmp_path)
    pos_id = _open_short_put(db, strike=100.0, credit=2.0, expiration=_EXPIRY_FAR)
    _mark(monkeypatch, value=0.80, pnl=120.0, delta=-0.10, underlying=110.0)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(0.80), db, now_ct=_NOW_CT,
                        lifecycle=True, be_level_fn=lambda pos: 0.0)

    row = _position(db, pos_id)
    assert row["status"] == "OPEN"
    assert row["be_armed"]
