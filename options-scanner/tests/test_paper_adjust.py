"""Tests for paper_adjust.py — rescue apply primitives.

Each primitive mutates the paper DB in one logical operation, writes a
position_adjustments audit row, realizes commission + any net cash into the
account, and returns a result dict.
"""
import paper_account_db as pdb
import paper_adjust as pa


#############################################
# SEED HELPERS
#############################################

def _seed_account(tmp_path, cash=25_000.0):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, cash, "2026-06-03")
    return db


def _seed_position(db, **kw):
    """Insert one OPEN PCS position and reserve its BP (mirrors the entry path)."""
    base = dict(
        signal_id="s1", symbol="SPY", strategy="PCS",
        short_strike=500.0, long_strike=495.0,
        call_short=None, call_long=None,
        width=5.0, expiration="2026-07-31", dte_at_entry=58,
        quantity=2, entry_credit=1.00, entry_order_id=1,
        max_loss_per=400.0, max_loss_total=800.0, entry_ts="t",
    )
    base.update(kw)
    pid = pdb.insert_position(db, base)
    pdb.reserve_buying_power(db, base["max_loss_total"])
    return pid


def _pos(db, pid):
    return next(p for p in pdb.fetch_all_positions(db) if p["position_id"] == pid)


def _leg(side, right, strike, qty=2, price=1.0, expiry="2026-07-31"):
    return {"side": side, "right": right, "strike": strike,
            "expiry": expiry, "qty": qty, "price": price}


#############################################
# apply_close
#############################################

def test_apply_close_closes_position_and_records(tmp_path):
    db = _seed_account(tmp_path)
    pid = _seed_position(db)
    pos = _pos(db, pid)
    # debit to close 0.30/contract * 100 * 2 = -60 gross, comm 2*2*0.65 = 2.60
    cand = {"action": "close", "gross_cash": -60.0, "commission": 2.60,
            "net_cash": -62.60, "est_fill_legs": [
                _leg("BUY", "PUT", 500.0), _leg("SELL", "PUT", 495.0)]}
    cash0 = pdb.get_account(db)["cash"]
    res = pa.apply_close(db, pos, cand)

    assert res["ok"] is True
    assert res["action"] == "close"
    assert res["position_id"] == pid
    p = _pos(db, pid)
    assert p["status"] == "CLOSED"
    # realized = (entry_credit - exit_debit) * 100 * qty.  exit_debit = 0.30
    # = (1.00 - 0.30) * 100 * 2 = 140
    assert res["realized"] == 140.0
    assert p["realized_pnl"] == 140.0
    # cash: + released BP (800) + realized (140) - commission (2.60)
    assert pdb.get_account(db)["cash"] == round(cash0 + 800.0 + 140.0 - 2.60, 2)
    # BP released
    assert pdb.get_account(db)["buying_power_reserved"] == 0.0
    rows = pdb.list_adjustments(db, pid)
    assert len(rows) == 1 and rows[0]["action"] == "close"


#############################################
# apply_partial_close
#############################################

def test_apply_partial_close_reduces_qty_stays_open(tmp_path):
    db = _seed_account(tmp_path)
    pid = _seed_position(db, quantity=2, max_loss_total=800.0)
    pos = _pos(db, pid)
    acct0 = pdb.get_account(db)
    cash0 = acct0["cash"]
    rp0 = acct0["realized_pnl"]
    # reserved starts at the full 800 (seed reserved the entry max loss)
    assert acct0["buying_power_reserved"] == 800.0
    # close 1 of 2; gross -30 (0.30 * 100 * 1), comm 2*1*0.65 = 1.30
    cand = {"action": "partial_close", "gross_cash": -30.0, "commission": 1.30,
            "net_cash": -31.30, "new_max_loss": 400.0,
            "est_fill_legs": [_leg("BUY", "PUT", 500.0, qty=1)]}
    res = pa.apply_partial_close(db, pos, cand)

    assert res["ok"] is True
    p = _pos(db, pid)
    assert p["status"] == "OPEN"
    assert p["quantity"] == 1                    # 2 - (2//2)
    assert p["max_loss_total"] == 400.0          # new_max_loss from candidate
    rows = pdb.list_adjustments(db, pid)
    assert len(rows) == 1 and rows[0]["action"] == "partial_close"

    # --- account-level invariants ---
    acct = pdb.get_account(db)
    # reserved == remaining fraction of old ml (== new max_loss_total)
    assert acct["buying_power_reserved"] == 400.0 == p["max_loss_total"]
    # realized P&L reflects the closed slice (exit_debit 0.30):
    #   (entry_credit 1.00 - 0.30) * 100 * 1 = 70, minus commission 1.30
    closed_realized = round((1.00 - 0.30) * 100 * 1, 2)   # 70.0
    assert res["realized"] == closed_realized
    assert acct["realized_pnl"] == round(rp0 + closed_realized - 1.30, 2)
    # cash: + released BP (400) + closed slice realized (70) - commission (1.30)
    assert acct["cash"] == round(cash0 + 400.0 + 70.0 - 1.30, 2)


#############################################
# apply_narrow
#############################################

def test_apply_narrow_updates_strikes_width_maxloss(tmp_path):
    db = _seed_account(tmp_path)
    pid = _seed_position(db)
    pos = _pos(db, pid)
    acct0 = pdb.get_account(db)
    cash0 = acct0["cash"]
    rp0 = acct0["realized_pnl"]
    assert acct0["buying_power_reserved"] == 800.0
    cand = {"action": "narrow", "gross_cash": -100.0, "commission": 2.60,
            "net_cash": -102.60, "new_width": 2.0, "new_max_loss": 600.0,
            "est_fill_legs": [_leg("SELL", "PUT", 495.0), _leg("BUY", "PUT", 498.0)]}
    res = pa.apply_narrow(db, pos, cand)

    assert res["ok"] is True
    p = _pos(db, pid)
    assert p["status"] == "OPEN"
    assert p["long_strike"] == 498.0             # new long from est_fill_legs (BUY)
    assert p["width"] == 2.0
    assert p["max_loss_total"] == 600.0
    rows = pdb.list_adjustments(db, pid)
    assert len(rows) == 1 and rows[0]["action"] == "narrow"

    # --- account-level invariants (pins the BP-reconciliation bug fix) ---
    acct = pdb.get_account(db)
    # reserved BP now tracks the new max_loss_total (was the bug: stayed 800)
    assert acct["buying_power_reserved"] == 600.0 == p["max_loss_total"]
    # cash: + released BP delta (800-600=200) + net_cash (-102.60)
    assert acct["cash"] == round(cash0 + 200.0 - 102.60, 2)
    # net_cash is realized P&L (the spread debit incl. commission); BP release
    # is a balance-sheet move, not P&L
    assert acct["realized_pnl"] == round(rp0 - 102.60, 2)


#############################################
# apply_convert_ic
#############################################

def test_apply_convert_ic_sets_call_legs_and_strategy(tmp_path):
    db = _seed_account(tmp_path)
    pid = _seed_position(db)
    pos = _pos(db, pid)
    cash0 = pdb.get_account(db)["cash"]
    cand = {"action": "convert_ic", "gross_cash": 80.0, "commission": 2.60,
            "net_cash": 77.40, "new_max_loss": 720.0,
            "est_fill_legs": [_leg("SELL", "CALL", 510.0), _leg("BUY", "CALL", 515.0)]}
    rp0 = pdb.get_account(db)["realized_pnl"]
    res = pa.apply_convert_ic(db, pos, cand)

    assert res["ok"] is True
    p = _pos(db, pid)
    assert p["status"] == "OPEN"
    assert p["strategy"] == "IC"
    assert p["call_short"] == 510.0
    assert p["call_long"] == 515.0
    assert p["max_loss_total"] == 720.0
    # --- account-level invariants (pins the BP-reconciliation bug fix) ---
    acct = pdb.get_account(db)
    # reserved BP now tracks the new max_loss_total (was the bug: stayed 800)
    assert acct["buying_power_reserved"] == 720.0 == p["max_loss_total"]
    # cash: net credit (+77.40) + released BP delta (800-720=80)
    assert acct["cash"] == round(cash0 + 77.40 + 80.0, 2)
    assert acct["realized_pnl"] == round(rp0 + 77.40, 2)
    rows = pdb.list_adjustments(db, pid)
    assert len(rows) == 1 and rows[0]["action"] == "convert_ic"


#############################################
# apply_convert_butterfly
#############################################

def test_apply_convert_butterfly_credits_cash(tmp_path):
    db = _seed_account(tmp_path)
    pid = _seed_position(db)
    pos = _pos(db, pid)
    cash0 = pdb.get_account(db)["cash"]
    cand = {"action": "convert_butterfly", "gross_cash": 120.0, "commission": 2.60,
            "net_cash": 117.40, "new_max_loss": 680.0,
            "est_fill_legs": [_leg("SELL", "CALL", 500.0), _leg("BUY", "CALL", 505.0)]}
    rp0 = pdb.get_account(db)["realized_pnl"]
    res = pa.apply_convert_butterfly(db, pos, cand)

    assert res["ok"] is True
    p = _pos(db, pid)
    assert p["status"] == "OPEN"
    assert p["max_loss_total"] == 680.0
    # --- account-level invariants (pins the BP-reconciliation bug fix) ---
    acct = pdb.get_account(db)
    # reserved BP now tracks the new max_loss_total (was the bug: stayed 800)
    assert acct["buying_power_reserved"] == 680.0 == p["max_loss_total"]
    # cash: net credit (+117.40) + released BP delta (800-680=120)
    assert acct["cash"] == round(cash0 + 117.40 + 120.0, 2)
    assert acct["realized_pnl"] == round(rp0 + 117.40, 2)
    rows = pdb.list_adjustments(db, pid)
    assert len(rows) == 1 and rows[0]["action"] == "convert_butterfly"


#############################################
# apply_roll
#############################################

def test_apply_roll_closes_old_opens_linked_new(tmp_path):
    db = _seed_account(tmp_path)
    pid = _seed_position(db)            # entry_credit 1.00, qty 2, max_loss 800
    pos = _pos(db, pid)
    acct0 = pdb.get_account(db)
    cash0 = acct0["cash"]
    rp0 = acct0["realized_pnl"]
    assert acct0["buying_power_reserved"] == 800.0
    # roll_down with DISTINCT leg prices so the close-debit / reopen-credit math
    # is genuinely exercised:
    #   close pair: BUY-back old short @0.80, SELL old long @0.30 -> debit 0.50
    #   reopen pair: SELL new short @0.70, BUY new long @0.25     -> credit 0.45
    cand = {"action": "roll_down", "gross_cash": 40.0, "commission": 5.20,
            "net_cash": 34.80, "new_width": 5.0, "new_max_loss": 760.0,
            "new_expiry": "2026-07-31",
            "est_fill_legs": [
                _leg("BUY", "PUT", 500.0, price=0.80),
                _leg("SELL", "PUT", 495.0, price=0.30),
                _leg("SELL", "PUT", 495.0, price=0.70),
                _leg("BUY", "PUT", 490.0, price=0.25)]}
    res = pa.apply_roll(db, pos, cand)

    assert res["ok"] is True
    new_id = res["new_position_id"]
    assert new_id is not None and new_id != pid
    old = _pos(db, pid)
    assert old["status"] == "CLOSED"
    assert old["realized_pnl"] is not None
    new = _pos(db, new_id)
    assert new["status"] == "OPEN"
    assert new["parent_position_id"] == pid
    assert new["short_strike"] == 495.0
    assert new["long_strike"] == 490.0
    assert new["max_loss_total"] == 760.0
    # reopen credit captured at the NEW strikes/prices (0.70 - 0.25)
    assert new["entry_credit"] == 0.45
    # close debit = 0.80 - 0.30 = 0.50; realized = (1.00 - 0.50)*100*2 = 100
    exit_debit = 0.50
    realized = round((1.00 - exit_debit) * 100 * 2, 2)   # 100.0
    assert res["realized"] == realized
    assert old["realized_pnl"] == realized

    # --- account-level invariants ---
    acct = pdb.get_account(db)
    # old BP (800) released by _close, new BP (760) reserved for the reopened
    # position => reserved == new position's max_loss_total
    assert acct["buying_power_reserved"] == 760.0 == new["max_loss_total"]
    # realized P&L: closed-spread P&L (100) minus commission (5.20)
    assert acct["realized_pnl"] == round(rp0 + realized - 5.20, 2)
    # cash: + released old BP (800) + close realized (100) - commission (5.20)
    #       - reserved new BP (760)
    assert acct["cash"] == round(cash0 + 800.0 + 100.0 - 5.20 - 760.0, 2)

    # adjustment row on the old position id
    rows = pdb.list_adjustments(db, pid)
    assert len(rows) == 1 and rows[0]["action"] == "roll_down"
    assert rows[0]["parent_position_id"] == pid


def test_apply_roll_out_changes_expiry(tmp_path):
    db = _seed_account(tmp_path)
    pid = _seed_position(db)
    pos = _pos(db, pid)
    cand = {"action": "roll_out", "gross_cash": 30.0, "commission": 5.20,
            "net_cash": 24.80, "new_width": 5.0, "new_max_loss": 770.0,
            "new_expiry": "2026-08-30",
            "est_fill_legs": [
                _leg("SELL", "PUT", 500.0, expiry="2026-08-30"),
                _leg("BUY", "PUT", 495.0, expiry="2026-08-30")]}
    res = pa.apply_roll(db, pos, cand)
    assert res["ok"] is True
    new = _pos(db, res["new_position_id"])
    assert new["expiration"] == "2026-08-30"
    assert new["short_strike"] == 500.0
    assert new["long_strike"] == 495.0


#############################################
# apply_inverted (advisory guard)
#############################################

def test_apply_inverted_refuses(tmp_path):
    db = _seed_account(tmp_path)
    pid = _seed_position(db, strategy="IC", call_short=520.0, call_long=525.0)
    pos = _pos(db, pid)
    cand = {"action": "inverted"}
    res = pa.apply_inverted(db, pos, cand)
    assert res["ok"] is False
    assert "advisory" in res["error"].lower()
    # no mutation
    assert _pos(db, pid)["status"] == "OPEN"
    assert pdb.list_adjustments(db, pid) == []


#############################################
# OPEN guard — every primitive refuses a non-OPEN position
#############################################

def test_primitives_refuse_non_open_position(tmp_path):
    db = _seed_account(tmp_path)
    pid = _seed_position(db)
    pdb.close_position(db, pid, exit_debit=0.2, exit_order_id=None,
                       realized_pnl=120.0, exit_reason="x", exit_ts="t",
                       status="CLOSED")
    pos = _pos(db, pid)
    cash0 = pdb.get_account(db)["cash"]
    cand_close = {"action": "close", "gross_cash": -60.0, "commission": 2.6,
                  "net_cash": -62.6, "est_fill_legs": []}
    cand_narrow = {"action": "narrow", "gross_cash": -100.0, "commission": 2.6,
                   "net_cash": -102.6, "new_width": 2.0, "new_max_loss": 600.0,
                   "est_fill_legs": [_leg("BUY", "PUT", 498.0)]}
    cand_ic = {"action": "convert_ic", "gross_cash": 80.0, "commission": 2.6,
               "net_cash": 77.4, "new_max_loss": 720.0,
               "est_fill_legs": [_leg("SELL", "CALL", 510.0)]}
    cand_roll = {"action": "roll_down", "gross_cash": 40.0, "commission": 5.2,
                 "net_cash": 34.8, "new_max_loss": 760.0, "new_expiry": "2026-07-31",
                 "est_fill_legs": [_leg("SELL", "PUT", 495.0), _leg("BUY", "PUT", 490.0)]}

    for fn, cand in [(pa.apply_close, cand_close),
                     (pa.apply_partial_close, cand_close),
                     (pa.apply_narrow, cand_narrow),
                     (pa.apply_convert_ic, cand_ic),
                     (pa.apply_convert_butterfly, cand_ic),
                     (pa.apply_roll, cand_roll)]:
        res = fn(db, pos, cand)
        assert res["ok"] is False, fn.__name__
        assert "not open" in res["error"].lower(), fn.__name__

    # nothing mutated
    assert pdb.get_account(db)["cash"] == cash0
    assert pdb.list_adjustments(db, pid) == []
