"""The paper books remember the delta they opened at (gap assessment B6).

``[stops].delta_drift`` is designed to measure adverse movement RELATIVE to
entry - so a spread sold at 0.30 is not cut on noise the way a flat 0.35 ceiling
would cut it - but ``paper_positions`` had no column for the entry delta. Every
position therefore fell to ``delta_abs_fallback`` (0.35), which is:

* **too tight** for anything sold rich: a 0.30-delta short is 0.05 from being
  cut the moment it opens, and
* **too loose** for anything sold cheap: a 0.10-delta short has to more than
  triple before the stop notices, where the drift rule would have acted at 0.22.

That matters more than it sounds. The 2026-08-25 calibration measured the stop
ladder as the app's actual edge - realized avg-win/avg-loss of 1.9-2.6 against a
hold-to-expiry 0.2 - and ``DELTA_STOP`` is the second most expensive rung in it.
A stop firing on the wrong threshold is a direct tax on the one thing that works.

⚠ The income structures are deliberately unaffected: ``loss_rules = false``
means they have no delta stop for an entry delta to sharpen.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import paper_account_db as pdb
import paper_engine as pe
import signal_recommender as rec
import sqlite3

_CT = ZoneInfo("America/Chicago")
_NOW_CT = datetime(2026, 5, 20, 10, 0, tzinfo=_CT)
_TODAY = "2026-05-20"
_EXPIRY = "2026-06-25"


class _FakeBroker:
    def __init__(self, price=0.48):
        self.price = price
        self.calls = []

    def submit_order(self, order, client):
        self.calls.append(order)
        return {"orderId": 1, "status": "FILLED", "orderType": "NET_CREDIT",
                "quantity": order["quantity"], "filledQuantity": order["quantity"],
                "price": self.price, "enteredTime": "t", "closeTime": "t",
                "orderStrategyType": "SINGLE", "complexOrderStrategyType": "VERTICAL",
                "orderLegCollection": [], "statusDescription": None}


def _sig(**kw):
    # $1 wide at a 0.40 fill -> $60 max loss a contract, so 4 fit inside the
    # $250 per-trade cap. A $5 width would be refused RISK_TOO_HIGH and every
    # test below would pass or fail for that reason instead.
    base = {"signal_id": "s1", "symbol": "SPY", "strategy": "PCS",
            "short_strike": 500, "long_strike": 499, "width": 1.0,
            "expiration": _EXPIRY, "dte_at_entry": 36,
            "entry_credit": 0.50, "entry_score": 80, "recommendation": None,
            "entry_short_delta": 0.30}
    base.update(kw)
    return base


def _account(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, _TODAY)
    return db


def _mark(monkeypatch, *, value, pnl, delta):
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing",
                        lambda trade, client: {
                            "current_value": value, "unrealized_pnl": pnl,
                            "pnl_pct_of_credit": 0.0, "current_underlying": 498.0,
                            "current_short_delta": delta, "error": None})


# --- the column ------------------------------------------------------------

def test_a_position_persists_the_delta_it_opened_at(tmp_path):
    db = _account(tmp_path)
    pid = pdb.insert_position(db, {
        "signal_id": "x", "symbol": "SPY", "strategy": "PCS",
        "short_strike": 500.0, "long_strike": 495.0, "width": 5.0,
        "expiration": _EXPIRY, "dte_at_entry": 36, "quantity": 1,
        "entry_credit": 1.00, "entry_order_id": None, "max_loss_per": 400.0,
        "max_loss_total": 400.0, "entry_ts": "t", "entry_short_delta": 0.27})

    row = next(p for p in pdb.fetch_all_positions(db) if p["position_id"] == pid)
    assert row["entry_short_delta"] == 0.27


def test_an_existing_database_gains_the_column(tmp_path):
    """The additive-migration contract: a book opened before B6 must keep
    working, with NULL meaning "not recorded" rather than "zero delta"."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE paper_positions (position_id INTEGER PRIMARY KEY, "
        "signal_id TEXT, symbol TEXT, strategy TEXT, status TEXT DEFAULT 'OPEN');"
        "INSERT INTO paper_positions (signal_id, symbol, strategy) "
        "VALUES ('old', 'SPY', 'PCS');")
    conn.commit()
    conn.close()

    pdb.init_db(db)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM paper_positions").fetchone()
        assert "entry_short_delta" in row.keys()
        assert row["entry_short_delta"] is None
    finally:
        conn.close()


# --- the producers ---------------------------------------------------------

def test_the_entry_cycle_records_the_signals_delta(tmp_path):
    db = _account(tmp_path)

    pe.run_entry_cycle(None, _TODAY, [_sig(entry_short_delta=0.18)],
                       _FakeBroker(0.40), db)

    assert pdb.fetch_open_positions(db)[0]["entry_short_delta"] == 0.18


def test_a_signal_with_no_delta_records_nothing_rather_than_zero(tmp_path):
    """``None`` and ``0.0`` are different claims: one is "not recorded", which
    falls back to the absolute ceiling, and the other is a delta of zero, which
    would make the drift rule fire at 0.12 on a position that never moved."""
    db = _account(tmp_path)
    sig = _sig()
    del sig["entry_short_delta"]

    pe.run_entry_cycle(None, _TODAY, [sig], _FakeBroker(0.40), db)

    assert pdb.fetch_open_positions(db)[0]["entry_short_delta"] is None


# --- the manage cycle reads it ---------------------------------------------

def test_the_manage_cycle_hands_the_entry_delta_to_the_rule_engine(tmp_path, monkeypatch):
    """The discriminating test. ``entry_short_delta`` was already threaded into
    the ctx - but only inside the LIFECYCLE branch, and lifecycle is off by
    default for the manual book and always off for the driver. So the column
    alone would have changed nothing for either book that actually trades."""
    db = _account(tmp_path)
    pe.run_entry_cycle(None, _TODAY, [_sig(entry_short_delta=0.22)],
                       _FakeBroker(0.40), db)
    _mark(monkeypatch, value=0.55, pnl=-10.0, delta=0.25)
    seen = {}

    def _spy(ctx):
        seen.update(ctx)
        return {"action": "HOLD", "reason": "spy", "code": "HOLD"}

    monkeypatch.setattr(pe.signal_recommender, "recommend", _spy)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(), db, now_ct=_NOW_CT)

    assert seen.get("entry_short_delta") == 0.22


def test_a_spread_sold_rich_is_not_cut_at_the_absolute_fallback(tmp_path, monkeypatch):
    """What B6 actually buys. Opened at 0.30 and now 0.34: past the 0.35-ish
    region the fallback polices, but only 0.04 of drift - inside the 0.12 the
    rule is written around. Before B6 this position was cut."""
    db = _account(tmp_path)
    pe.run_entry_cycle(None, _TODAY, [_sig(entry_short_delta=0.30)],
                       _FakeBroker(0.40), db)
    _mark(monkeypatch, value=0.55, pnl=-10.0, delta=0.36)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(0.70), db, now_ct=_NOW_CT)

    assert pdb.fetch_open_positions(db), "cut on the absolute fallback, not the drift"


def test_the_same_spread_IS_cut_once_the_drift_is_real(tmp_path, monkeypatch):
    """The other half: 0.30 + 0.12 of drift is a genuine breach, and it has to
    still fire. Without this the test above would pass by disabling the stop."""
    db = _account(tmp_path)
    pe.run_entry_cycle(None, _TODAY, [_sig(entry_short_delta=0.30)],
                       _FakeBroker(0.40), db)
    _mark(monkeypatch, value=0.80, pnl=-30.0, delta=0.43)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(0.80), db, now_ct=_NOW_CT)

    closed = [p for p in pdb.fetch_all_positions(db) if p["status"] == "CLOSED"]
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "DELTA_STOP"


def test_a_cheap_short_is_cut_EARLIER_than_the_fallback_would(tmp_path, monkeypatch):
    """The loose end of the same coin, and the one nobody notices: sold at 0.10,
    now 0.24. The fallback would have waited for 0.35 - more than a tripling of
    the risk the trade was sized for."""
    db = _account(tmp_path)
    pe.run_entry_cycle(None, _TODAY, [_sig(entry_short_delta=0.10)],
                       _FakeBroker(0.40), db)
    _mark(monkeypatch, value=0.75, pnl=-25.0, delta=0.24)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(0.75), db, now_ct=_NOW_CT)

    closed = [p for p in pdb.fetch_all_positions(db) if p["status"] == "CLOSED"]
    assert len(closed) == 1 and closed[0]["exit_reason"] == "DELTA_STOP"


def test_an_unrecorded_delta_still_uses_the_absolute_fallback(tmp_path, monkeypatch):
    """Back-compat for every position already in the book: NULL keeps exactly
    the pre-B6 behaviour rather than disabling the stop."""
    db = _account(tmp_path)
    sig = _sig()
    del sig["entry_short_delta"]
    pe.run_entry_cycle(None, _TODAY, [sig], _FakeBroker(0.40), db)
    _mark(monkeypatch, value=0.80, pnl=-30.0, delta=0.36)

    pe.run_manage_cycle(None, _TODAY, _FakeBroker(0.80), db, now_ct=_NOW_CT)

    closed = [p for p in pdb.fetch_all_positions(db) if p["status"] == "CLOSED"]
    assert len(closed) == 1 and closed[0]["exit_reason"] == "DELTA_STOP"


def test_a_rolled_position_records_its_OWN_entry_delta(tmp_path):
    """A roll is a new entry at a new strike, so it takes the candidate's delta.
    Inheriting the old position's would measure drift from a strike that is no
    longer in the trade."""
    import paper_adjust

    db = _account(tmp_path)
    pid = pdb.insert_position(db, {
        "signal_id": "r1", "symbol": "SPY", "strategy": "PCS",
        "short_strike": 500.0, "long_strike": 499.0, "width": 1.0,
        "expiration": _EXPIRY, "dte_at_entry": 36, "quantity": 1,
        "entry_credit": 0.50, "entry_order_id": None, "max_loss_per": 50.0,
        "max_loss_total": 50.0, "entry_ts": "t", "entry_short_delta": 0.20})
    pos = next(p for p in pdb.fetch_all_positions(db) if p["position_id"] == pid)
    candidate = {
        "action": "roll_out", "new_expiry": "2026-07-25", "new_width": 1.0,
        "new_max_loss": 50.0, "new_short_delta": 0.34, "commission": 2.60,
        "est_fill_legs": [
            {"side": "BUY", "right": "PUT", "strike": 500.0, "expiry": _EXPIRY,
             "qty": 1, "price": 0.80},
            {"side": "SELL", "right": "PUT", "strike": 499.0, "expiry": _EXPIRY,
             "qty": 1, "price": 0.30},
            {"side": "SELL", "right": "PUT", "strike": 500.0, "expiry": "2026-07-25",
             "qty": 1, "price": 1.20},
            {"side": "BUY", "right": "PUT", "strike": 499.0, "expiry": "2026-07-25",
             "qty": 1, "price": 0.55}]}

    res = paper_adjust.apply_roll(db, pos, candidate)

    assert res["ok"], res
    rolled = next(p for p in pdb.fetch_all_positions(db)
                  if p["position_id"] == res["new_position_id"])
    assert rolled["entry_short_delta"] == 0.34


def test_the_income_structures_are_unaffected_by_any_of_this():
    """``loss_rules = false`` means there is no delta stop to sharpen. Pinned so
    that a future change to the drift rule cannot quietly reach them."""
    ctx = {"entry_credit": 1.70, "unrealized_pnl": -40.0,
           "current_short_delta": -0.80, "entry_short_delta": -0.15,
           "dte_remaining": 30}
    assert rec.recommend({**ctx, "strategy": "PCS"})["code"] == "DELTA_STOP"
    assert rec.recommend({**ctx, "strategy": "SHORT_PUT"})["action"] == "HOLD"
