"""Redis-driven end-to-end of the captured auto-manage lifecycle (Task 8).

Drives ``captured_manage`` through the REAL command path (``handlers.handle_command``
with a fakeredis ``Bus``) against a REAL temp ``signals.db``, proving a captured
signal ARMS break-even at +50% then AUTO-CLOSES at break-even on a retrace — writing
a ``signal_outcomes`` row (realized P&L) and surfacing in
``cache:options:captured_closed``. The user's book stays paper-only (an outcome
record, NEVER a broker order).

No live proxy / Redis / engine: the repricer is stubbed (controlled marks), the bus
is fakeredis, and ``signal_db`` is redirected to a tmp DB by forcing its low-level
``connect()`` to the temp path — the signal_db functions bind ``db_path=DEFAULT_DB_PATH``
at DEF time, so ``connect`` is the safe interception seam. ``signal_recommender`` +
``commission`` stay REAL, so the lifecycle (arm → break-even stop, round-trip-commission
be_level) is genuinely exercised end-to-end.
"""
from datetime import date, timedelta

from shared.bus import Bus
from shared.contracts.envelope import Command
# Importing the service package first runs its module-top sys.path glue
# (OPTIONS_SCANNER → sys.path), so ``signal_db`` / ``signal_repricer`` resolve when
# imported INSIDE the test (the same idiom as the driver-paper e2e).
from services.options_svc import handlers  # noqa: F401
from services.options_svc import compute  # noqa: F401


def _seed_open_signal(signal_db, db_path, expiration):
    signal_db.insert_signal({
        "signal_id": "CAP1", "scanner_type": "SWING", "symbol": "SPY",
        "strategy": "PCS", "short_strike": 490.0, "long_strike": 485.0,
        "call_short": None, "call_long": None, "width": 5.0,
        "expiration": expiration, "dte_at_entry": 30,
        "entry_credit": 1.0, "entry_max_loss": 400.0, "entry_score": 60,
        "entry_grade": "B", "entry_short_delta": -0.10, "entry_net_theta": 0.4,
        "entry_iv_rank": 30.0, "entry_underlying": 500.0,
        "first_seen_ts": "2026-08-09T09:00:00-05:00", "first_seen_date": "2026-08-09",
        "dedup_key": "SPY|PCS|490|485|cap1", "status": "OPEN",
    }, db_path=db_path)


def test_captured_manage_e2e_arm_then_breakeven_close(tmp_path, monkeypatch):
    import signal_db
    import signal_repricer

    db = tmp_path / "signals.db"
    # Redirect ALL signal_db access to the temp DB via the low-level connect().
    real_connect = signal_db.connect
    monkeypatch.setattr(signal_db, "connect", lambda db_path=None: real_connect(db))

    far = (date.today() + timedelta(days=30)).isoformat()
    _seed_open_signal(signal_db, db, far)

    # Stubbed reprice: +50% first (arms), then a retrace to <= be_level ($2.60 for
    # a 1-lot PCS) → break-even stop. Never touches a live chain.
    seq = iter([
        {"current_value": 0.40, "unrealized_pnl": 60.0, "pnl_pct_of_credit": 60.0,
         "current_underlying": 500.0, "current_short_delta": -0.08, "error": None},
        {"current_value": 0.99, "unrealized_pnl": 1.0, "pnl_pct_of_credit": 1.0,
         "current_underlying": 500.0, "current_short_delta": -0.10, "error": None},
    ])
    monkeypatch.setattr(signal_repricer, "reprice_swing", lambda r, c: next(seq))
    monkeypatch.setattr(signal_repricer, "clear_chain_cache", lambda: None)

    bus = Bus(fake=True)

    # ── Cycle 1: crosses +50% → ARMS break-even, does NOT close.
    handlers.handle_command(bus, Command(type="captured_manage"))
    sig = signal_db.get_signal("CAP1", db_path=db)
    assert sig["be_armed"] == 1 and sig["status"] == "OPEN"
    # Still on the open view; nothing closed yet.
    opened = bus.cache_get("cache:options:captured")
    assert any(s["signal_id"] == "CAP1" for s in opened.payload["signals"])
    closed_env = bus.cache_get("cache:options:captured_closed")
    assert closed_env.payload["closed"] == []

    # ── Cycle 2: retrace to <= break-even → BREAKEVEN_STOP auto-close.
    handlers.handle_command(bus, Command(type="captured_manage"))
    sig = signal_db.get_signal("CAP1", db_path=db)
    assert sig["status"] == "CLOSED"

    # A real signal_outcomes row was written (paper-only; realized ~ +$1 = net ≈ $0
    # after round-trip commissions).
    conn = signal_db.connect(db)
    try:
        row = conn.execute(
            "SELECT exit_reason, exit_value, realized_pnl FROM signal_outcomes "
            "WHERE signal_id='CAP1'").fetchone()
    finally:
        conn.close()
    assert row["exit_reason"] == "BREAKEVEN_STOP"
    assert abs(row["exit_value"] - 0.99) < 1e-6
    assert abs(row["realized_pnl"] - 1.0) < 1e-6

    # cache:options:captured_closed reflects it, with the day realized total.
    env = bus.cache_get("cache:options:captured_closed")
    closed = env.payload["closed"]
    assert any(c["signal_id"] == "CAP1" and c["exit_reason"] == "BREAKEVEN_STOP"
               for c in closed)
    assert abs(env.payload["total_realized"] - 1.0) < 1e-6
    # And the open view no longer lists it.
    opened = bus.cache_get("cache:options:captured")
    assert not any(s["signal_id"] == "CAP1" for s in opened.payload["signals"])
