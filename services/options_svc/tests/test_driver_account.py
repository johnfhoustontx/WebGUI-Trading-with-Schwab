"""Tests for the isolated driver paper account (compute.ensure_driver_account /
driver_account_view / driver_account_perf).

Every test monkeypatches ``compute.DRIVER_PAPER_DB`` to a ``tmp_path`` DB so the
real ``options-scanner/data/paper_account*.db`` files are NEVER touched. The
``paper_account_db.connect`` helper auto-creates the schema for a new path, so a
fresh tmp DB needs no fixture beyond ``ensure_driver_account``.
"""
from services.options_svc import compute


def test_ensure_and_view_driver_account(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account(starting_balance=25000.0)
    v = compute.driver_account_view()
    assert v["has_account"] is True
    assert v["snapshot"]["cash"] == 25000.0
    assert v["snapshot"]["session_pnl"] == 0.0
    assert v["positions"] == [] and v["snapshot"]["open_count"] == 0


def test_driver_account_view_includes_closed_positions(tmp_path, monkeypatch):
    """The view exposes CLOSED positions (with exit_ts + realized_pnl) so the EoD
    report can date-bucket the driver book like the manual ledger."""
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    # open one, then close it
    compute.open_driver_position(_driver_signal(), qty=1, broker=_fake_broker(1.50))
    import paper_account_db
    pos = paper_account_db.fetch_open_positions(db)[0]
    paper_account_db.update_position_mark(
        db, pos["position_id"], status="CLOSED", realized_pnl=42.0,
        exit_ts="2026-06-27T15:00:00")
    view = compute.driver_account_view()
    assert "closed_positions" in view
    closed = view["closed_positions"]
    assert len(closed) == 1 and closed[0]["status"] == "CLOSED"
    assert closed[0]["realized_pnl"] == 42.0
    # open list no longer contains it
    assert all(p["status"] == "OPEN" for p in view["positions"])


def test_driver_account_perf_reads_db(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    perf = compute.driver_account_perf()
    assert perf["total_trades"] == 0 and perf["win_rate"] == 0.0


# ── Task 2.1: open_driver_position ───────────────────────────────────────────


def _fake_broker(fill_price):
    """A deterministic FILLED broker — no proxy/network. Mirrors the Schwab-shaped
    response paper_broker.submit_order returns (status/price/enteredTime/filledQuantity)."""
    class B:
        PREFIX = "[PAPER-DRV]"

        def submit_order(self, order, client):
            return {"orderId": 1, "status": "FILLED", "price": fill_price,
                    "enteredTime": "2026-06-25T14:00:00",
                    "filledQuantity": order["quantity"]}
    return B()


def _driver_signal(**o):
    # width=2.0 / credit=1.50 → size_contracts(1.5, 2.0) = (5, 50.0): a positive
    # engine-sized qty, so the min(clamped, sized) reconciliation is exercisable
    # both ways (a $5 width sizes to 0 at the $250 risk cap — nothing would open).
    base = {"signal_id": "MU_PCS_x", "symbol": "MU", "strategy": "PCS",
            "short_strike": 105.0, "long_strike": 103.0, "call_short": None,
            "call_long": None, "width": 2.0, "expiration": "2026-07-10",
            "dte_at_entry": 15, "entry_credit": 1.50, "source": "driver"}
    base.update(o)
    return base


def test_open_driver_position_into_driver_db(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    # fill 1.50 on a $2 width → max_loss_per=(2-1.5)*100=$50; engine sizes to 5,
    # clamp qty=2 → min(2,5)=2 opens (the clamp ceiling wins here).
    res = compute.open_driver_position(_driver_signal(), qty=2, broker=_fake_broker(1.50))
    assert res["status"] == "opened" and res["qty"] == 2
    pv = compute.driver_account_view()
    assert pv["snapshot"]["open_count"] == 1
    pos = pv["positions"][0]
    assert pos["symbol"] == "MU" and pos["strategy"] == "PCS"
    assert pos["quantity"] == 2
    assert pos["entry_credit"] == 1.50 and pos["max_loss_total"] == 100.0
    # the order row links to the position (entry_order_id resolves correctly)
    assert pos["entry_order_id"]
    assert pv["orders"] and pv["orders"][0]["status"] == "FILLED"
    assert pv["orders"][0]["quantity"] == 2          # the re-sized qty, not the request


def test_open_driver_position_accepts_raw_scanner_signal(tmp_path, monkeypatch):
    """Regression: the driver feeds RAW scanner signals (keyed ``type``/``credit``/
    ``id`` — NOT ``strategy``/``entry_credit``/``signal_id``). open_driver_position
    must normalize them and open, instead of KeyError'ing on 'signal_id' and
    silently degrading to status=error (which left the driver account empty while the
    decision log claimed trades were 'executed')."""
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    scanner_sig = {"id": "SPY_CCS_x", "symbol": "SPY", "type": "PCS",
                   "short_strike": 105.0, "long_strike": 103.0, "width": 2.0,
                   "expiration": "2026-07-10", "dte": 15, "credit": 1.50,
                   "source": "driver"}
    res = compute.open_driver_position(scanner_sig, qty=2, broker=_fake_broker(1.50))
    assert res["status"] == "opened" and res["qty"] == 2
    pos = compute.driver_account_view()["positions"][0]
    assert pos["symbol"] == "SPY" and pos["strategy"] == "PCS"
    assert pos["entry_credit"] == 1.50


def test_open_driver_position_clamp_is_ceiling(tmp_path, monkeypatch):
    """The guardrail clamp is a CEILING: an absurdly large requested qty must NOT
    open more than the engine sizes off the fill (min(clamped, sized))."""
    import paper_sizing

    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    # Size against the DRIVER's per-trade cap (what open_driver_position uses), not the
    # manual account's default $250: (2-1.5)*100 = $50/contract; floor(1500/50) = 30.
    sized = paper_sizing.size_contracts(1.5, 2.0, max_risk=compute._DRIVER_MAX_RISK_PER_TRADE)[0]
    assert sized == 30
    res = compute.open_driver_position(_driver_signal(), qty=100, broker=_fake_broker(1.50))
    assert res["status"] == "opened"
    pos = compute.driver_account_view()["positions"][0]
    assert pos["quantity"] == sized           # sized DOWN from 100, never up
    assert res["qty"] == sized


def test_open_driver_position_rejects_unfilled(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()

    class B:
        PREFIX = "x"

        def submit_order(self, order, client):
            return {"status": "REJECTED", "price": 0}

    res = compute.open_driver_position(_driver_signal(), qty=1, broker=B())
    assert res["status"] != "opened"
    assert compute.driver_account_view()["snapshot"]["open_count"] == 0


def test_open_driver_position_rejects_low_credit(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    # fill 0.05 < config_paper.MIN_FILL_CREDIT (0.10) → LOW_CREDIT, nothing opens.
    res = compute.open_driver_position(_driver_signal(), qty=1, broker=_fake_broker(0.05))
    assert res["status"] == "rejected" and res["reason"] == "LOW_CREDIT"
    assert compute.driver_account_view()["snapshot"]["open_count"] == 0


def test_open_driver_position_rejects_degenerate_fill(tmp_path, monkeypatch):
    """A fill >= width has max_loss_per <= 0 (no risk to size against) → reject."""
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    res = compute.open_driver_position(_driver_signal(), qty=1, broker=_fake_broker(5.50))
    assert res["status"] == "rejected"
    assert compute.driver_account_view()["snapshot"]["open_count"] == 0


def test_open_driver_position_never_raises(tmp_path, monkeypatch):
    """A malformed signal degrades to {"status": "error"}, never raises."""
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    res = compute.open_driver_position({"symbol": "MU"}, qty=1, broker=_fake_broker(1.50))
    assert res["status"] == "error"


def _poke_account(db, **fields):
    """Test helper: set account columns (session_date / halted) directly."""
    import paper_account_db
    conn = paper_account_db.connect(db)
    try:
        with conn:
            cols = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE account SET {cols} WHERE id=1", tuple(fields.values()))
    finally:
        conn.close()


def test_open_driver_position_rolls_stale_prior_day_halt(tmp_path, monkeypatch):
    """A drawdown halt from a PRIOR session must not block the new day: the first
    open rolls the session (un-halts) and the trade goes through. Guards the
    manual 'Run now' before the 5-min manage tick self-heals it."""
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    _poke_account(db, halted=1, session_date="2020-01-01")   # stale prior-day halt
    res = compute.open_driver_position(_driver_signal(), qty=1, broker=_fake_broker(1.50))
    assert res["status"] == "opened"
    assert compute.driver_account_view()["snapshot"]["open_count"] == 1


def test_open_driver_position_respects_same_day_halt(tmp_path, monkeypatch):
    """A halt from TODAY's session is real (banked $500 / hit the loss cap) and
    must STILL reject — the roll only clears a STALE (prior-day) halt."""
    import datetime as dt

    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    _poke_account(db, halted=1, session_date=dt.date.today().isoformat())   # today's halt
    res = compute.open_driver_position(_driver_signal(), qty=1, broker=_fake_broker(1.50))
    assert res["status"] == "rejected" and res["reason"] == "halted"
    assert compute.driver_account_view()["snapshot"]["open_count"] == 0


def test_open_driver_position_wide_spx_opens_under_raised_cap(tmp_path, monkeypatch):
    """A wide $SPX-shaped CCS (~$705/contract) opens under the driver's raised
    per-trade cap ($1500) but is rejected RISK_TOO_HIGH under the old $250 cap. This
    is the fix that lets the driver actually TRADE $SPX/MU: at $250 the paper sizer
    returned 0 contracts, so those picks logged 'Executed' (the enqueue) yet never
    opened."""
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    spx = _driver_signal(signal_id="SPX_CCS_x", symbol="$SPX", strategy="CCS",
                         short_strike=6000.0, long_strike=6010.0, width=10.0,
                         entry_credit=2.95)   # (10-2.95)*100 = $705/contract
    # Old $250 cap → floor(250/705) = 0 contracts → rejected.
    monkeypatch.setattr(compute, "_DRIVER_MAX_RISK_PER_TRADE", 250.0)
    r0 = compute.open_driver_position(spx, qty=2, broker=_fake_broker(2.95))
    assert r0["status"] == "rejected" and r0["reason"] == "RISK_TOO_HIGH"
    assert compute.driver_account_view()["snapshot"]["open_count"] == 0
    # Raised $1500 cap → floor(1500/705) = 2 contracts → opens.
    monkeypatch.setattr(compute, "_DRIVER_MAX_RISK_PER_TRADE", 1500.0)
    r1 = compute.open_driver_position(spx, qty=2, broker=_fake_broker(2.95))
    assert r1["status"] == "opened" and r1["qty"] == 2
    assert r1["max_loss_total"] == 1410.0     # 705 * 2
    assert compute.driver_account_view()["snapshot"]["open_count"] == 1


# ── Task 3.1: run_driver_manage_cycle ────────────────────────────────────────


def test_run_driver_manage_cycle_targets_driver_db(tmp_path, monkeypatch):
    import paper_engine

    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()          # so has_driver_account() is True
    seen = {}
    monkeypatch.setattr(paper_engine, "run_manage_cycle",
                        lambda client, today, db_path=None: seen.update(db_path=db_path))
    compute.run_driver_manage_cycle()
    assert seen["db_path"] == compute.DRIVER_PAPER_DB


def test_run_driver_manage_cycle_noop_without_account(tmp_path, monkeypatch):
    import paper_engine

    db = tmp_path / "driver.db"                # never seeded → has_driver_account() False
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    called = {"n": 0}
    monkeypatch.setattr(paper_engine, "run_manage_cycle",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    compute.run_driver_manage_cycle()
    assert called["n"] == 0                    # gated off when no driver account
