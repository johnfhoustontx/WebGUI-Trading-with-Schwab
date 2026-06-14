"""Round-trip tests for trades_db.py and the SQLite-backed paper_trader."""
import json

import pytest

import trades_db
import paper_trader


def _seed_trade(tid="abc123", status="OPEN", strategy="PCS", symbol="SPY"):
    return {
        "trade_id": tid,
        "mode": "PAPER",
        "status": status,
        "symbol": symbol,
        "strategy": strategy,
        "trade_type": "0-DTE",
        "expiration": "2026-04-19",
        "dte_at_entry": 0,
        "short_strike": 500.0,
        "long_strike": 498.0,
        "width": 2.0,
        "quantity": 1,
        "entry_credit": 0.50,
        "entry_credit_total": 50.0,
        "max_loss_per": 1.50,
        "max_loss_total": 150.0,
        "breakeven": "499.50",
        "short_delta": -0.15,
        "net_theta": 0.08,
        "underlying_at_entry": 505.0,
        "entry_time": "2026-04-19T09:30:00-05:00",
        "exit_time": None,
        "exit_debit": None,
        "exit_debit_total": None,
        "realized_pnl": None,
        "exit_reason": None,
        "notes": "",
    }


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every persistence path at an isolated tmp DB file."""
    db = tmp_path / "trades.db"
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(trades_db, "LEGACY_TRADES_JSON", tmp_path / "paper_trades.json")
    monkeypatch.setattr(trades_db, "LEGACY_EVENTS_JSON", tmp_path / "trade_log.json")
    trades_db._initialised.clear()
    yield tmp_path
    trades_db._initialised.clear()


def test_insert_and_fetch_round_trip(sandbox):
    t = _seed_trade()
    conn = trades_db.connect()
    try:
        trades_db.insert_trade(conn, t)
        conn.commit()
        got = trades_db.fetch_one(conn, "abc123")
    finally:
        conn.close()

    for k in ("trade_id", "status", "symbol", "strategy", "entry_credit",
              "max_loss_total", "short_delta", "entry_time"):
        assert got[k] == t[k], f"mismatch on {k}"


def test_ic_trade_preserves_extra_strikes(sandbox):
    t = _seed_trade(strategy="IC")
    t["call_short"] = 510.0
    t["call_long"] = 512.0
    conn = trades_db.connect()
    try:
        trades_db.insert_trade(conn, t)
        conn.commit()
        got = trades_db.fetch_one(conn, "abc123")
    finally:
        conn.close()
    assert got["call_short"] == 510.0
    assert got["call_long"] == 512.0


def test_update_trade_flips_status(sandbox):
    t = _seed_trade()
    conn = trades_db.connect()
    try:
        trades_db.insert_trade(conn, t)
        t["status"] = "CLOSED"
        t["realized_pnl"] = 30.0
        trades_db.update_trade(conn, "abc123", t)
        conn.commit()
        got = trades_db.fetch_one(conn, "abc123")
    finally:
        conn.close()
    assert got["status"] == "CLOSED"
    assert got["realized_pnl"] == 30.0


def test_fetch_by_status_filters(sandbox):
    conn = trades_db.connect()
    try:
        trades_db.insert_trade(conn, _seed_trade("t1", status="OPEN"))
        trades_db.insert_trade(conn, _seed_trade("t2", status="CLOSED"))
        trades_db.insert_trade(conn, _seed_trade("t3", status="OPEN"))
        conn.commit()
        open_ids = sorted(t["trade_id"] for t in trades_db.fetch_by_status(conn, "OPEN"))
        closed_ids = sorted(t["trade_id"] for t in trades_db.fetch_by_status(conn, "CLOSED"))
    finally:
        conn.close()
    assert open_ids == ["t1", "t3"]
    assert closed_ids == ["t2"]


def test_delete_by_status_removes_closed_only(sandbox):
    conn = trades_db.connect()
    try:
        trades_db.insert_trade(conn, _seed_trade("t1", status="OPEN"))
        trades_db.insert_trade(conn, _seed_trade("t2", status="CLOSED"))
        trades_db.insert_trade(conn, _seed_trade("t3", status="EXPIRED"))
        conn.commit()
        removed = trades_db.delete_trades_by_status(conn, ("CLOSED", "EXPIRED"))
        conn.commit()
        remaining = trades_db.fetch_all(conn)
    finally:
        conn.close()
    assert removed == 2
    assert [t["trade_id"] for t in remaining] == ["t1"]


def test_insert_event_round_trip(sandbox):
    conn = trades_db.connect()
    try:
        trades_db.insert_event(conn, {
            "timestamp": "2026-04-19T09:30:00-05:00",
            "event": "OPENED",
            "trade_id": "abc123",
            "symbol": "SPY",
            "strategy": "PCS",
            "short_strike": 500.0,
            "credit": 0.50,
            "pnl": None,
        })
        conn.commit()
        events = trades_db.fetch_events(conn)
    finally:
        conn.close()
    assert len(events) == 1
    assert events[0]["event"] == "OPENED"
    assert events[0]["trade_id"] == "abc123"


#############################################
# paper_trader.py backend swap
#############################################

@pytest.fixture
def paper_sandbox(tmp_path, monkeypatch):
    db = tmp_path / "trades.db"
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(trades_db, "LEGACY_TRADES_JSON", tmp_path / "paper_trades.json")
    monkeypatch.setattr(trades_db, "LEGACY_EVENTS_JSON", tmp_path / "trade_log.json")
    trades_db._initialised.clear()
    yield tmp_path
    trades_db._initialised.clear()


def test_paper_trader_add_then_load(paper_sandbox):
    t = _seed_trade()
    paper_trader.add_trade(t)
    trades = paper_trader.load_trades()
    assert len(trades) == 1
    assert trades[0]["trade_id"] == "abc123"
    assert trades[0]["status"] == "OPEN"


def test_paper_trader_update_trade(paper_sandbox):
    t = _seed_trade()
    paper_trader.add_trade(t)
    closed = paper_trader.close_paper_trade(t, exit_debit=0.20, reason="MANUAL")
    paper_trader.update_trade("abc123", closed)
    trades = paper_trader.load_trades()
    assert trades[0]["status"] == "CLOSED"
    assert trades[0]["exit_debit"] == 0.20
    assert trades[0]["realized_pnl"] == 30.0  # (0.50 - 0.20) * 1 * 100


def test_paper_trader_delete_trade(paper_sandbox):
    paper_trader.add_trade(_seed_trade("t1"))
    paper_trader.add_trade(_seed_trade("t2"))
    paper_trader.delete_trade("t1")
    trades = paper_trader.load_trades()
    assert [t["trade_id"] for t in trades] == ["t2"]


def test_paper_trader_delete_closed_only(paper_sandbox):
    paper_trader.add_trade(_seed_trade("open1", status="OPEN"))
    paper_trader.add_trade(_seed_trade("closed1", status="CLOSED"))
    paper_trader.add_trade(_seed_trade("expired1", status="EXPIRED"))
    n = paper_trader.delete_closed_trades()
    assert n == 2
    trades = paper_trader.load_trades()
    assert [t["trade_id"] for t in trades] == ["open1"]


def test_paper_trader_get_open_trades(paper_sandbox):
    paper_trader.add_trade(_seed_trade("open1", status="OPEN"))
    paper_trader.add_trade(_seed_trade("closed1", status="CLOSED"))
    open_trades = paper_trader.get_open_trades()
    assert [t["trade_id"] for t in open_trades] == ["open1"]


def test_paper_trader_summary(paper_sandbox):
    paper_trader.add_trade(_seed_trade("open1", status="OPEN"))
    closed_trade = _seed_trade("closed1", status="CLOSED")
    closed_trade["realized_pnl"] = 30.0
    paper_trader.add_trade(closed_trade)
    loser = _seed_trade("loser1", status="CLOSED")
    loser["realized_pnl"] = -15.0
    paper_trader.add_trade(loser)

    summary = paper_trader.get_trade_summary()
    assert summary["total_trades"] == 3
    assert summary["open_count"] == 1
    assert summary["closed_count"] == 2
    assert summary["win_count"] == 1
    assert summary["loss_count"] == 1
    assert summary["win_rate"] == 50.0
    assert summary["total_pnl"] == 15.0


def test_legacy_json_migration_on_first_init(tmp_path, monkeypatch):
    """paper_trades.json alongside an uninitialized trades.db should be
    imported on first connect."""
    trades_db._initialised.clear()

    legacy = [
        _seed_trade("j1", status="OPEN"),
        _seed_trade("j2", status="CLOSED"),
    ]
    (tmp_path / "paper_trades.json").write_text(json.dumps(legacy))
    (tmp_path / "trade_log.json").write_text(json.dumps([
        {"timestamp": "2026-04-19T09:30:00", "event": "OPENED", "trade_id": "j1",
         "symbol": "SPY", "strategy": "PCS", "short_strike": 500.0,
         "credit": 0.50, "pnl": None},
    ]))

    db = tmp_path / "trades.db"
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(trades_db, "LEGACY_TRADES_JSON", tmp_path / "paper_trades.json")
    monkeypatch.setattr(trades_db, "LEGACY_EVENTS_JSON", tmp_path / "trade_log.json")

    # First connect triggers the migration.
    conn = trades_db.connect()
    try:
        migrated = trades_db.fetch_all(conn)
        events = trades_db.fetch_events(conn)
    finally:
        conn.close()
    trades_db._initialised.clear()

    assert sorted(t["trade_id"] for t in migrated) == ["j1", "j2"]
    assert len(events) == 1
    assert events[0]["trade_id"] == "j1"


def test_legacy_migration_skipped_when_db_has_rows(tmp_path, monkeypatch):
    trades_db._initialised.clear()
    db = tmp_path / "trades.db"
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(trades_db, "LEGACY_TRADES_JSON", tmp_path / "paper_trades.json")
    monkeypatch.setattr(trades_db, "LEGACY_EVENTS_JSON", tmp_path / "trade_log.json")

    # Seed the DB first.
    conn = trades_db.connect()
    try:
        trades_db.insert_trade(conn, _seed_trade("existing1"))
        conn.commit()
    finally:
        conn.close()

    # Then drop legacy JSON — migration should NOT run on the next init.
    (tmp_path / "paper_trades.json").write_text(json.dumps([_seed_trade("j1")]))
    trades_db._initialised.clear()

    conn = trades_db.connect()
    try:
        rows = trades_db.fetch_all(conn)
    finally:
        conn.close()
    trades_db._initialised.clear()

    assert sorted(t["trade_id"] for t in rows) == ["existing1"]


def test_schema_has_entry_greek_columns(sandbox):
    import sqlite3
    trades_db.init_db()
    conn = sqlite3.connect(trades_db.DEFAULT_DB_PATH)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    finally:
        conn.close()
    assert {"entry_delta", "entry_theta", "entry_vega", "entry_gamma"} <= cols


def test_create_paper_trade_captures_position_perspective_greeks():
    # Scanner-convention signal: net_theta = short - long (negative for short credit spread)
    signal = {
        "symbol": "SPY", "type": "PCS", "expiration": "2026-05-16",
        "dte": 23, "short_strike": 500, "long_strike": 498, "width": 2,
        "credit": 0.50, "max_loss": 1.50,
        "short_delta": -0.15,
        "net_theta": -0.03,   # scanner OLD convention (short_theta - long_theta, both negative)
        "net_vega": 0.02,     # scanner OLD convention
        "underlying_price": 505, "breakeven": "499.50",
    }
    trade = paper_trader.create_paper_trade(signal, quantity=1)
    # Position-perspective: theta flipped positive, vega flipped negative
    assert trade["entry_theta"] == pytest.approx(0.03)
    assert trade["entry_vega"] == pytest.approx(-0.02)
    # Not captured
    assert trade["entry_delta"] is None
    assert trade["entry_gamma"] is None


def test_create_paper_trade_greeks_missing_from_signal():
    signal = {
        "symbol": "SPY", "type": "PCS", "expiration": "2026-05-16",
        "dte": 23, "short_strike": 500, "long_strike": 498, "width": 2,
        "credit": 0.50, "max_loss": 1.50,
        "underlying_price": 505, "breakeven": "499.50",
        # No net_theta, net_vega — should result in None fields
    }
    trade = paper_trader.create_paper_trade(signal, quantity=1)
    assert trade["entry_theta"] is None
    assert trade["entry_vega"] is None


def test_init_db_migrates_legacy_schema_missing_entry_greek_columns(sandbox):
    """Forward-migration path: a pre-existing DB with the old schema (no
    entry_delta/theta/vega/gamma columns) should be ALTERed on init and
    existing rows should survive."""
    import sqlite3
    db_path = trades_db.DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Manually create a legacy schema (same as current but without the 4 new columns)
    legacy_schema = """
    CREATE TABLE trades (
        trade_id TEXT PRIMARY KEY,
        mode TEXT,
        status TEXT NOT NULL,
        symbol TEXT NOT NULL,
        strategy TEXT NOT NULL,
        trade_type TEXT,
        expiration TEXT,
        dte_at_entry INTEGER,
        short_strike REAL,
        long_strike REAL,
        call_short REAL,
        call_long REAL,
        width REAL,
        quantity INTEGER,
        entry_credit REAL,
        entry_credit_total REAL,
        max_loss_per REAL,
        max_loss_total REAL,
        breakeven TEXT,
        short_delta REAL,
        net_theta REAL,
        underlying_at_entry REAL,
        entry_time TEXT NOT NULL,
        exit_time TEXT,
        exit_debit REAL,
        exit_debit_total REAL,
        realized_pnl REAL,
        exit_reason TEXT,
        notes TEXT DEFAULT ''
    );
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(legacy_schema)
        conn.execute(
            "INSERT INTO trades (trade_id, status, symbol, strategy, entry_time) "
            "VALUES ('legacy1', 'OPEN', 'SPY', 'PCS', '2026-04-19T09:30:00-05:00')"
        )
        conn.commit()
    finally:
        conn.close()

    # Force re-init so init_db() actually runs
    trades_db._initialised.clear()
    trades_db.init_db()

    # Verify new columns exist
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
        assert {"entry_delta", "entry_theta", "entry_vega", "entry_gamma"} <= cols

        # Verify legacy row survives with NULL in new columns
        row = conn.execute(
            "SELECT trade_id, entry_delta, entry_theta, entry_vega, entry_gamma "
            "FROM trades WHERE trade_id='legacy1'"
        ).fetchone()
        assert row is not None
        assert row[0] == 'legacy1'
        assert row[1] is None
        assert row[2] is None
        assert row[3] is None
        assert row[4] is None
    finally:
        conn.close()


def test_roundtrip_preserves_entry_greeks(sandbox):
    trade = _seed_trade()
    trade["entry_delta"] = 0.20
    trade["entry_theta"] = 0.05
    trade["entry_vega"] = -0.04
    trade["entry_gamma"] = -0.002
    conn = trades_db.connect()
    try:
        trades_db.insert_trade(conn, trade)
        conn.commit()
        loaded = trades_db.fetch_one(conn, "abc123")
    finally:
        conn.close()
    assert loaded["entry_delta"] == pytest.approx(0.20)
    assert loaded["entry_theta"] == pytest.approx(0.05)
    assert loaded["entry_vega"] == pytest.approx(-0.04)
    assert loaded["entry_gamma"] == pytest.approx(-0.002)
