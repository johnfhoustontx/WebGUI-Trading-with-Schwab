import paper_account_db as pdb


def _db(tmp_path):
    return str(tmp_path / "acct.db")


def test_has_traded_signal(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    assert pdb.has_traded_signal(db, "sigX") is False
    pid = pdb.insert_position(db, {
        "signal_id": "sigX", "symbol": "QQQ", "strategy": "PCS", "short_strike": 1,
        "long_strike": 0, "width": 1, "expiration": "2026-06-05", "dte_at_entry": 1,
        "quantity": 1, "entry_credit": 0.5, "entry_order_id": 1, "max_loss_per": 50,
        "max_loss_total": 50, "entry_ts": "t"})
    assert pdb.has_traded_signal(db, "sigX") is True
    # still True after the position closes (prevents re-entry churn)
    pdb.close_position(db, pid, exit_debit=0.2, exit_order_id=2, realized_pnl=30,
                       exit_reason="X", exit_ts="t", status="CLOSED")
    assert pdb.has_traded_signal(db, "sigX") is True


def test_has_order_for_signal(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    assert pdb.has_order_for_signal(db, "sX") is False
    pdb.insert_order(db, {"signal_id": "sX", "ts": "t", "symbol": "MU",
        "side": "SELL_TO_OPEN", "strategy": "PCS", "legs": "[]", "quantity": 0,
        "order_type": "NET_CREDIT", "limit_price": None, "status": "REJECTED",
        "fill_price": None, "fill_ts": None, "reject_reason": "RISK_TOO_HIGH",
        "response_json": "{}"})
    # a rejected order still counts — so we don't retry the same signal
    assert pdb.has_order_for_signal(db, "sX") is True


def test_purge_rejected_orders(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pdb.insert_order(db, {"signal_id": "a", "ts": "t", "symbol": "MU",
        "side": "SELL_TO_OPEN", "strategy": "PCS", "legs": "[]", "quantity": 0,
        "order_type": "NET_CREDIT", "limit_price": None, "status": "REJECTED",
        "fill_price": None, "fill_ts": None, "reject_reason": "RISK_TOO_HIGH",
        "response_json": "{}"})
    pdb.insert_order(db, {"signal_id": "b", "ts": "t", "symbol": "QQQ",
        "side": "SELL_TO_OPEN", "strategy": "PCS", "legs": "[]", "quantity": 1,
        "order_type": "NET_CREDIT", "limit_price": 0.3, "status": "FILLED",
        "fill_price": 0.3, "fill_ts": "t", "reject_reason": None,
        "response_json": "{}"})
    n = pdb.purge_rejected_orders(db)
    assert n == 1
    remaining = pdb.fetch_orders(db)
    assert len(remaining) == 1 and remaining[0]["status"] == "FILLED"


def test_init_seeds_single_account_row(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, starting_balance=25_000.0, session_date="2026-06-03")
    acct = pdb.get_account(db)
    assert acct["cash"] == 25_000.0
    assert acct["buying_power_reserved"] == 0.0
    assert acct["realized_pnl"] == 0.0
    assert acct["session_date"] == "2026-06-03"
    assert acct["session_realized_pnl"] == 0.0
    assert acct["halted"] == 0


def test_ensure_account_is_idempotent(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pdb.ensure_account(db, 99_999.0, "2026-06-04")  # must NOT overwrite
    acct = pdb.get_account(db)
    assert acct["cash"] == 25_000.0
    assert acct["session_date"] == "2026-06-03"


def test_reserve_and_release_buying_power(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pdb.reserve_buying_power(db, 400.0)
    acct = pdb.get_account(db)
    assert acct["cash"] == 24_600.0
    assert acct["buying_power_reserved"] == 400.0
    pdb.release_buying_power(db, 400.0)
    acct = pdb.get_account(db)
    assert acct["cash"] == 25_000.0
    assert acct["buying_power_reserved"] == 0.0


def _open_pos(db, signal_id, max_loss_total):
    return pdb.insert_position(db, {
        "signal_id": signal_id, "symbol": "QQQ", "strategy": "PCS",
        "short_strike": 1, "long_strike": 0, "width": 1,
        "expiration": "2026-06-05", "dte_at_entry": 1, "quantity": 1,
        "entry_credit": 0.5, "entry_order_id": 1, "max_loss_per": max_loss_total,
        "max_loss_total": max_loss_total, "entry_ts": "t"})


def test_reconcile_fixes_orphaned_reserved_bp(tmp_path):
    """R6: a crash between reserve_buying_power and insert_position leaves BP
    reserved against no position. Reconcile recomputes reserved = Σ open
    max_loss_total, credits the orphaned amount back to cash (cash+reserved kept
    invariant), and returns the corrected drift."""
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    # One real open position worth 300 of reserved BP.
    _open_pos(db, "sigA", 300.0)
    pdb.reserve_buying_power(db, 300.0)
    # Simulate a crash AFTER a second reserve but BEFORE the insert: 200 orphaned.
    pdb.reserve_buying_power(db, 200.0)
    before = pdb.get_account(db)
    assert before["buying_power_reserved"] == 500.0
    total_before = before["cash"] + before["buying_power_reserved"]

    drift = pdb.reconcile_buying_power(db)

    after = pdb.get_account(db)
    assert drift == 200.0                                # over-reserved by 200
    assert after["buying_power_reserved"] == 300.0       # = Σ open max_loss_total
    assert after["cash"] == round(before["cash"] + 200.0, 2)  # orphaned BP credited back
    # Total committed capital is invariant across the fix.
    assert round(after["cash"] + after["buying_power_reserved"], 2) == round(total_before, 2)


def test_reconcile_is_noop_when_consistent(tmp_path):
    """When reserved already equals Σ open max_loss_total, reconcile changes
    nothing and returns 0.0 (idempotent)."""
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    _open_pos(db, "sigA", 300.0)
    pdb.reserve_buying_power(db, 300.0)
    before = pdb.get_account(db)

    drift = pdb.reconcile_buying_power(db)
    # Running it twice is still a no-op.
    drift2 = pdb.reconcile_buying_power(db)

    after = pdb.get_account(db)
    assert drift == 0.0 and drift2 == 0.0
    assert after["cash"] == before["cash"]
    assert after["buying_power_reserved"] == before["buying_power_reserved"]


def test_reconcile_no_account_is_safe(tmp_path):
    """Reconcile against a DB with no account row is a safe no-op (returns 0.0)."""
    db = _db(tmp_path)
    assert pdb.reconcile_buying_power(db) == 0.0


def test_reconcile_ignores_closed_positions(tmp_path):
    """Closed positions don't hold BP, so reconcile excludes them from the sum."""
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    open_pid = _open_pos(db, "sigOpen", 250.0)
    closed_pid = _open_pos(db, "sigClosed", 400.0)
    pdb.close_position(db, closed_pid, exit_debit=0.1, exit_order_id=9,
                       realized_pnl=10, exit_reason="X", exit_ts="t", status="CLOSED")
    # Reserved reflects BOTH opens (a crash left the closed one's BP reserved).
    pdb.reserve_buying_power(db, 650.0)

    drift = pdb.reconcile_buying_power(db)

    after = pdb.get_account(db)
    assert drift == 400.0  # the closed position's orphaned BP
    assert after["buying_power_reserved"] == 250.0  # only the open position


def test_realize_pnl_updates_lifetime_and_session(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pdb.realize_pnl(db, 120.0)
    pdb.realize_pnl(db, -50.0)
    acct = pdb.get_account(db)
    assert acct["realized_pnl"] == 70.0
    assert acct["session_realized_pnl"] == 70.0
    assert acct["cash"] == 25_070.0


def test_session_roll_resets_session_counters(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pdb.realize_pnl(db, -300.0)
    pdb.set_halted(db, True)
    rolled = pdb.roll_session_if_needed(db, "2026-06-04")
    assert rolled is True
    acct = pdb.get_account(db)
    assert acct["session_date"] == "2026-06-04"
    assert acct["session_realized_pnl"] == 0.0
    assert acct["halted"] == 0
    assert acct["realized_pnl"] == -300.0
    assert acct["session_start_equity"] == 24_700.0
    assert pdb.roll_session_if_needed(db, "2026-06-04") is False


def test_drawdown_halt_check(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pdb.realize_pnl(db, -2_400.0)
    assert pdb.should_halt(db, open_unrealized=-150.0,
                           max_drawdown=2_500.0) is True
    assert pdb.should_halt(db, open_unrealized=0.0,
                           max_drawdown=2_500.0) is False


def test_insert_and_fetch_order(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    oid = pdb.insert_order(db, {
        "signal_id": "abc123", "ts": "2026-06-03T09:41:00-05:00",
        "symbol": "SPY", "side": "SELL_TO_OPEN", "strategy": "PCS",
        "legs": "[]", "quantity": 5, "order_type": "NET_CREDIT",
        "limit_price": 0.50, "status": "FILLED", "fill_price": 0.48,
        "fill_ts": "2026-06-03T09:41:00-05:00", "reject_reason": None,
        "response_json": "{}",
    })
    assert isinstance(oid, int)
    orders = pdb.fetch_orders(db)
    assert len(orders) == 1 and orders[0]["fill_price"] == 0.48


def test_insert_open_close_position(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pid = pdb.insert_position(db, {
        "signal_id": "abc123", "symbol": "SPY", "strategy": "PCS",
        "short_strike": 500, "long_strike": 499, "width": 1.0,
        "expiration": "2026-06-03", "dte_at_entry": 0, "quantity": 5,
        "entry_credit": 0.48, "entry_order_id": 1, "max_loss_per": 52.0,
        "max_loss_total": 260.0, "entry_ts": "2026-06-03T09:41:00-05:00",
    })
    assert pdb.has_open_position(db, "abc123") is True
    assert len(pdb.fetch_open_positions(db)) == 1
    pdb.close_position(db, pid, exit_debit=0.20, exit_order_id=2,
                       realized_pnl=140.0, exit_reason="TARGET_HIT",
                       exit_ts="2026-06-03T10:00:00-05:00", status="CLOSED")
    assert pdb.has_open_position(db, "abc123") is False
    assert len(pdb.fetch_open_positions(db)) == 0


def test_update_position_mark(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pid = pdb.insert_position(db, {
        "signal_id": "x", "symbol": "SPY", "strategy": "PCS",
        "short_strike": 500, "long_strike": 499, "width": 1.0,
        "expiration": "2026-06-03", "dte_at_entry": 0, "quantity": 1,
        "entry_credit": 0.50, "entry_order_id": 1, "max_loss_per": 50.0,
        "max_loss_total": 50.0, "entry_ts": "t",
    })
    pdb.update_position_mark(db, pid, current_value=0.30, unrealized_pnl=20.0,
                             current_short_delta=-0.12, last_mark_ts="t2")
    pos = pdb.fetch_open_positions(db)[0]
    assert pos["unrealized_pnl"] == 20.0
    assert pdb.open_unrealized(db) == 20.0


def test_reset_account_restores_and_clears(tmp_path):
    db = _db(tmp_path)
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pdb.insert_position(db, {
        "signal_id": "s", "symbol": "SPY", "strategy": "PCS", "short_strike": 1,
        "long_strike": 0, "width": 1, "expiration": "2026-06-03", "dte_at_entry": 0,
        "quantity": 1, "entry_credit": 0.5, "entry_order_id": 1, "max_loss_per": 50,
        "max_loss_total": 50, "entry_ts": "t"})
    pdb.insert_order(db, {"signal_id": "s", "ts": "t", "symbol": "SPY",
        "side": "SELL_TO_OPEN", "strategy": "PCS", "legs": "[]", "quantity": 1,
        "order_type": "NET_CREDIT", "limit_price": 0.5, "status": "FILLED",
        "fill_price": 0.5, "fill_ts": "t", "reject_reason": None, "response_json": "{}"})
    pdb.reserve_buying_power(db, 50)
    pdb.reset_account(db, starting_balance=25_000.0, session_date="2026-06-04")
    acct = pdb.get_account(db)
    assert acct["cash"] == 25_000.0 and acct["buying_power_reserved"] == 0.0
    assert acct["realized_pnl"] == 0.0 and acct["session_date"] == "2026-06-04"
    assert acct["session_realized_pnl"] == 0.0 and acct["halted"] == 0
    assert pdb.fetch_all_positions(db) == [] and pdb.fetch_orders(db) == []
